import json
import boto3
import psycopg2
import psycopg2.extras
import os
from datetime import datetime, timezone, timedelta
from collections import defaultdict

def handler(event, context):
    """
    Основная функция обработки
    - API Gateway: сохраняет данные в S3 и raw_telemetry
    - Timer trigger: агрегирует данные из S3 и сохраняет в averaged_data
    - API endpoints: /api/data и /api/aggregated для дашборда
    """
    
    # Определяем тип вызова
    if 'httpMethod' in event:
        # Проверяем путь для API данных дашборда
        path = event.get('path', '')
        if path == '/api/data':
            return get_dashboard_data(event, context)
        elif path == '/api/aggregated':
            return get_aggregated_data(event, context)
        else:
            # Вызов через API Gateway для сохранения данных
            return process_api_request(event, context)
    else:
        # Вызов через триггер (агрегация)
        return process_aggregation(event, context)

def get_dashboard_data(event, context):
    """
    Возвращает данные для дашборда
    Endpoint: GET /api/data
    """
    print("Getting dashboard data...")
    
    # Получаем параметры запроса
    params = event.get('queryStringParameters', {}) or {}
    hours = int(params.get('hours', 6))
    device_filter = params.get('device', 'all')
    
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        # 1. Получаем общую статистику
        if device_filter == 'all':
            cursor.execute("""
                SELECT 
                    COUNT(*) as total,
                    COALESCE(AVG(value), 0) as average,
                    COUNT(DISTINCT device_id) as devices
                FROM raw_telemetry
                WHERE timestamp > NOW() - INTERVAL '%s hours'
            """, (hours,))
        else:
            cursor.execute("""
                SELECT 
                    COUNT(*) as total,
                    COALESCE(AVG(value), 0) as average,
                    COUNT(DISTINCT device_id) as devices
                FROM raw_telemetry
                WHERE timestamp > NOW() - INTERVAL '%s hours'
                AND device_id = %s
            """, (hours, device_filter))
        
        stats = cursor.fetchone()
        
        # 2. Получаем временной ряд для графика
        if device_filter == 'all':
            cursor.execute("""
                SELECT 
                    timestamp, 
                    value, 
                    device_id
                FROM raw_telemetry
                WHERE timestamp > NOW() - INTERVAL '%s hours'
                ORDER BY timestamp ASC
                LIMIT 1000
            """, (hours,))
        else:
            cursor.execute("""
                SELECT 
                    timestamp, 
                    value, 
                    device_id
                FROM raw_telemetry
                WHERE timestamp > NOW() - INTERVAL '%s hours'
                AND device_id = %s
                ORDER BY timestamp ASC
                LIMIT 1000
            """, (hours, device_filter))
        
        timeline = cursor.fetchall()
        
        # 3. Получаем значения для гистограммы
        cursor.execute("""
            SELECT value
            FROM raw_telemetry
            WHERE timestamp > NOW() - INTERVAL '24 hours'
            LIMIT 2000
        """)
        values = [row['value'] for row in cursor.fetchall()]
        
        # 4. Получаем статистику по устройствам
        cursor.execute("""
            SELECT 
                device_id,
                COUNT(*) as count,
                AVG(value) as avg_value,
                MIN(value) as min_value,
                MAX(value) as max_value
            FROM raw_telemetry
            WHERE timestamp > NOW() - INTERVAL '24 hours'
            GROUP BY device_id
            ORDER BY device_id
        """)
        device_stats = cursor.fetchall()
        
        # 5. Получаем список всех устройств
        cursor.execute("SELECT DISTINCT device_id FROM raw_telemetry ORDER BY device_id")
        devices = [row['device_id'] for row in cursor.fetchall()]
        
        cursor.close()
        
        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
                'Access-Control-Allow-Headers': 'Content-Type'
            },
            'body': json.dumps({
                'stats': {
                    'total': stats['total'] if stats else 0,
                    'average': float(stats['average']) if stats else 0,
                    'devices': stats['devices'] if stats else 0
                },
                'timeline': [{'timestamp': row['timestamp'].isoformat(), 'value': row['value'], 'device_id': row['device_id']} for row in timeline],
                'values': values,
                'device_stats': {row['device_id']: {'count': row['count'], 'avg': float(row['avg_value']), 'min': float(row['min_value']), 'max': float(row['max_value'])} for row in device_stats},
                'devices': devices
            }, default=str)
        }
        
    except Exception as e:
        print(f"Error getting dashboard data: {e}")
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({'error': str(e)})
        }
    finally:
        if conn:
            conn.close()

