# resource.tf

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

resource "yandex_vpc_subnet" "vm_subnet_a" {
  name           = "vm-subnet-a"
  zone           = var.zone
  network_id     = yandex_vpc_network.iot_network.id
  v4_cidr_blocks = ["10.0.10.0/24"]
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

# Сервисный аккаунт для машины с MOSQUITTO (MQTT)
resource "yandex_iam_service_account" "vm_sa" {
  name        = "vm-sa"
  description = "Service account for MQTT VM"
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
# ГРУППА БЕЗОПАСНОСТИ ДЛЯ MQTT
# ============================================

resource "yandex_vpc_security_group" "mqtt_sg" {
  name       = "mqtt-security-group"
  network_id = yandex_vpc_network.iot_network.id

  # Входящий трафик на MQTT
  ingress {
    description    = "MQTT Standard Port"
    protocol       = "TCP"
    port           = 1883
    v4_cidr_blocks = ["0.0.0.0/0"] # Лучше ограничить IP клиентов в продакшене
  }

  # Входящий трафик на MQTT WebSocket
  ingress {
    description    = "MQTT WebSocket"
    protocol       = "TCP"
    port           = 9001
    v4_cidr_blocks = ["0.0.0.0/0"]
  }

  # SSH доступ (рекомендуется только с вашего IP)
  ingress {
    description    = "SSH"
    protocol       = "TCP"
    port           = 22
    v4_cidr_blocks = ["0.0.0.0/0"] # Замените на ваш IP: "x.x.x.x/32"
  }

  # Исходящий трафик разрешен весь
  egress {
    description    = "Allow all outbound"
    protocol       = "ANY"
    v4_cidr_blocks = ["0.0.0.0/0"]
  }
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

# Создаем zip архив для MQTT эмулятора
data "archive_file" "mqtt_emulator_zip" {
  type        = "zip"
  source_dir  = "${path.module}/mqtt_emulator"
  output_path = "${path.module}/mqtt_emulator.zip"
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

# Функция эмулятора MQTT
resource "yandex_function" "mqtt_emulator" {
  name = "mqtt-emulator"

  runtime            = "python311"
  entrypoint         = "mqtt_emulator.handler"
  memory             = "128"
  execution_timeout  = "120"
  user_hash          = data.archive_file.mqtt_emulator_zip.output_base64sha256
  service_account_id = yandex_iam_service_account.function_sa.id

  content {
    zip_filename = data.archive_file.mqtt_emulator_zip.output_path
  }

  environment = {
    MQTT_HOST     = yandex_compute_instance.mqtt_broker.network_interface[0].nat_ip_address
    MQTT_PORT     = "1883"
    MQTT_USERNAME = var.mqtt_username
    MQTT_PASSWORD = var.mqtt_password
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

# Триггер для автоматической эмуляции
resource "yandex_function_trigger" "mqtt_emulation_trigger" {
  count = 1
  
  name = "mqtt-emulation-trigger"

  timer {
    cron_expression = "*/1 * * * ? *"  # Каждые 2 минуты
    payload = jsonencode({
      devices     = ["Device_1", "Device_2","Device_3","Device_4"]
      count       = 5
      interval    = 0.5
      value_range = [0, 100]
      pattern     = "random"
    })
  }

  function {
    id                 = yandex_function.mqtt_emulator.id
    service_account_id = yandex_iam_service_account.function_sa.id
  }
}

# ============================================
# ПОЛУЧЕНИЕ АКТУАЛЬНОГО ОБРАЗА UBUNTU
# ============================================

data "yandex_compute_image" "ubuntu_image" {
  family = "ubuntu-2204-lts"
}

# ============================================
# ВИРТУАЛЬНАЯ МАШИНА ДЛЯ MOSQUITTO (MQTT)
# ============================================

resource "yandex_compute_instance" "mqtt_broker" {
  name        = "mosquitto-broker"
  platform_id = "standard-v2"
  zone        = var.zone

  resources {
    cores  = 2
    memory = 2
  }

  boot_disk {
    initialize_params {
      image_id = data.yandex_compute_image.ubuntu_image.id
      size     = 20
      type     = "network-ssd"
    }
  }

  network_interface {
    subnet_id = yandex_vpc_subnet.vm_subnet_a.id
    nat       = true
    security_group_ids = [yandex_vpc_security_group.mqtt_sg.id]
  }

  metadata = {
    ssh-keys = "ubuntu:ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAbrycFWUGU/mp7/DNLHH/EIfhd8g66DZ+i7E0Q5y209 your_email@example.com"
    user-data = <<-EOF
      #cloud-config
      
      packages:
        - mosquitto
        - mosquitto-clients
        - curl
        - jq
      
      write_files:
        - path: /opt/mosquitto-forwarder/forward.sh
          permissions: '0755'
          content: |
            #!/bin/bash
            
            API_GATEWAY_URL="https://${yandex_api_gateway.api_gw.domain}/data"
            MQTT_HOST="localhost"
            MQTT_PORT=1883
            LOG_FILE="/var/log/mosquitto-forwarder.log"
            
            # Подписываемся на все топики и построчно обрабатываем
            mosquitto_sub -h "$MQTT_HOST" -p "$MQTT_PORT" -t "#" -v | while read -r topic payload; do
                # Пропускаем системные топики
                [[ "$topic" == \$SYS/* ]] && continue
                
                # Формируем JSON для отправки в API Gateway
                DATA=$(echo "$payload" | jq -c --arg topic "$topic" '{
                    timestamp: (.timestamp // (now | strftime("%Y-%m-%dT%H:%M:%SZ"))),
                    value: (.value // (. | tonumber? // .)),
                    device_id: (.device_id // $topic)
                }' 2>/dev/null || echo "$payload" | jq -c --arg topic "$topic" '{
                    timestamp: (now | strftime("%Y-%m-%dT%H:%M:%SZ")),
                    value: (. | tonumber? // .),
                    device_id: $topic
                }')
                
                # Отправляем в API Gateway
                RESPONSE=$(curl -s -X POST \
                    -H "Content-Type: application/json" \
                    -d "$DATA" \
                    -w "\n%%{http_code}" \
                    "$API_GATEWAY_URL/")
                
                HTTP_CODE=$(echo "$RESPONSE" | tail -n1)
                BODY=$(echo "$RESPONSE" | head -n-1)
                
                echo "$(date '+%Y-%m-%d %H:%M:%S') - Topic: $topic - HTTP $HTTP_CODE - $BODY" >> "$LOG_FILE"
            done

        - path: /etc/systemd/system/mqtt-forwarder.service
          permissions: '0644'
          content: |
            [Unit]
            Description=MQTT to API Gateway Forwarder
            After=mosquitto.service
            Requires=mosquitto.service
            
            [Service]
            Type=simple
            ExecStart=/opt/mosquitto-forwarder/forward.sh
            Restart=always
            RestartSec=5
            
            [Install]
            WantedBy=multi-user.target

        - path: /etc/mosquitto/conf.d/default.conf
          content: |
            listener 1883 0.0.0.0
            allow_anonymous true
            
            listener 9001
            protocol websockets
            
            log_type all
            log_timestamp true
          permissions: '0644'

      runcmd:
        # Создаем директории и файлы
        - mkdir -p /opt/mosquitto-forwarder
        - touch /var/log/mosquitto-forwarder.log
        - chmod 644 /var/log/mosquitto-forwarder.log
        
        # Перезапускаем Mosquitto
        - systemctl restart mosquitto
        - sleep 3
        
        # Включаем и запускаем форвардер
        - systemctl daemon-reload
        - systemctl enable mqtt-forwarder
        - systemctl start mqtt-forwarder
        
        - echo "Mosquitto MQTT Broker + Forwarder setup complete!"
    EOF
  }

  service_account_id = yandex_iam_service_account.vm_sa.id

  depends_on = [
    yandex_api_gateway.api_gw
  ]
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

output "mqtt_connection_info" {
  value = {
    host     = yandex_compute_instance.mqtt_broker.network_interface[0].nat_ip_address
    port     = 1883
    ws_port  = 9001
  }
}
# ============================================
# GRAFANA НА ВИРТУАЛЬНОЙ МАШИНЕ
# (Managed Service пока не завезли в провайдер, качаю сам)
# ============================================

# Группа безопасности для Grafana
resource "yandex_vpc_security_group" "grafana_sg" {
  name       = "grafana-security-group"
  network_id = yandex_vpc_network.iot_network.id

  ingress {
    description    = "Allow Grafana Web Interface"
    protocol       = "TCP"
    port           = 3000
    v4_cidr_blocks = ["0.0.0.0/0"] # ЗАМЕНИ НА СВОЙ IP потом!
  }

  # Порт 8080 для плагинов или прокси, если вдруг понадобится
  ingress {
    description    = "Grafana alternative port"
    protocol       = "TCP"
    port           = 8080
    v4_cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description    = "SSH"
    protocol       = "TCP"
    port           = 22
    v4_cidr_blocks = ["0.0.0.0/0"] # Тоже лучше ограничить до своего IP
  }

  egress {
    description    = "Allow all outbound traffic"
    protocol       = "ANY"
    v4_cidr_blocks = ["0.0.0.0/0"]
  }
}

# Сама виртуалка с Grafana
resource "yandex_compute_instance" "grafana_vm" {
  name        = "grafana-instance"
  platform_id = "standard-v2"
  zone        = var.zone

  resources {
    cores  = 2
    memory = 2 # 2 ГБ для Grafana — комфортный минимум
  }

  boot_disk {
    initialize_params {
      image_id = data.yandex_compute_image.ubuntu_image.id
      size     = 20
      type     = "network-ssd"
    }
  }

  network_interface {
    subnet_id          = yandex_vpc_subnet.vm_subnet_a.id
    nat                = true
    security_group_ids = [yandex_vpc_security_group.grafana_sg.id]
  }

  metadata = {
    ssh-keys = "ubuntu:ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAbrycFWUGU/mp7/DNLHH/EIfhd8g66DZ+i7E0Q5y209 your_email@example.com"
    user-data = <<-EOF
      #cloud-config
      
      packages:
        - wget
        - curl
        - jq

      write_files:
        - path: /opt/grafana-setup.sh
          permissions: '0755'
          content: |
            #!/bin/bash
            set -e
            
            echo "=== Grafana Post-Install Setup ==="
            
            # Wait for Grafana to be ready
            echo "Waiting for Grafana to be ready..."
            ATTEMPT=0
            MAX_ATTEMPTS=30
            while [ $ATTEMPT -lt $MAX_ATTEMPTS ]; do
              HTTP_STATUS=$(curl -s -o /dev/null -w '%%{http_code}' http://localhost:3000/api/health)
              if [ "$HTTP_STATUS" = "200" ]; then
                echo "Grafana is ready!"
                break
              fi
              ATTEMPT=$((ATTEMPT + 1))
              echo "Attempt $ATTEMPT/$MAX_ATTEMPTS: HTTP $HTTP_STATUS, waiting 10 seconds..."
              sleep 10
            done
            
            if [ $ATTEMPT -eq $MAX_ATTEMPTS ]; then
              echo "ERROR: Grafana did not start within timeout"
              exit 1
            fi
            
            # Change admin password
            echo "Changing default admin password..."
            curl -s -X PUT \
              -H 'Content-Type: application/json' \
              -d '{"oldPassword":"admin","newPassword":"${var.grafana_admin_password}"}' \
              http://admin:admin@localhost:3000/api/user/password
            
            sleep 3
            
            # Add PostgreSQL datasource
            echo "Adding PostgreSQL datasource..."
            curl -s -X POST \
              -H 'Content-Type: application/json' \
              -d '{"name":"PostgreSQL IoT","type":"postgres","url":"${yandex_mdb_postgresql_cluster.db_cluster.host[0].fqdn}:${var.db_port}","database":"${var.database_name}","user":"${var.db_user_name}","secureJsonData":{"password":"${var.db_user_password}"},"access":"proxy","jsonData":{"sslmode":"require","postgresVersion":1500,"timescaledb":false}}' \
              http://admin:${var.grafana_admin_password}@localhost:3000/api/datasources
            
            # List current datasources
            echo "Current datasources:"
            curl -s http://admin:${var.grafana_admin_password}@localhost:3000/api/datasources | jq -r '.[].name' || echo "Could not list datasources"
            
            echo "=== Setup completed ==="

      runcmd:
        # Install Grafana
        - wget -q https://dl.grafana.com/oss/release/grafana_10.4.2_amd64.deb -O /tmp/grafana.deb
        - dpkg -i /tmp/grafana.deb
        - apt-get install -f -y
        - rm /tmp/grafana.deb
        - systemctl daemon-reload
        - systemctl enable grafana-server
        - systemctl start grafana-server
        - echo "Grafana installed and running"
        # Wait for Grafana to fully start
        - sleep 20
        # Run setup script
        - /opt/grafana-setup.sh
        - echo "Grafana setup script completed"
    EOF
  }

  depends_on = [
    yandex_vpc_subnet.vm_subnet_a,
    yandex_mdb_postgresql_cluster.db_cluster
  ]
}

# ============================================
# ВЫХОДНЫЕ ДАННЫЕ GRAFANA
# ============================================

output "grafana_url" {
  value = "http://${yandex_compute_instance.grafana_vm.network_interface[0].nat_ip_address}:3000"
}

output "grafana_login" {
  value = "admin"
}

output "grafana_password" {
  value     = var.grafana_admin_password
  sensitive = true
}

output "grafana_datasource_info" {
  value = {
    url        = "http://${yandex_compute_instance.grafana_vm.network_interface[0].nat_ip_address}:3000"
    login      = "admin"
    datasource = "PostgreSQL IoT"
    database   = var.database_name
    host       = yandex_mdb_postgresql_cluster.db_cluster.host[0].fqdn
  }
}

output "grafana_note" {
  value = "Источник данных PostgreSQL 'PostgreSQL IoT' настраивается автоматически при создании VM. Можете сразу создавать дашборды!"
}