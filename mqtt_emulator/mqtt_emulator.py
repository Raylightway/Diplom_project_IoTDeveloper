import json
import random
import math
import time
import os
from datetime import datetime, timezone
import paho.mqtt.client as mqtt

def handler(event, context):
    """
    Эмулятор MQTT устройств - отправляет сообщения в Mosquitto брокер
    
    Параметры event:
    - mqtt_host: адрес MQTT брокера
    - mqtt_port: порт (по умолчанию 1883)
    - devices: список устройств
    - topics: список топиков (если не указаны, используются device_id)
    - count: количество сообщений на устройство
    - interval: интервал между сообщениями (сек)
    - value_range: [min, max] диапазон значений
    - pattern: паттерн генерации (random, sine, linear, step, sawtooth)
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
    devices = event.get('devices', ['sensor_1', 'sensor_2', 'sensor_3'])
    topics = event.get('topics', [f"devices/{device}" for device in devices])
    
    if len(topics) != len(devices):
        topics = [f"devices/{device}" for device in devices]
    
    # Параметры отправки
    count = int(event.get('count', 10))
    interval = float(event.get('interval', 1.0))
    value_range = event.get('value_range', [0, 100])
    pattern = event.get('pattern', 'random')
    
    # Дополнительные параметры
    qos = int(event.get('qos', 0))
    retain = event.get('retain', False)
    username = event.get('username', os.environ.get('MQTT_USERNAME'))
    password = event.get('password', os.environ.get('MQTT_PASSWORD'))
    
    print(f"Target: {mqtt_host}:{mqtt_port}")
    print(f"Devices: {len(devices)}, Messages per device: {count}")
    print(f"Pattern: {pattern}, Range: {value_range}")
    
    # Создаем MQTT клиент
    client_id = f"emulator_{int(time.time())}_{random.randint(1000, 9999)}"
    client = mqtt.Client(client_id=client_id)
    
    # Настраиваем аутентификацию если нужно
    if username and password:
        client.username_pw_set(username, password)
    
    # Флаги для отслеживания состояния
    connected = False
    messages_sent = 0
    messages_failed = 0
    
    def on_connect(client, userdata, flags, rc):
        nonlocal connected
        if rc == 0:
            connected = True
            print(f"Connected to {mqtt_host}:{mqtt_port}")
        else:
            print(f"Connection failed with code {rc}")
    
    def on_publish(client, userdata, mid):
        nonlocal messages_sent
        messages_sent += 1
    
    client.on_connect = on_connect
    client.on_publish = on_publish
    
    # Подключаемся к брокеру
    try:
        client.connect(mqtt_host, mqtt_port, keepalive=60)
        client.loop_start()
        
        # Ждем подключения
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
    
    for device_id, topic in zip(devices, topics):
        device_result = {
            'device_id': device_id,
            'topic': topic,
            'messages_sent': 0,
            'messages_failed': 0,
            'values': []
        }
        
        for i in range(count):
            # Генерируем значение
            value = generate_value(pattern, i, count, value_range)
            
            # Формируем payload
            payload = {
                'device_id': device_id,
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'value': value,
                'sequence': i + 1,
                'pattern': pattern
            }
            
            try:
                # Отправляем в MQTT
                result = client.publish(
                    topic,
                    json.dumps(payload),
                    qos=qos,
                    retain=retain
                )
                
                if result.rc == mqtt.MQTT_ERR_SUCCESS:
                    device_result['messages_sent'] += 1
                    device_result['values'].append(value)
                    print(f"  ✓ {topic}: {value:.2f}")
                else:
                    device_result['messages_failed'] += 1
                    print(f"  ✗ {topic}: publish failed")
                    
            except Exception as e:
                device_result['messages_failed'] += 1
                print(f"  ✗ {topic}: {str(e)}")
            
            # Пауза между сообщениями
            if interval > 0 and i < count - 1:
                time.sleep(interval)
        
        results.append(device_result)
    
    # Отключаемся
    client.loop_stop()
    client.disconnect()
    
    elapsed_time = time.time() - start_time
    total_sent = sum(r['messages_sent'] for r in results)
    total_failed = sum(r['messages_failed'] for r in results)
    
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
                'pattern': pattern,
                'mqtt_host': mqtt_host,
                'mqtt_port': mqtt_port
            },
            'results': results
        }, default=str)
    }

def generate_value(pattern, index, total, value_range):
    """
    Генерирует значение в зависимости от паттерна
    
    Patterns:
    - random: случайное значение в диапазоне
    - sine: синусоида
    - linear: линейное возрастание
    - step: ступенчатая функция
    - sawtooth: пилообразная волна
    """
    min_val, max_val = value_range
    range_size = max_val - min_val
    mid_val = (min_val + max_val) / 2
    
    if pattern == 'sine':
        # Синусоида
        angle = (index / total) * 2 * math.pi
        value = mid_val + (range_size / 2) * math.sin(angle)
        
    elif pattern == 'linear':
        # Линейное возрастание
        value = min_val + (range_size * index / total)
        
    elif pattern == 'step':
        # Ступенчатая функция
        step_size = max(1, total // 5)
        step = index // step_size
        value = min_val + (range_size * step / 4)
        
    elif pattern == 'sawtooth':
        # Пилообразная волна
        period = total / 3
        value = min_val + (range_size * (index % period) / period)
        
    else:  # random
        value = random.uniform(min_val, max_val)
    
    # Добавляем небольшой шум для реалистичности
    value += random.gauss(0, range_size * 0.02)
    
    # Округляем до 2 знаков
    return round(max(min_val, min(max_val, value)), 2)