import psycopg2
import os
import logging
from psycopg2 import sql

logger = logging.getLogger()
logger.setLevel(logging.INFO)

def handler(event, context):
    """
    Функция для инициализации базы данных
    Создает необходимые таблицы при первом запуске
    """
    
    # Получаем параметры подключения
    db_config = {
        'host': os.environ['DB_HOST'],
        'port': os.environ.get('DB_PORT', '6432'),
        'database': os.environ['DB_NAME'],
        'user': os.environ['DB_USER'],
        'password': os.environ['DB_PASSWORD']
    }
    
    connection = None
    cursor = None
    
    try:
        logger.info(f"Connecting to database at {db_config['host']}:{db_config['port']}")
        
        # Подключаемся к БД
        connection = psycopg2.connect(
            host=db_config['host'],
            port=db_config['port'],
            database=db_config['database'],
            user=db_config['user'],
            password=db_config['password'],
            sslmode='require'
        )
        
        cursor = connection.cursor()
        
        # SQL для создания таблицы (из вашего init.sql)
        create_table_sql = """
        CREATE TABLE IF NOT EXISTS averaged_data (
            id SERIAL PRIMARY KEY,
            interval_start TIMESTAMP NOT NULL,
            average_value DOUBLE PRECISION NOT NULL,
            count INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        
        CREATE INDEX IF NOT EXISTS idx_interval_start ON averaged_data(interval_start);
        
        -- Дополнительно: создаем таблицу для raw данных, если нужно
        CREATE TABLE IF NOT EXISTS raw_telemetry (
            id SERIAL PRIMARY KEY,
            timestamp TIMESTAMP NOT NULL,
            value DOUBLE PRECISION NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        
        CREATE INDEX IF NOT EXISTS idx_raw_timestamp ON raw_telemetry(timestamp);
        """
        
        # Выполняем SQL
        cursor.execute(create_table_sql)
        connection.commit()
        
        # Проверяем, что таблица создалась
        cursor.execute("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public' 
            AND table_name IN ('averaged_data', 'raw_telemetry')
        """)
        
        tables = cursor.fetchall()
        logger.info(f"Successfully created tables: {[t[0] for t in tables]}")
        
        return {
            'statusCode': 200,
            'body': {
                'message': 'Database initialized successfully',
                'tables': [t[0] for t in tables]
            }
        }
        
    except psycopg2.Error as e:
        logger.error(f"Database error: {str(e)}")
        if connection:
            connection.rollback()
        return {
            'statusCode': 500,
            'body': {
                'error': 'Database initialization failed',
                'details': str(e)
            }
        }
        
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        return {
            'statusCode': 500,
            'body': {
                'error': 'Unexpected error',
                'details': str(e)
            }
        }
        
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()
            logger.info("Database connection closed")