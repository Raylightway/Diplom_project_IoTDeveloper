# mqtt_emulator.py
import json
import random
import math
import time
import os
from datetime import datetime, timezone, timedelta
import paho.mqtt.client as mqtt
import boto3

def handler(event, context):
    """
    Эмулятор MQTT устройств с сохранением состояния батареи в S3 bucket
    """
    
    print("=" * 50)
    print(f"MQTT Emulator started at {datetime.now(timezone.utc).isoformat()}")
    print("=" * 50)
    
    # Получаем параметры
    mqtt_host = event.get('mqtt_host', os.environ.get('MQTT_HOST'))
    mqtt_port = int(event.get('mqtt_port', os.environ.get('MQTT_PORT', 1883)))
    
    if not mqtt_host:
        return {
            'statusCode': 400,
            'body': json.dumps({
                'status': 'error',
                'message': 'MQTT_HOST is required'
            })
        }
    
    # Параметры устройств
    devices = event.get('devices', ['Device_1', 'Device_2', 'Device_3', 'Device_4'])
    if devices and isinstance(devices[0], dict):
        devices = [d['device_id'] if isinstance(d, dict) else d for d in devices]
    
    # Параметры батареи
    battery_config = event.get('battery_config', {
        'charge_rate': 2.0,      # % в минуту при зарядке
        'discharge_rate': 1.0,   # % в минуту при разрядке
        'min_level': 20.0,       # Минимальный уровень
        'max_level': 90.0,       # Максимальный уровень
        'default_level': 70.0    # Уровень по умолчанию для новых устройств
    })
    
    # Параметры отправки
    count = int(event.get('count', 5))
    interval = float(event.get('interval', 0.5))
    
    # Диапазоны для других метрик
    temperature_range = event.get('temperature_range', [15.0, 35.0])
    humidity_range = event.get('humidity_range', [30.0, 90.0])
    pattern = event.get('pattern', 'random')
    
    # Загружаем состояния батарей из S3 bucket
    battery_states = load_battery_states_from_s3(devices, battery_config)
    
    print(f"Target: {mqtt_host}:{mqtt_port}")
    print(f"Devices: {devices}")
    print(f"Battery config: {battery_config}")
    print(f"Pattern: {pattern}")
    
    # Выводим начальные состояния
    print("\nInitial battery states:")
    for device_id, state in battery_states.items():
        print(f"  {device_id}: {state['level']:.1f}% [{'↑ Charging' if state['charging'] else '↓ Discharging'}] "
              f"(last update: {state.get('last_update', 'N/A')})")
    
    # Создаем MQTT клиент
    client_id = f"emulator_{int(time.time())}_{random.randint(1000, 9999)}"
    client = mqtt.Client(client_id=client_id)
    
    username = event.get('username', os.environ.get('MQTT_USERNAME'))
    password = event.get('password', os.environ.get('MQTT_PASSWORD'))
    if username and password:
        client.username_pw_set(username, password)
    
    qos = int(event.get('qos', 0))
    retain = event.get('retain', False)
    
    connected = False
    messages_sent = 0
    messages_failed = 0
    
    def on_connect(client, userdata, flags, rc):
        nonlocal connected
        if rc == 0:
            connected = True
            print(f"\nConnected to {mqtt_host}:{mqtt_port}")
        else:
            print(f"Connection failed with code {rc}")
    
    def on_publish(client, userdata, mid):
        nonlocal messages_sent
        messages_sent += 1
    
    client.on_connect = on_connect
    client.on_publish = on_publish
    
    try:
        client.connect(mqtt_host, mqtt_port, keepalive=60)
        client.loop_start()
        
        timeout = 10
        start_time = time.time()
        while not connected and (time.time() - start_time) < timeout:
            time.sleep(0.1)
        
        if not connected:
            return {
                'statusCode': 500,
                'body': json.dumps({
                    'status': 'error',
                    'message': f'Failed to connect to {mqtt_host}:{mqtt_port}'
                })
            }
    except Exception as e:
        return {
            'statusCode': 500,
            'body': json.dumps({
                'status': 'error',
                'message': f'Connection error: {str(e)}'
            })
        }
    
    # Отправляем сообщения
    results = []
    start_time = time.time()
    
    for device_id in devices:
        # Получаем состояние батареи
        battery_state = battery_states[device_id]
        
        device_result = {
            'device_id': device_id,
            'messages_sent': 0,
            'messages_failed': 0,
            'battery_levels': []
        }
        
        topic = f"devices/{device_id}"
        
        for i in range(count):
            # Обновляем уровень батареи
            battery_level = update_battery_level(battery_state, battery_config, interval)
            
            # Генерируем другие метрики
            status = random.choice(['online', 'online', 'online', 'warning', 'error'])
            
            metrics = {
                'temperature_c': round(generate_value(pattern, i, count, temperature_range), 2),
                'humidity_percent': round(generate_value(pattern, i, count, humidity_range), 2),
                'battery_level_percent': round(battery_level, 2)
            }
            
            # Формируем payload
            payload = {
                'device_id': device_id,
                'firmware_version': '1.0.0',
                'status': status,
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'metrics': metrics
            }
            
            try:
                result = client.publish(topic, json.dumps(payload), qos=qos, retain=retain)
                
                if result.rc == mqtt.MQTT_ERR_SUCCESS:
                    device_result['messages_sent'] += 1
                    device_result['battery_levels'].append(battery_level)
                    if i % 5 == 0 or i == count - 1:  # Печатаем каждое 5-е сообщение
                        print(f"  ✓ {device_id}: bat={battery_level:.1f}% "
                              f"[{'↑' if battery_state['charging'] else '↓'}] "
                              f"temp={metrics['temperature_c']}°C "
                              f"hum={metrics['humidity_percent']}%")
                else:
                    device_result['messages_failed'] += 1
                    print(f"  ✗ {device_id}: publish failed")
                    
            except Exception as e:
                device_result['messages_failed'] += 1
                print(f"  ✗ {device_id}: {str(e)}")
            
            if interval > 0 and i < count - 1:
                time.sleep(interval)
        
        results.append(device_result)
    
    # Отключаемся
    client.loop_stop()
    client.disconnect()
    
    # Сохраняем состояния батарей в S3 bucket
    save_battery_states_to_s3(battery_states)
    
    elapsed_time = time.time() - start_time
    total_sent = sum(r['messages_sent'] for r in results)
    total_failed = sum(r['messages_failed'] for r in results)
    
    # Выводим финальные состояния
    print("\nFinal battery states (saved to S3):")
    for device_id, state in battery_states.items():
        print(f"  {device_id}: {state['level']:.1f}% [{'↑ Charging' if state['charging'] else '↓ Discharging'}]")
    
    print("=" * 50)
    print(f"Emulation completed: {total_sent}/{total_sent + total_failed} messages sent in {elapsed_time:.2f}s")
    print("=" * 50)
    
    return {
        'statusCode': 200,
        'body': json.dumps({
            'status': 'success',
            'message': f'Sent {total_sent} messages to MQTT broker',
            'summary': {
                'total_messages': total_sent,
                'total_failed': total_failed,
                'elapsed_time': round(elapsed_time, 2),
                'devices': len(devices),
                'pattern': pattern
            },
            'battery_states': {k: {'level': round(v['level'], 2), 'charging': v['charging']} 
                              for k, v in battery_states.items()},
            'results': results
        }, default=str)
    }

