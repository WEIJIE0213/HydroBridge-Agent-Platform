from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

celery_app = Celery(
    "hydrobridge",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.worker.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    result_expires=86_400,
    task_default_retry_delay=10,
    task_routes={
        "app.worker.tasks.process_outbox": {"queue": "indexing"},
        "app.worker.tasks.run_monitoring_scan": {"queue": "research"},
        "app.worker.tasks.run_evaluation": {"queue": "evaluation"},
    },
    beat_schedule={
        "process-outbox-every-10-seconds": {
            "task": "app.worker.tasks.process_outbox",
            "schedule": 10.0,
        },
        "monitor-water-achievements-daily": {
            "task": "app.worker.tasks.run_monitoring_scan",
            "schedule": crontab(hour=2, minute=30),
        },
    },
)
