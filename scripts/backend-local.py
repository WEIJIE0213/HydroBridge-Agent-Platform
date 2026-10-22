"""Run a backend CLI against published local service ports without printing credentials."""
import os
import runpy
import sys
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy.engine import URL

root = Path(__file__).resolve().parents[1]
env = dotenv_values(root / '.env')
for path in (root / '.env', Path.home() / '.continuum' / '.env'):
    if path.exists():
        for key, value in dotenv_values(path).items():
            if value is not None:
                os.environ[key] = value
os.environ.update({
    'DATABASE_URL': URL.create('postgresql+asyncpg', username=env.get('POSTGRES_USER','hydrobridge'),
        password=env.get('POSTGRES_PASSWORD','change-me-local'), host='127.0.0.1', port=5433,
        database=env.get('POSTGRES_DB','hydrobridge')).render_as_string(hide_password=False),
    'REDIS_RUNTIME_URL': 'redis://127.0.0.1:6380/0',
    'REDIS_CACHE_URL': 'redis://127.0.0.1:6381/0',
    'CELERY_BROKER_URL': 'redis://127.0.0.1:6380/1',
    'CELERY_RESULT_BACKEND': 'redis://127.0.0.1:6380/2',
    'ELASTICSEARCH_URL': 'http://127.0.0.1:9200',
    'ELASTICSEARCH_ANALYZER': 'ik_max_word',
    'ELASTICSEARCH_INDEX': 'hydrobridge-chunks-bge-v4',
    'KNOWLEDGE_INDEX_VERSION': '4',
    'EMBEDDING_BACKEND': 'bge_service',
    'BGE_SERVICE_URL': 'http://127.0.0.1:8081',
    'RETRIEVAL_BACKEND': 'elasticsearch',
    'RETRIEVAL_ALLOW_FALLBACK': 'false',
    'MINIO_ENDPOINT': '127.0.0.1:9000',
    'BOOTSTRAP_SEED_PATH': str(root / 'seed-data/achievements.json'),
})
sys.path.insert(0, str(root / 'backend'))
module = sys.argv.pop(1)
runpy.run_module(module, run_name='__main__')
