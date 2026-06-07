# terraform.tfvars

yc_token       = "y0__wgBEL_MkYEHGMHdEyDk2p7NFzCq_caKCARmQKMUCzu8OAFJ5P8v9Y9E6Sbc"
yc_cloud_id  = "b1ghaldh06m42l069n7r"
yc_folder_id = "b1gm4i273dt3chocgk63"
bucket_name  = "my-iot-data"
function_name = "data-processor-func"
database_name = "telemetry_db"
postgresql_version    = "14"
db_resource_preset     = "s2.micro"
db_disk_size          = 20
db_disk_type          = "network-ssd"
db_environment        = "PRODUCTION"
db_user_name         = "app_dbuser"
db_user_password     = "password"
db_assign_public_ip  = true

