import json
import boto3
import psycopg2
import os
from datetime import datetime, timezone, timedelta
from collections import defaultdict

def handler(event, context):
    """
    Основная функция обработки
    - API Gateway: сохраняет данные в S3 и raw_telemetry
    - Timer trigger: агрегирует данные из S3 и сохраняет в averaged_metrics
    """

    # Определяем тип вызова
    if 'httpMethod' in event:
        # Вызов через API Gateway для сохранения данных
        return process_api_request(event, context)
    else:
        # Вызов через триггер (агрегация)
        return process_aggregation(event, context)

# ============================================
# ОСТАЛЬНЫЕ ФУНКЦИИ
# ============================================

def process_api_request(event, context):
    """
    Обработка API запроса
    Сохраняет данные одновременно в S3 bucket и в таблицу raw_telemetry
    """
    
    # Получаем данные из запроса
    try:
        body = json.loads(event['body'])
    except (KeyError, json.JSONDecodeError):
        return {
            'statusCode': 400,
            'body': json.dumps({'error': 'Invalid JSON in request body'})
        }

    # Извлекаем поля из нового формата
    device_id = body.get('device_id', 'unknown')
    firmware_version = body.get('firmware_version', 'unknown')
    status = body.get('status', 'unknown')
    timestamp = body.get('timestamp', datetime.now(timezone.utc).isoformat())
    metrics = body.get('metrics', {})
    
    # Проверяем наличие метрик
    if not metrics:
        return {
            'statusCode': 400,
            'body': json.dumps({'error': 'Metrics are required'})
        }
    
    temperature = metrics.get('temperature_c')
    humidity = metrics.get('humidity_percent')
    battery = metrics.get('battery_level_percent')
    
    if temperature is None and humidity is None and battery is None:
        return {
            'statusCode': 400,
            'body': json.dumps({'error': 'At least one metric is required'})
        }

    errors = []
    
    # 1. Сохраняем в S3 bucket
    try:
        save_to_bucket(body)
        print(f"Saved to S3: device={device_id}, metrics={metrics}, time={timestamp}")
    except Exception as e:
        error_msg = f"Failed to save to S3: {str(e)}"
        print(error_msg)
        errors.append(error_msg)

    # 2. Сохраняем в таблицу raw_telemetry
    try:
        save_raw_to_database(device_id, firmware_version, status, timestamp, metrics)
        print(f"Saved to raw_telemetry: device={device_id}, time={timestamp}")
    except Exception as e:
        error_msg = f"Failed to save to database: {str(e)}"
        print(error_msg)
        errors.append(error_msg)

    # Возвращаем результат
    if errors:
        return {
            'statusCode': 207,
            'body': json.dumps({
                'status': 'partial_success',
                'message': 'Data saved with errors',
                'errors': errors,
                'data': body
            })
        }
    else:
        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({
                'status': 'success',
                'message': 'Data saved to S3 and database',
                'data': body
            })
        }

def process_aggregation(event, context):
    """
    Агрегация данных (запускается по триггеру каждые 5 минут)
    Читает данные из S3 bucket, усредняет по device_id и метрикам,
    сохраняет в averaged_metrics, затем удаляет обработанные файлы из S3
    """
    print("=" * 50)
    print(f"Starting aggregation at {datetime.now(timezone.utc).isoformat()}")
    print("=" * 50)
    
    # Инициализируем таблицы в БД (если не существуют)
    init_database()
    
    # Получаем все непрочитанные файлы из S3 bucket
    s3_files = get_all_unprocessed_files_from_s3()
    
    if not s3_files:
        print("No unprocessed files found in S3 bucket")
        return {
            'statusCode': 200,
            'body': json.dumps({'message': 'No new data to aggregate'})
        }
    
    print(f"Found {len(s3_files)} unprocessed files in S3")
    
    # Читаем данные из файлов
    raw_data = []
    for file_info in s3_files:
        try:
            data = read_file_from_s3(file_info['key'])
            if data:
                raw_data.append(data)
                print(f"Read file: {file_info['key']}")
        except Exception as e:
            print(f"Error reading file {file_info['key']}: {str(e)}")
    
    if not raw_data:
        print("No valid data found in S3 files")
        return {
            'statusCode': 200,
            'body': json.dumps({'message': 'No valid data in files'})
        }
    
    print(f"Loaded {len(raw_data)} records from S3")
    
    # Агрегируем данные по device_id и 5-минутным интервалам
    aggregated_data = aggregate_data_by_device(raw_data)
    print(f"Aggregated into {len(aggregated_data)} records")
    
    # Сохраняем агрегированные данные в averaged_metrics
    try:
        save_aggregated_to_database(aggregated_data)
        print(f"Successfully saved {len(aggregated_data)} aggregated records to database")
    except Exception as e:
        print(f"Error saving aggregated data: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': f'Failed to save aggregated data: {str(e)}'})
        }
    
    # Удаляем обработанные файлы из S3
    deleted_count = 0
    for file_info in s3_files:
        try:
            delete_file_from_s3(file_info['key'])
            deleted_count += 1
            print(f"Deleted file: {file_info['key']}")
        except Exception as e:
            print(f"Error deleting file {file_info['key']}: {str(e)}")
    
    print(f"Deleted {deleted_count} files from S3")
    print("=" * 50)
    print("Aggregation completed successfully")
    print("=" * 50)
    
    return {
        'statusCode': 200,
        'body': json.dumps({
            'message': 'Aggregation completed',
            'files_processed': len(s3_files),
            'records_processed': len(raw_data),
            'aggregated_records': len(aggregated_data),
            'files_deleted': deleted_count
        })
    }

