from celery import Celery
from celery.signals import after_task_publish, task_received, task_prerun, task_postrun
import logging

from app.config import get_settings
from app.logging_setup import configure_logging

configure_logging()
logger = logging.getLogger(__name__)

celery = Celery("notifications", broker=get_settings().redis_url, include=["app.tasks"])
celery.conf.update(
    task_serializer="json", accept_content=["json"], timezone="UTC",
    task_ignore_result=True, task_acks_late=True, task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1, broker_connection_retry_on_startup=True,
    broker_transport_options={"visibility_timeout": 3600},
    task_soft_time_limit=90, task_time_limit=120,
    beat_schedule={"dispatch-due-emails": {"task": "app.tasks.dispatch_due", "schedule": 60.0}},
)


@after_task_publish.connect
def log_publish(sender=None, headers=None, routing_key=None, **kwargs):
    if sender in {"app.tasks.dispatch_due", "app.tasks.deliver"}:
        logger.info("TASK_PUBLISHED task=%s task_id=%s queue=%s", sender,
                    (headers or {}).get("id"), routing_key)


@task_received.connect
def log_received(request=None, **kwargs):
    if request is not None:
        logger.info("TASK_RECEIVED task=%s task_id=%s", request.name, request.id)


@task_prerun.connect
def log_started(task_id=None, task=None, **kwargs):
    logger.info("TASK_STARTED task=%s task_id=%s", getattr(task, "name", None), task_id)


@task_postrun.connect
def log_finished(task_id=None, task=None, state=None, **kwargs):
    logger.info("TASK_FINISHED task=%s task_id=%s celery_state=%s",
                getattr(task, "name", None), task_id, state)
