from celery import Celery

from app.config import get_settings

celery = Celery("notifications", broker=get_settings().redis_url, include=["app.tasks"])
celery.conf.update(
    task_serializer="json", accept_content=["json"], timezone="UTC",
    task_ignore_result=True, task_acks_late=True, task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1, broker_connection_retry_on_startup=True,
    broker_transport_options={"visibility_timeout": 3600},
    task_soft_time_limit=90, task_time_limit=120,
    beat_schedule={"dispatch-due-emails": {"task": "app.tasks.dispatch_due", "schedule": 10.0}},
)