# ============================================
# ФУНКЦИИ ДЛЯ РАБОТЫ С S3 BUCKET
# ============================================

def save_to_bucket(data):
    """Сохраняет данные в S3 bucket"""
    s3 = get_s3_client()
    bucket_name = os.environ['BUCKET_NAME']
    
    # Создаем уникальный ключ файла
    device_id = data.get('device_id', 'unknown')
    timestamp = data.get('timestamp', datetime.now(timezone.utc).isoformat())
    dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
    
    file_key = f"raw/{dt.year}/{dt.month:02d}/{dt.day:02d}/{dt.hour:02d}/{dt.minute:02d}_{dt.second:02d}_{device_id}_{int(dt.timestamp() * 1000)}.json"

    # Добавляем время получения
    data['received_at'] = datetime.now(timezone.utc).isoformat()

    s3.put_object(
        Bucket=bucket_name,
        Key=file_key,
        Body=json.dumps(data),
        ContentType='application/json'
    )
    return file_key

def get_all_unprocessed_files_from_s3():
    """Получает список всех файлов из S3 bucket"""
    s3 = get_s3_client()
    bucket_name = os.environ['BUCKET_NAME']
    
    all_files = []
    continuation_token = None
    
    try:
        while True:
            list_kwargs = {'Bucket': bucket_name, 'Prefix': 'raw/'}
            if continuation_token:
                list_kwargs['ContinuationToken'] = continuation_token
                
            response = s3.list_objects_v2(**list_kwargs)
            
            if 'Contents' in response:
                for obj in response['Contents']:
                    all_files.append({
                        'key': obj['Key'],
                        'last_modified': obj['LastModified'],
                        'size': obj['Size']
                    })
            
            if response.get('IsTruncated'):
                continuation_token = response.get('NextContinuationToken')
            else:
                break
                
    except Exception as e:
        print(f"Error listing S3 files: {str(e)}")
    
    return all_files

def read_file_from_s3(file_key):
    """Читает данные из файла в S3"""
    s3 = get_s3_client()
    bucket_name = os.environ['BUCKET_NAME']
    
    try:
        response = s3.get_object(Bucket=bucket_name, Key=file_key)
        content = json.loads(response['Body'].read())
        return content
    except Exception as e:
        print(f"Error reading {file_key}: {str(e)}")
        return None

def delete_file_from_s3(file_key):
    """Удаляет файл из S3 bucket"""
    s3 = get_s3_client()
    bucket_name = os.environ['BUCKET_NAME']
    
    s3.delete_object(Bucket=bucket_name, Key=file_key)

def get_s3_client():
    """Создает клиент S3"""
    endpoint_url = os.environ.get('STORAGE_ENDPOINT', 'https://storage.yandexcloud.net')
    aws_access_key_id = os.environ.get('AWS_ACCESS_KEY_ID')
    aws_secret_access_key = os.environ.get('AWS_SECRET_ACCESS_KEY')

    return boto3.client('s3',
        endpoint_url=endpoint_url,
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key
    )

