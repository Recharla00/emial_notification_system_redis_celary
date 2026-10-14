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
    with Session() as session:
        ids = session.scalars(select(Notification.id).where(due(utcnow()))
                              .order_by(Notification.next_attempt_at).limit(100)).all()
    for notification_id in ids:
        deliver.delay(notification_id)
    return len(ids)


def permanent_failure(exc):
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return all(500 <= code < 600 for code, _ in exc.recipients.values())
    return isinstance(exc, smtplib.SMTPResponseException) and 500 <= exc.smtp_code < 600


@celery.task(name="app.tasks.deliver")
def deliver(notification_id):
    now, token = utcnow(), str(uuid4())
    with Session.begin() as session:
        result = session.execute(update(Notification).where(Notification.id == notification_id, due(now))
                                 .values(status="sending", claim_token=token,
                                         lease_until=now + timedelta(minutes=5)))
        if not result.rowcount:
            return
    with Session.begin() as session:
        notification = session.get(Notification, notification_id)
        if notification.attempts >= get_settings().max_attempts:
            notification.status = "failed"
            notification.last_error = "Attempt limit reached after interrupted delivery"
            notification.lease_until = None
            notification.claim_token = None
            return
        notification.attempts += 1
    try:
        send_email(notification)
    except Exception as exc:
        terminal = permanent_failure(exc) or notification.attempts >= get_settings().max_attempts
        values = dict(status="failed" if terminal else "retrying",
                      last_error=type(exc).__name__, lease_until=None, claim_token=None,
                      next_attempt_at=utcnow() + timedelta(seconds=min(3600, 30 * 2 ** (notification.attempts - 1)) + random.randint(0, 10)))
        # Never log SMTP error bodies, credentials, message content, or recipients.
        logger.warning("Email %s attempt %s: %s", notification_id, notification.attempts, type(exc).__name__)
    else:
        values = dict(status="sent", sent_at=utcnow(), last_error=None, lease_until=None, claim_token=None)
    with Session.begin() as session:
        session.execute(update(Notification).where(Notification.id == notification_id,
                                                   Notification.claim_token == token).values(**values))
