import logging
import random
import smtplib
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import and_, or_, select, update

from app.celery_app import celery
from app.config import get_settings
from app.database import Notification, Session, utcnow
from app.mailer import send_email

logger = logging.getLogger(__name__)


def due(now):
    return or_(
        and_(Notification.status.in_(["pending", "retrying"]), Notification.next_attempt_at <= now),
        and_(Notification.status == "sending", Notification.lease_until <= now),
    )


@celery.task(name="app.tasks.dispatch_due")
def dispatch_due():
    # The database is a durable outbox. Broker outages never lose accepted requests.
    logger.info("DISPATCH scanning database for due notifications")
    with Session() as session:
        ids = session.scalars(select(Notification.id).where(due(utcnow()))
                              .order_by(Notification.next_attempt_at).limit(100)).all()
    logger.info("DISPATCH found=%s", len(ids))
    for notification_id in ids:
        try:
            job = deliver.delay(notification_id)
        except Exception as exc:
            logger.error("ENQUEUE_FAILED notification_id=%s error_type=%s (record remains in database)",
                         notification_id, type(exc).__name__)
            raise
        logger.info("ENQUEUED notification_id=%s task_id=%s", notification_id, job.id)
    return len(ids)


def permanent_failure(exc):
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return all(500 <= code < 600 for code, _ in exc.recipients.values())
    return isinstance(exc, smtplib.SMTPResponseException) and 500 <= exc.smtp_code < 600


@celery.task(name="app.tasks.deliver")
def deliver(notification_id):
    logger.info("DELIVERY starting notification_id=%s task_id=%s", notification_id, deliver.request.id)
    now, token = utcnow(), str(uuid4())
    with Session.begin() as session:
        result = session.execute(update(Notification).where(Notification.id == notification_id, due(now))
                                 .values(status="sending", claim_token=token,
                                         lease_until=now + timedelta(minutes=5)))
        if not result.rowcount:
            logger.info("DELIVERY skipped notification_id=%s (not due or already claimed)", notification_id)
            return
    with Session.begin() as session:
        notification = session.get(Notification, notification_id)
        if notification.attempts >= get_settings().max_attempts:
            notification.status = "failed"
            notification.last_error = "Attempt limit reached after interrupted delivery"
            notification.lease_until = None
            notification.claim_token = None
            logger.warning("ATTEMPT_LIMIT notification_id=%s attempts=%s", notification_id, notification.attempts)
            return
        notification.attempts += 1
    logger.info("SMTP sending notification_id=%s attempt=%s", notification_id, notification.attempts)
    try:
        send_email(notification)
    except Exception as exc:
        terminal = permanent_failure(exc) or notification.attempts >= get_settings().max_attempts
        values = dict(status="failed" if terminal else "retrying",
                      last_error=type(exc).__name__, lease_until=None, claim_token=None,
                      next_attempt_at=utcnow() + timedelta(seconds=min(3600, 30 * 2 ** (notification.attempts - 1)) + random.randint(0, 10)))
        # Never log SMTP error bodies, credentials, message content, or recipients.
        logger.warning("DELIVERY_ERROR notification_id=%s attempt=%s error_type=%s target_status=%s next_attempt_at=%s",
                       notification_id, notification.attempts, type(exc).__name__, values["status"],
                       values["next_attempt_at"] if not terminal else "none")
    else:
        values = dict(status="sent", sent_at=utcnow(), last_error=None, lease_until=None, claim_token=None)
    with Session.begin() as session:
        result = session.execute(update(Notification).where(Notification.id == notification_id,
                                                   Notification.claim_token == token).values(**values))
    if result.rowcount:
        logger.info("DELIVERY recorded notification_id=%s status=%s", notification_id, values["status"])
    else:
        logger.warning("DELIVERY_UPDATE_SKIPPED notification_id=%s reason=claim_changed", notification_id)
