import json
import boto3
import psycopg2
import os
from datetime import datetime, timedelta
from collections import defaultdict

def handler(event, context):
    # Получаем данные из запроса
    try:
        body = json.loads(event['body'])
    except (KeyError, json.JSONDecodeError):
        return {
            'statusCode': 400,
            'body': json.dumps({'error': 'Invalid JSON in request body'})
        }

    timestamp = body.get('timestamp', datetime.utcnow().isoformat())
    value = body.get('value')

    if value is None:
        return {
            'statusCode': 400,
            'body': json.dumps({'error': 'Value is required'})
        }

    # Сохраняем сырые данные в бакет
    save_to_bucket(timestamp, value)

    # Обрабатываем данные и сохраняем усреднённые значения
    process_and_save_averages()

    return {
        'statusCode': 200,
        'body': json.dumps({
            'status': 'success',
            'timestamp': timestamp,
            'value': value
        })
    }


def save_to_bucket(timestamp, value):
    """Сохраняет сырые данные в Object Storage"""
    s3 = get_s3_client()
    bucket_name = os.environ['BUCKET_NAME']

    data = {'timestamp': timestamp, 'value': value}
    key = f"raw/{timestamp}.json"

    try:
        s3.put_object(
            Bucket=bucket_name,
            Key=key,
            Body=json.dumps(data)
        )
    except Exception as e:
        print(f"Error saving to bucket: {e}")
        raise

def get_s3_client():
    """Создаёт клиент S3 с настройками из переменных окружения"""
    endpoint_url = os.environ.get('STORAGE_ENDPOINT', 'https://storage.yandexcloud.net')
    aws_access_key_id = os.environ.get('AWS_ACCESS_KEY_ID')
    aws_secret_access_key = os.environ.get('AWS_SECRET_ACCESS_KEY')

    client_kwargs = {
        'endpoint_url': endpoint_url
    }

    if aws_access_key_id and aws_secret_access_key:
        client_kwargs.update({
            'aws_access_key_id': aws_access_key_id,
            'aws_secret_access_key': aws_secret_access_key
        })

    return boto3.client('s3', **client_kwargs)

def process_and_save_averages():
    """Обрабатывает сырые данные и сохраняет усреднённые значения"""
    minutes_interval = int(os.environ.get('AVERAGING_INTERVAL_MINUTES', 5))
    s3 = get_s3_client()
    bucket_name = os.environ['BUCKET_NAME']

    # Получаем сырые данные за последние N минут
    raw_data = get_recent_raw_data(s3, bucket_name, minutes=minutes_interval)

    if not raw_data:
        print("No recent data to process")
        return

    # Усредняем данные
    averaged_data = average_data(raw_data, minutes_interval)

    # Сохраняем в БД
    save_to_database(averaged_data)

def get_recent_raw_data(s3_client, bucket_name, minutes):
    """Получает сырые данные из бакета за последние N минут"""
    # Используем timezone-aware datetime
    from datetime import timezone
    cutoff_time = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    
    paginator = s3_client.get_paginator('list_objects_v2')
    page_iterator = paginator.paginate(Bucket=bucket_name, Prefix='raw/')

    data = []
    for page in page_iterator:
        if 'Contents' in page:
            for obj in page['Contents']:
                if obj['LastModified'] >= cutoff_time:
                    try:
                        response = s3_client.get_object(Bucket=bucket_name, Key=obj['Key'])
                        data.append(json.load(response['Body']))
                    except Exception as e:
                        print(f"Error reading object {obj['Key']}: {e}")
    return data

def average_data(data_list, interval_minutes):
    """Усредняет данные по временным интервалам"""
    grouped = defaultdict(list)

    for item in data_list:
        # Группируем по интервалам (например, каждые 5 минут)
        dt = datetime.fromisoformat(item['timestamp'])
        interval_start = dt.replace(
            minute=(dt.minute // interval_minutes) * interval_minutes,
            second=0,
            microsecond=0
        )
        grouped[interval_start].append(item['value'])

    averages = []
    for interval, values in grouped.items():
        avg_value = sum(values) / len(values)
        averages.append({
            'interval_start': interval.isoformat(),
            'average_value': avg_value,
            'count': len(values),
            'raw_values': values  # для отладки, можно убрать
        })
    return averages

def save_to_database(averages):
    """Сохраняет усреднённые данные в PostgreSQL"""
    conn = get_db_connection()

    try:
        cursor = conn.cursor()
        for avg in averages:
            cursor.execute(
                "INSERT INTO averaged_data (interval_start, average_value, count) VALUES (%s, %s, %s)",
                (avg['interval_start'], avg['average_value'], avg['count'])
            )
        conn.commit()
        cursor.close()
    except Exception as e:
        print(f"Database error: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()

def get_db_connection():
    """Создаёт соединение с PostgreSQL на основе переменных окружения"""
    return psycopg2.connect(
        host=os.environ['DB_HOST'],
        port=os.environ.get('DB_PORT', '6432'),
        database=os.environ['DB_NAME'],
        user=os.environ['DB_USER'],
        password=os.environ['DB_PASSWORD']
    )

