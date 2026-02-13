import os

import pymysql
from dotenv import find_dotenv, load_dotenv

load_dotenv()
CURRENT_ENV = os.getenv('SERVICE_ENV', 'test')
load_dotenv(find_dotenv(f'.env.{CURRENT_ENV}'))

AG_UI_EVENT = 'ag-ui'

DB_CONFIG = {
    'host': os.getenv('MYSQL_HOST', 'localhost'),
    'port': int(os.getenv('MYSQL_PORT', 3306)),
    'user': os.getenv('MYSQL_USER', 'root'),
    'password': os.getenv('MYSQL_PASSWORD', 'password'),
    'database': os.getenv('MYSQL_DATABASE', 'matmaster'),
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor,  # 直接返回字典格式
    'autocommit': False,
}

BI_URL = os.getenv('BI_URL', 'https://account.test.dp.tech')