# ============================================
# ФУНКЦИИ ДЛЯ РАБОТЫ С БАЗОЙ ДАННЫХ
# ============================================

def save_raw_to_database(device_id, firmware_version, status, timestamp, metrics):
    """Сохраняет сырые данные в таблицу raw_telemetry"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO raw_telemetry 
            (device_id, firmware_version, status, timestamp, 
             temperature_c, humidity_percent, battery_level_percent, created_at) 
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            device_id, 
            firmware_version, 
            status, 
            timestamp,
            metrics.get('temperature_c'),
            metrics.get('humidity_percent'),
            metrics.get('battery_level_percent'),
            datetime.now(timezone.utc)
        ))
        conn.commit()
        cursor.close()
    except Exception as e:
        print(f"Error saving to raw_telemetry: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()

def init_database():
    """Инициализирует таблицы в БД"""
    conn = get_db_connection()
    cursor = None
    
    try:
        cursor = conn.cursor()
        
        # Создаем таблицу raw_telemetry с новыми полями
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS raw_telemetry (
                id SERIAL PRIMARY KEY,
                device_id VARCHAR(100) NOT NULL,
                firmware_version VARCHAR(50),
                status VARCHAR(50),
                timestamp TIMESTAMP NOT NULL,
                temperature_c DOUBLE PRECISION,
                humidity_percent DOUBLE PRECISION,
                battery_level_percent DOUBLE PRECISION,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Создаем индексы для raw_telemetry
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_raw_timestamp ON raw_telemetry(timestamp);
            CREATE INDEX IF NOT EXISTS idx_raw_device_id ON raw_telemetry(device_id);
            CREATE INDEX IF NOT EXISTS idx_raw_device_timestamp ON raw_telemetry(device_id, timestamp);
        """)
        
        # Создаем таблицу averaged_metrics для усредненных данных
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS averaged_metrics (
                id SERIAL PRIMARY KEY,
                device_id VARCHAR(100) NOT NULL,
                interval_start TIMESTAMP NOT NULL,
                interval_end TIMESTAMP NOT NULL,
                avg_temperature_c DOUBLE PRECISION,
                min_temperature_c DOUBLE PRECISION,
                max_temperature_c DOUBLE PRECISION,
                avg_humidity_percent DOUBLE PRECISION,
                min_humidity_percent DOUBLE PRECISION,
                max_humidity_percent DOUBLE PRECISION,
                avg_battery_level_percent DOUBLE PRECISION,
                min_battery_level_percent DOUBLE PRECISION,
                max_battery_level_percent DOUBLE PRECISION,
                sample_count INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(device_id, interval_start)
            )
        """)
        
        # Создаем индексы для averaged_metrics
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_avg_device_id ON averaged_metrics(device_id);
            CREATE INDEX IF NOT EXISTS idx_avg_interval_start ON averaged_metrics(interval_start);
            CREATE INDEX IF NOT EXISTS idx_avg_device_interval ON averaged_metrics(device_id, interval_start);
        """)
        
        conn.commit()
        print("Database tables initialized successfully")
        
    except Exception as e:
        print(f"Error initializing database: {e}")
        if conn:
            conn.rollback()
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