def load_battery_states_from_s3(devices, config):
    """
    Загружает состояния батарей из S3 bucket
    """
    states = {}
    
    try:
        # Пробуем загрузить файл с состояниями
        saved_states = read_battery_states_file()
        
        if saved_states:
            print(f"Loaded battery states file from S3 (last modified: {saved_states.get('last_modified', 'unknown')})")
            
            for device_id in devices:
                if device_id in saved_states.get('devices', {}):
                    # Используем сохраненное состояние
                    device_state = saved_states['devices'][device_id]
                    last_update = datetime.fromisoformat(device_state['last_update'].replace('Z', '+00:00'))
                    time_diff = (datetime.now(timezone.utc) - last_update).total_seconds() / 60
                    
                    # Применяем изменение за прошедшее время
                    if time_diff > 0:
                        if device_state['charging']:
                            new_level = device_state['level'] + (config['charge_rate'] * time_diff)
                        else:
                            new_level = device_state['level'] - (config['discharge_rate'] * time_diff)
                        
                        # Ограничиваем диапазоном
                        new_level = max(config['min_level'], min(config['max_level'], new_level))
                    else:
                        new_level = device_state['level']
                    
                    states[device_id] = {
                        'level': new_level,
                        'charging': device_state['charging'],
                        'last_update': device_state['last_update']
                    }
                    
                    print(f"  Loaded {device_id}: {new_level:.1f}% "
                          f"(was {device_state['level']:.1f}% {time_diff:.1f} min ago)")
                else:
                    # Новое устройство
                    states[device_id] = create_initial_state(config)
                    print(f"  Created {device_id}: {states[device_id]['level']:.1f}% (new device)")
        else:
            # Файл не найден, создаем начальные состояния
            print("No battery states file found in S3, creating initial states")
            for device_id in devices:
                states[device_id] = create_initial_state(config)
                print(f"  Created {device_id}: {states[device_id]['level']:.1f}%")
    
    except Exception as e:
        print(f"Error loading from S3: {e}")
        # Fallback: создаем состояния по умолчанию
        for device_id in devices:
            states[device_id] = create_initial_state(config)
    
    return states

