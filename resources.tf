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
    postgresql_config = {
      max_connections          = "100"
    shared_buffers           = 2097152      # 2 GB → 2 097 152 KB
    work_mem               = 65536        # 64 MB → 65 536 KB (минимум)
    maintenance_work_mem    = 1048576      # 1 GB → 1 048 576 KB (минимум)
    effective_cache_size   = 6291456      # 6 GB → 6 291 456 KB



      # Добавьте другие параметры PostgreSQL по необходимости
    }
    version = var.postgresql_version
    # Другие настройки конфигурации кластера
  resources {
    resource_preset_id = var.db_resource_preset
    disk_size          = var.db_disk_size
    disk_type_id       = var.db_disk_type
  }

  }
}

resource "yandex_function" "data_processor" {
  name = var.function_name

  runtime       = "python311"
  entrypoint    = "index.handler"
  memory        = "128"
  execution_timeout = "60"
  user_hash          = filesha256("function.zip")

  content {
    zip_filename = "function.zip"
  }

  environment = {
    BUCKET_NAME                  = var.bucket_name
    DB_HOST                    = yandex_mdb_postgresql_cluster.db_cluster.host[0].fqdn  # Используем прямое значение
    DB_PORT                   = var.db_port
    DB_NAME                   = var.database_name
    DB_USER                   = var.db_user_name
    DB_PASSWORD               = var.db_user_password
    STORAGE_ENDPOINT           = var.storage_endpoint
    AVERAGING_INTERVAL_MINUTES = var.averaging_interval_minutes
    AWS_ACCESS_KEY_ID         = yandex_iam_service_account_static_access_key.function_keys.access_key
    AWS_SECRET_ACCESS_KEY     = yandex_iam_service_account_static_access_key.function_keys.secret_key
  }
}


resource "yandex_resourcemanager_folder_iam_member" "sa_storage_access" {
  folder_id = var.yc_folder_id
  role        = "admin"
  member      = "serviceAccount:${yandex_iam_service_account.function_sa.id}"
}

resource "yandex_iam_service_account_static_access_key" "function_keys" {
  service_account_id = yandex_iam_service_account.function_sa.id
}

resource "yandex_api_gateway" "api_gw" {
  name        = "data-ingestion-gateway"
  description = "API Gateway for data ingestion"
  
  spec = templatefile("${path.module}/api-spec.yaml.tpl", {
    function_id = yandex_function.data_processor.id
    service_account_id = yandex_iam_service_account.api_gateway_sa.id  # Передаем ID в шаблон
  })
  
  depends_on = [
    yandex_function.data_processor,
    yandex_resourcemanager_folder_iam_member.api_gateway_function_invoker
  ]
}

resource "yandex_vpc_network" "iot_network" {
  name = "iot-network"
}

resource "yandex_vpc_subnet" "db_subnet_a" {
  name           = "db-subnet-a"
  zone           = var.zone
  network_id     = yandex_vpc_network.iot_network.id
  v4_cidr_blocks = ["10.0.1.0/24"]
}

resource "yandex_storage_bucket" "data_bucket" {
  bucket = var.bucket_name
  acl    = "private"
  folder_id = var.yc_folder_id
}

resource "yandex_function_trigger" "timer_trigger" {
  name = "five-minute-averaging"

  timer {
    cron_expression = "*/5 * ? * *"
  }

  function {
    id = yandex_function.data_processor.id
    service_account_id = yandex_iam_service_account.function_sa.id
  }
}

resource "yandex_iam_service_account" "function_sa" {
  name        = "function-service-account"
  description = "Service account for Cloud Function"
}

resource "yandex_resourcemanager_folder_iam_member" "function_invoker" {
  folder_id = var.yc_folder_id
  role      = "functions.functionInvoker"
  member    = "serviceAccount:${yandex_iam_service_account.function_sa.id}"
}

# Сервисный аккаунт для API Gateway
resource "yandex_iam_service_account" "api_gateway_sa" {
  name        = "api-gateway-sa"
  description = "Service account for API Gateway"
}

# Роль для вызова функции
resource "yandex_resourcemanager_folder_iam_member" "api_gateway_function_invoker" {
  folder_id = var.yc_folder_id
  role      = "functions.functionInvoker"
  member    = "serviceAccount:${yandex_iam_service_account.api_gateway_sa.id}"
}


# Скачивание SSL-сертификата для безопасного подключения
resource "null_resource" "download_cert" {
  provisioner "local-exec" {
      command = "curl -s -o ${path.module}/CA.pem https://storage.yandexcloud.net/cloud-certs/CA.pem"
  }
  
  triggers = {
    always_run = timestamp()
  }
}

# Инициализация таблиц в базе данных
resource "null_resource" "init_db" {
  depends_on = [
    yandex_mdb_postgresql_cluster.db_cluster,
    null_resource.download_cert
  ]

provisioner "local-exec" {
  command = <<-EOT
    $ErrorActionPreference = "Continue"   # Не останавливаться сразу
    $PGHOST = "${yandex_mdb_postgresql_cluster.db_cluster.host[0].fqdn}"
    $PORT   = "${var.db_port}"
    $DBNAME = "${var.database_name}"
    $USER   = "${var.db_user_name}"
    $PASSWORD = '${var.db_user_password}'
    $CERT   = "${path.module}/CA.pem"
    $SQL_FILE = "${path.module}/functions/init.sql"

    Write-Host "Trying direct psql connection to debug..."
    $env:PGPASSWORD = $PASSWORD

    & "C:\Program Files\PostgreSQL\15\bin\psql.exe" "host=$PGHOST port=$PORT dbname=$DBNAME user=$USER sslmode=verify-full sslrootcert=$CERT" -c "SELECT 1;"

    # psql "host=$PGHOST port=$PORT dbname=$DBNAME user=$USER sslmode=verify-full sslrootcert=$CERT" -c "SELECT 1;"

    Write-Host "Exit code: $LASTEXITCODE"
    if ($LASTEXITCODE -ne 0) {
      Write-Error "Initial debug connection failed"
      exit 1
    }

    Write-Host "Database is ready, proceeding to initialization..."

    & "C:\Program Files\PostgreSQL\15\bin\psql.exe" "host=$PGHOST port=$PORT dbname=$DBNAME user=$USER sslmode=verify-full sslrootcert=$CERT" -f $SQL_FILE
    # psql "host=$PGHOST port=$PORT dbname=$DBNAME user=$USER sslmode=verify-full sslrootcert=$CERT" -f $SQL_FILE
    if ($LASTEXITCODE -ne 0) {
      Write-Error "Failed to initialize database"
      exit 1
    }
    Write-Host "Database initialization completed successfully!"
  EOT
  interpreter = ["powershell.exe", "-Command"]
}

  triggers = {
    file_hash  = filesha256("${path.module}/functions/init.sql")
    cluster_id = yandex_mdb_postgresql_cluster.db_cluster.id
    db_host    = yandex_mdb_postgresql_cluster.db_cluster.host[0].fqdn
  }
}

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