def aggregate_data_by_device(data_list):
    """
    Агрегирует данные по device_id и 5-минутным интервалам
    Для каждого устройства и интервала вычисляет средние значения метрик
    """
    # Структура: {device_id: {interval_start: {metrics}}}
    grouped = defaultdict(lambda: defaultdict(lambda: {
        'temperatures': [],
        'humidities': [],
        'batteries': [],
        'statuses': []
    }))
    
    for item in data_list:
        device_id = item.get('device_id', 'unknown')
        timestamp = datetime.fromisoformat(item['timestamp'].replace('Z', '+00:00'))
        
        # Округляем до 5 минут
        interval_start = timestamp.replace(
            minute=(timestamp.minute // 5) * 5,
            second=0,
            microsecond=0
        )
        interval_end = interval_start + timedelta(minutes=5)
        
        metrics = item.get('metrics', {})
        
        # Собираем метрики если они есть
        if 'temperature_c' in metrics and metrics['temperature_c'] is not None:
            grouped[device_id][interval_start]['temperatures'].append(metrics['temperature_c'])
        
        if 'humidity_percent' in metrics and metrics['humidity_percent'] is not None:
            grouped[device_id][interval_start]['humidities'].append(metrics['humidity_percent'])
        
        if 'battery_level_percent' in metrics and metrics['battery_level_percent'] is not None:
            grouped[device_id][interval_start]['batteries'].append(metrics['battery_level_percent'])
        
        grouped[device_id][interval_start]['statuses'].append(item.get('status', 'unknown'))
    
    # Вычисляем агрегированные значения
    aggregated = []
    
    for device_id, intervals in grouped.items():
        for interval_start, data in intervals.items():
            interval_end = interval_start + timedelta(minutes=5)
            
            record = {
                'device_id': device_id,
                'interval_start': interval_start.isoformat(),
                'interval_end': interval_end.isoformat(),
                'sample_count': len(data['statuses'])
            }
            
            # Агрегируем температуру
            if data['temperatures']:
                record['avg_temperature_c'] = round(sum(data['temperatures']) / len(data['temperatures']), 2)
                record['min_temperature_c'] = round(min(data['temperatures']), 2)
                record['max_temperature_c'] = round(max(data['temperatures']), 2)
            
            # Агрегируем влажность
            if data['humidities']:
                record['avg_humidity_percent'] = round(sum(data['humidities']) / len(data['humidities']), 2)
                record['min_humidity_percent'] = round(min(data['humidities']), 2)
                record['max_humidity_percent'] = round(max(data['humidities']), 2)
            
            # Агрегируем заряд батареи
            if data['batteries']:
                record['avg_battery_level_percent'] = round(sum(data['batteries']) / len(data['batteries']), 2)
                record['min_battery_level_percent'] = round(min(data['batteries']), 2)
                record['max_battery_level_percent'] = round(max(data['batteries']), 2)
            
            aggregated.append(record)
    
    return aggregated

def save_aggregated_to_database(aggregated_data):
    """Сохраняет агрегированные данные в таблицу averaged_metrics"""
    conn = get_db_connection()
    cursor = None
    
    try:
        cursor = conn.cursor()
        
        for data in aggregated_data:
            cursor.execute("""
                INSERT INTO averaged_metrics 
                (device_id, interval_start, interval_end,
                 avg_temperature_c, min_temperature_c, max_temperature_c,
                 avg_humidity_percent, min_humidity_percent, max_humidity_percent,
                 avg_battery_level_percent, min_battery_level_percent, max_battery_level_percent,
                 sample_count)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (device_id, interval_start) DO UPDATE SET
                    interval_end = EXCLUDED.interval_end,
                    avg_temperature_c = EXCLUDED.avg_temperature_c,
                    min_temperature_c = EXCLUDED.min_temperature_c,
                    max_temperature_c = EXCLUDED.max_temperature_c,
                    avg_humidity_percent = EXCLUDED.avg_humidity_percent,
                    min_humidity_percent = EXCLUDED.min_humidity_percent,
                    max_humidity_percent = EXCLUDED.max_humidity_percent,
                    avg_battery_level_percent = EXCLUDED.avg_battery_level_percent,
                    min_battery_level_percent = EXCLUDED.min_battery_level_percent,
                    max_battery_level_percent = EXCLUDED.max_battery_level_percent,
                    sample_count = EXCLUDED.sample_count,
                    created_at = CURRENT_TIMESTAMP
            """, (
                data['device_id'],
                data['interval_start'],
                data['interval_end'],
                data.get('avg_temperature_c'),
                data.get('min_temperature_c'),
                data.get('max_temperature_c'),
                data.get('avg_humidity_percent'),
                data.get('min_humidity_percent'),
                data.get('max_humidity_percent'),
                data.get('avg_battery_level_percent'),
                data.get('min_battery_level_percent'),
                data.get('max_battery_level_percent'),
                data['sample_count']
            ))
        
        conn.commit()
        
    except Exception as e:
        print(f"Error saving aggregated data: {e}")
        if conn:
            conn.rollback()
        raise
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

def get_db_connection():
    """Создает соединение с PostgreSQL"""
    return psycopg2.connect(
        host=os.environ['DB_HOST'],
        port=os.environ.get('DB_PORT', '6432'),
        database=os.environ['DB_NAME'],
        user=os.environ['DB_USER'],
        password=os.environ['DB_PASSWORD'],
        sslmode='require'
    )