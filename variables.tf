# variables.tf

variable "yc_token" {}
variable "yc_cloud_id" {}
variable "yc_folder_id" {}
variable "bucket_name" {}
variable "function_name" {}
variable "database_name" {}

variable "storage_endpoint" {
  description = "Endpoint URL for Yandex Object Storage"
  type        = string
  default     = "https://storage.yandexcloud.net"
}
variable "db_host" {
  description = "PostgreSQL host address"
  type        = string
  default     = "yandex_mdb_postgresql_cluster.db_cluster.host[0].fqdn"
}

variable "db_port" {
  description = "PostgreSQL port"
  type        = string
  default     = "6432"
}

variable "averaging_interval_minutes" {
  description = "Interval for data averaging in minutes"
  type        = number
  default     = 5
}

variable "aws_access_key_id" {
  description = "AWS access key ID for Object Storage (optional)"
  type        = string
  default     = ""
}

variable "aws_secret_access_key" {
  description = "AWS secret access key for Object Storage (optional)"
  type        = string
  default     = ""
}
variable "postgresql_version" {
  description = "PostgreSQL version for the cluster"
  type        = string
  default     = "14"
}

variable "db_resource_preset" {
  description = "Resource preset for PostgreSQL cluster (e.g., s2.micro)"
  type        = string
  default     = "s2.micro"
}

variable "db_disk_size" {
  description = "Disk size for PostgreSQL cluster in GB"
  type        = number
  default     = 10
}

variable "db_disk_type" {
  description = "Disk type for PostgreSQL cluster (network-ssd, network-hdd, etc.)"
  type        = string
  default     = "network-ssd"
}

variable "db_environment" {
  description = "Environment type (PRODUCTION, PRESTABLE, etc.)"
  type        = string
  default     = "PRODUCTION"
}

variable "zone" {
  description = "Availability zone for database host"
  type        = string
  default     = "ru-central1-b"
}

variable "db_user_name" {
  description = "Database user name"
  type        = string
  default     = "dbuser"
}

variable "db_user_password" {
  description = "Database user password"
  type        = string
  sensitive   = true
}

variable "db_assign_public_ip" {
  description = "Whether to assign public IP to database host"
  type        = bool
  default     = true
}

variable "mqtt_username" {
  description = "MQTT broker username (optional)"
  type        = string
  default     = ""
  sensitive   = true
}

variable "mqtt_password" {
  description = "MQTT broker password (optional)"
  type        = string
  default     = ""
  sensitive   = true
}

# ============================================
# ПЕРЕМЕННЫЕ GRAFANA
# ============================================

variable "grafana_admin_password" {
  description = "New admin password for Grafana"
  type        = string
  default     = ""
  sensitive   = true
}