def get_aggregated_data(event, context):
    """
    Возвращает агрегированные данные для дашборда
    Endpoint: GET /api/aggregated
    """
    print("Getting aggregated data...")
    
    # Получаем параметры запроса
    params = event.get('queryStringParameters', {}) or {}
    hours = int(params.get('hours', 24))
    
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        cursor.execute("""
            SELECT 
                interval_start,
                average_value,
                min_value,
                max_value,
                count,
                devices_count
            FROM averaged_data
            WHERE interval_start > NOW() - INTERVAL '%s hours'
            ORDER BY interval_start ASC
        """, (hours,))
        
        intervals = cursor.fetchall()
        cursor.close()
        
        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
                'Access-Control-Allow-Headers': 'Content-Type'
            },
            'body': json.dumps({
                'intervals': [{
                    'interval_start': row['interval_start'].isoformat(),
                    'average_value': float(row['average_value']),
                    'min_value': float(row['min_value']) if row['min_value'] else None,
                    'max_value': float(row['max_value']) if row['max_value'] else None,
                    'count': row['count'],
                    'devices_count': row['devices_count']
                } for row in intervals]
            }, default=str)
        }
        
    except Exception as e:
        print(f"Error getting aggregated data: {e}")
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({'error': str(e)})
        }
    finally:
        if conn:
            conn.close()

# ============================================
# ОСТАЛЬНЫЕ ФУНКЦИИ (БЕЗ ИЗМЕНЕНИЙ)
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

    timestamp = body.get('timestamp', datetime.now(timezone.utc).isoformat())
    value = body.get('value')
    device_id = body.get('device_id', 'unknown')

    if value is None:
        return {
            'statusCode': 400,
            'body': json.dumps({'error': 'Value is required'})
        }

    errors = []
    
    # 1. Сохраняем в S3 bucket
    try:
        save_to_bucket(timestamp, value, device_id)
        print(f"Saved to S3: device={device_id}, value={value}, time={timestamp}")
    except Exception as e:
        error_msg = f"Failed to save to S3: {str(e)}"
        print(error_msg)
        errors.append(error_msg)

    # 2. Сохраняем в таблицу raw_telemetry
    try:
        save_raw_to_database(timestamp, value, device_id)
        print(f"Saved to raw_telemetry: device={device_id}, value={value}, time={timestamp}")
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
                'data': {
                    'timestamp': timestamp,
                    'value': value,
                    'device_id': device_id
                }
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
                'data': {
                    'timestamp': timestamp,
                    'value': value,
                    'device_id': device_id
                }
            })
        }