def create_initial_state(config):
    """Создает начальное состояние для устройства"""
    initial_level = random.uniform(
        config['min_level'] + 20, 
        config['max_level'] - 10
    )
    return {
        'level': initial_level,
        'charging': random.choice([True, False]),
        'last_update': datetime.now(timezone.utc).isoformat()
    }

def read_battery_states_file():
    """
    Читает файл с состояниями батарей из S3 bucket
    """
    try:
        s3 = get_s3_client()
        bucket_name = os.environ['BUCKET_NAME']
        
        try:
            response = s3.get_object(
                Bucket=bucket_name,
                Key='emulator/battery_states.json'
            )
            content = json.loads(response['Body'].read())
            content['last_modified'] = response['LastModified'].isoformat()
            print(f"Read battery states from s3://{bucket_name}/emulator/battery_states.json")
            return content
        except s3.exceptions.NoSuchKey:
            print(f"File not found: s3://{bucket_name}/emulator/battery_states.json")
            return None
        except Exception as e:
            print(f"Error reading from S3: {e}")
            return None
            
    except Exception as e:
        print(f"S3 client error: {e}")
        return None

def save_battery_states_to_s3(states):
    """
    Сохраняет состояния батарей в S3 bucket
    """
    try:
        s3 = get_s3_client()
        bucket_name = os.environ['BUCKET_NAME']
        
        # Формируем данные для сохранения
        data = {
            'last_updated': datetime.now(timezone.utc).isoformat(),
            'devices': {}
        }
        
        for device_id, state in states.items():
            data['devices'][device_id] = {
                'level': round(state['level'], 2),
                'charging': state['charging'],
                'last_update': datetime.now(timezone.utc).isoformat()
            }
        
        # Сохраняем в S3
        s3.put_object(
            Bucket=bucket_name,
            Key='emulator/battery_states.json',
            Body=json.dumps(data, indent=2),
            ContentType='application/json'
        )
        
        print(f"Saved battery states to s3://{bucket_name}/emulator/battery_states.json")
        
        # Выводим что сохранили
        for device_id, device_data in data['devices'].items():
            print(f"  {device_id}: {device_data['level']:.1f}% [{'↑' if device_data['charging'] else '↓'}]")
        
    except Exception as e:
        print(f"Error saving to S3: {e}")

def update_battery_level(state, config, time_delta_minutes):
    """
    Обновляет уровень батареи с пилообразным поведением
    """
    if state['charging']:
        # Заряжаем
        new_level = state['level'] + (config['charge_rate'] * time_delta_minutes)
        
        if new_level >= config['max_level']:
            new_level = config['max_level']
            state['charging'] = False  # Начинаем разряжать
            print(f"    ⚡ Battery full ({new_level:.1f}%), starting discharge")
    else:
        # Разряжаем
        new_level = state['level'] - (config['discharge_rate'] * time_delta_minutes)
        
        if new_level <= config['min_level']:
            new_level = config['min_level']
            state['charging'] = True  # Начинаем заряжать
            print(f"    🔋 Battery low ({new_level:.1f}%), starting charge")
    
    # Добавляем небольшой шум для реалистичности
    new_level += random.gauss(0, 0.1)
    new_level = max(config['min_level'], min(config['max_level'], new_level))
    
    state['level'] = new_level
    state['last_update'] = datetime.now(timezone.utc).isoformat()
    
    return new_level

def generate_value(pattern, index, total, value_range):
    """Генерирует значение метрики"""
    min_val, max_val = value_range
    range_size = max_val - min_val
    
    if pattern == 'sine':
        angle = (index / max(total, 1)) * 2 * math.pi
        value = (min_val + max_val) / 2 + (range_size / 2) * math.sin(angle)
    elif pattern == 'linear':
        value = min_val + (range_size * index / max(total, 1))
    elif pattern == 'step':
        step_size = max(1, total // 5)
        step = index // step_size
        value = min_val + (range_size * step / 4)
    elif pattern == 'sawtooth':
        period = max(total / 3, 1)
        value = min_val + (range_size * (index % period) / period)
    else:  # random
        value = random.uniform(min_val, max_val)
    
    value += random.gauss(0, range_size * 0.02)
    return max(min_val, min(max_val, value))

def get_s3_client():
    """Создает клиент S3 для Yandex Object Storage"""
    endpoint_url = os.environ.get('STORAGE_ENDPOINT', 'https://storage.yandexcloud.net')
    aws_access_key_id = os.environ.get('AWS_ACCESS_KEY_ID')
    aws_secret_access_key = os.environ.get('AWS_SECRET_ACCESS_KEY')
    
    return boto3.client('s3',
        endpoint_url=endpoint_url,
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key
    )