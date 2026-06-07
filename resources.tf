# ============================================
# СЕТЕВЫЕ РЕСУРСЫ
# ============================================

resource "yandex_vpc_network" "iot_network" {
  name = "iot-network"
}

resource "yandex_vpc_subnet" "db_subnet_a" {
  name           = "db-subnet-a"
  zone           = var.zone
  network_id     = yandex_vpc_network.iot_network.id
  v4_cidr_blocks = ["10.0.1.0/24"]
}

# ============================================
# КЛАСТЕР POSTGRESQL
# ============================================

resource "yandex_mdb_postgresql_cluster" "db_cluster" {
  name            = "${var.database_name}-cluster"
  environment     = var.db_environment
  network_id      = yandex_vpc_network.iot_network.id

  database {
    name  = var.database_name
    owner = var.db_user_name
  }

  user {
    name     = var.db_user_name
    password = var.db_user_password
    permission {
      database_name = var.database_name
    }
  }

  host {
    zone       = var.zone
    subnet_id  = yandex_vpc_subnet.db_subnet_a.id
    assign_public_ip = var.db_assign_public_ip
  }

  config {
    version = var.postgresql_version
    
    postgresql_config = {
      max_connections          = "100"
      shared_buffers           = 2097152      # 2 GB → 2 097 152 KB
      work_mem                 = 65536        # 64 MB → 65 536 KB
      maintenance_work_mem     = 1048576      # 1 GB → 1 048 576 KB
      effective_cache_size     = 6291456      # 6 GB → 6 291 456 KB
    }
    
    resources {
      resource_preset_id = var.db_resource_preset
      disk_size          = var.db_disk_size
      disk_type_id       = var.db_disk_type
    }
  }
}

# ============================================
# ХРАНИЛИЩЕ S3
# ============================================

resource "yandex_storage_bucket" "data_bucket" {
  bucket     = var.bucket_name
  folder_id  = var.yc_folder_id
  
  # Используем grant вместо deprecated acl
  grant {
    id          = yandex_iam_service_account.function_sa.id
    type        = "CanonicalUser"
    permissions = ["FULL_CONTROL"]
  }
}

# ============================================
# СЕРВИСНЫЕ АККАУНТЫ
# ============================================

# Сервисный аккаунт для Cloud Function
resource "yandex_iam_service_account" "function_sa" {
  name        = "function-service-account"
  description = "Service account for Cloud Function"
}

# Сервисный аккаунт для API Gateway
resource "yandex_iam_service_account" "api_gateway_sa" {
  name        = "api-gateway-sa"
  description = "Service account for API Gateway"
}

# ============================================
# СТАТИЧЕСКИЕ КЛЮЧИ ДОСТУПА
# ============================================

resource "yandex_iam_service_account_static_access_key" "function_keys" {
  service_account_id = yandex_iam_service_account.function_sa.id
}

# ============================================
# НАЗНАЧЕНИЕ ПРАВ (IAM ROLES)
# ============================================

# Права для основной функции на доступ к S3
resource "yandex_resourcemanager_folder_iam_member" "sa_storage_access" {
  folder_id = var.yc_folder_id
  role      = "storage.editor"
  member    = "serviceAccount:${yandex_iam_service_account.function_sa.id}"
}

# Права для API Gateway на вызов функции
resource "yandex_resourcemanager_folder_iam_member" "api_gateway_function_invoker" {
  folder_id = var.yc_folder_id
  role      = "functions.functionInvoker"
  member    = "serviceAccount:${yandex_iam_service_account.api_gateway_sa.id}"
}

# Права для триггера на вызов функции
resource "yandex_resourcemanager_folder_iam_member" "function_invoker" {
  folder_id = var.yc_folder_id
  role      = "functions.functionInvoker"
  member    = "serviceAccount:${yandex_iam_service_account.function_sa.id}"
}

# Дополнительные права для функции на вызов самой себя
resource "yandex_resourcemanager_folder_iam_member" "function_self_invoke" {
  folder_id = var.yc_folder_id
  role      = "functions.functionInvoker"
  member    = "serviceAccount:${yandex_iam_service_account.function_sa.id}"
}

# ============================================
# СОЗДАНИЕ ZIP АРХИВОВ ДЛЯ ФУНКЦИЙ
# ============================================

# Создаем zip архив из папки functions (основная функция)
data "archive_file" "functions_zip" {
  type        = "zip"
  source_dir  = "${path.module}/functions"
  output_path = "${path.module}/functions.zip"
}

# ============================================
# ОСНОВНАЯ ФУНКЦИЯ ОБРАБОТКИ ДАННЫХ
# ============================================

resource "yandex_function" "data_processor" {
  name = var.function_name

  runtime            = "python311"
  entrypoint         = "index.handler"
  memory             = "128"
  execution_timeout  = "60"
  user_hash          = data.archive_file.functions_zip.output_base64sha256
  service_account_id = yandex_iam_service_account.function_sa.id

  content {
    zip_filename = data.archive_file.functions_zip.output_path
  }

  environment = {
    BUCKET_NAME                  = var.bucket_name
    DB_HOST                      = yandex_mdb_postgresql_cluster.db_cluster.host[0].fqdn
    DB_PORT                      = var.db_port
    DB_NAME                      = var.database_name
    DB_USER                      = var.db_user_name
    DB_PASSWORD                  = var.db_user_password
    STORAGE_ENDPOINT             = var.storage_endpoint
    AVERAGING_INTERVAL_MINUTES   = var.averaging_interval_minutes
    AWS_ACCESS_KEY_ID            = yandex_iam_service_account_static_access_key.function_keys.access_key
    AWS_SECRET_ACCESS_KEY        = yandex_iam_service_account_static_access_key.function_keys.secret_key
  }
}

# ============================================
# API GATEWAY
# ============================================

resource "yandex_api_gateway" "api_gw" {
  name        = "data-ingestion-gateway"
  description = "API Gateway for data ingestion"
  
  spec = templatefile("${path.module}/api-spec.yaml.tpl", {
    function_id        = yandex_function.data_processor.id
    service_account_id = yandex_iam_service_account.api_gateway_sa.id
  })
  
  depends_on = [
    yandex_function.data_processor,
    yandex_resourcemanager_folder_iam_member.api_gateway_function_invoker
  ]
}

# ============================================
# ТРИГГЕР ПО РАСПИСАНИЮ
# ============================================

resource "yandex_function_trigger" "timer_trigger" {
  name = "five-minute-averaging"

  timer {
    cron_expression = "*/5 * ? * *"
  }

  function {
    id                 = yandex_function.data_processor.id
    service_account_id = yandex_iam_service_account.function_sa.id
  }
}

# ============================================
# ВЫХОДНЫЕ ДАННЫЕ
# ============================================

output "db_host_fqdn" {
  value = yandex_mdb_postgresql_cluster.db_cluster.host[0].fqdn
}

output "function_id" {
  value = yandex_function.data_processor.id
}

output "api_gateway_domain" {
  value = yandex_api_gateway.api_gw.domain
}

output "bucket_name" {
  value = yandex_storage_bucket.data_bucket.bucket
}