def process_aggregation(event, context):
    """
    Агрегация данных (запускается по триггеру каждые 5 минут)
    Читает данные из S3 bucket, усредняет, сохраняет в averaged_data,
    затем удаляет обработанные файлы из S3
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
    
    # Агрегируем данные по 5-минутным интервалам
    aggregated_data = aggregate_data(raw_data)
    print(f"Aggregated into {len(aggregated_data)} intervals")
    
    # Сохраняем агрегированные данные в averaged_data
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
            'intervals_created': len(aggregated_data),
            'files_deleted': deleted_count
        })
    }

# ============================================
# ФУНКЦИИ ДЛЯ РАБОТЫ С S3 BUCKET
# ============================================

def save_to_bucket(timestamp, value, device_id):
    """Сохраняет данные в S3 bucket"""
    s3 = get_s3_client()
    bucket_name = os.environ['BUCKET_NAME']
    
    # Создаем уникальный ключ файла
    dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
    file_key = f"raw/{dt.year}/{dt.month:02d}/{dt.day:02d}/{dt.hour:02d}/{dt.minute:02d}_{dt.second:02d}_{device_id}_{int(dt.timestamp() * 1000)}.json"

    data = {
        'timestamp': timestamp,
        'value': value,
        'device_id': device_id,
        'received_at': datetime.now(timezone.utc).isoformat()
    }

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

def save_raw_to_database(timestamp, value, device_id):
    """Сохраняет сырые данные в таблицу raw_telemetry"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO raw_telemetry (timestamp, value, device_id, created_at) 
            VALUES (%s, %s, %s, %s)
        """, (timestamp, value, device_id, datetime.now(timezone.utc)))
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
        
        # Создаем таблицу raw_telemetry
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS raw_telemetry (
                id SERIAL PRIMARY KEY,
                timestamp TIMESTAMP NOT NULL,
                value DOUBLE PRECISION NOT NULL,
                device_id VARCHAR(100),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Создаем индексы для raw_telemetry
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_raw_timestamp ON raw_telemetry(timestamp);
            CREATE INDEX IF NOT EXISTS idx_raw_device_id ON raw_telemetry(device_id);
        """)
        
        # Создаем таблицу averaged_data
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS averaged_data (
                id SERIAL PRIMARY KEY,
                interval_start TIMESTAMP NOT NULL UNIQUE,
                average_value DOUBLE PRECISION NOT NULL,
                min_value DOUBLE PRECISION,
                max_value DOUBLE PRECISION,
                count INTEGER NOT NULL,
                devices_count INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Создаем индексы для averaged_data
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_avg_interval_start ON averaged_data(interval_start);
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

def aggregate_data(data_list):
    """Агрегирует данные по 5-минутным интервалам"""
    grouped = defaultdict(lambda: {'values': [], 'devices': set()})
    
    for item in data_list:
        timestamp = datetime.fromisoformat(item['timestamp'].replace('Z', '+00:00'))
        # Округляем до 5 минут
        interval_start = timestamp.replace(
            minute=(timestamp.minute // 5) * 5,
            second=0,
            microsecond=0
        )
        
        key = interval_start.isoformat()
        grouped[key]['values'].append(item['value'])
        grouped[key]['devices'].add(item.get('device_id', 'unknown'))
    
    # Вычисляем агрегированные значения
    aggregated = []
    for interval_start, data in grouped.items():
        values = data['values']
        avg_value = sum(values) / len(values)
        aggregated.append({
            'interval_start': interval_start,
            'average_value': round(avg_value, 2),
            'min_value': round(min(values), 2),
            'max_value': round(max(values), 2),
            'count': len(values),
            'devices_count': len(data['devices'])
        })
    
    return aggregated

def save_aggregated_to_database(aggregated_data):
    """Сохраняет агрегированные данные в таблицу averaged_data"""
    conn = get_db_connection()
    cursor = None
    
    try:
        cursor = conn.cursor()
        
        for data in aggregated_data:
            cursor.execute("""
                INSERT INTO averaged_data 
                (interval_start, average_value, min_value, max_value, count, devices_count)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (interval_start) DO UPDATE SET
                    average_value = EXCLUDED.average_value,
                    min_value = EXCLUDED.min_value,
                    max_value = EXCLUDED.max_value,
                    count = EXCLUDED.count,
                    devices_count = EXCLUDED.devices_count,
                    created_at = CURRENT_TIMESTAMP
            """, (
                data['interval_start'],
                data['average_value'],
                data['min_value'],
                data['max_value'],
                data['count'],
                data['devices_count']
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