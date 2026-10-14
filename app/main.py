import hashlib
import logging
import secrets
from contextlib import asynccontextmanager
from datetime import timezone
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.security import APIKeyHeader
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.config import get_settings
from app.database import Notification, Session, init_db, utcnow
from app.schemas import EmailRequest, EmailStatus
from app.logging_setup import configure_logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app):
    configure_logging()
    init_db()
    logger.info("API_READY database_initialized=true")
    yield
    logger.info("API_STOPPED")


app = FastAPI(title="Email Notification Service", version="1.0.0", lifespan=lifespan)
api_key = APIKeyHeader(name="X-API-Key", auto_error=False)


def authenticate(key: str | None = Depends(api_key)):
    if not key or not secrets.compare_digest(key.encode(), get_settings().api_key.get_secret_value().encode()):
        logger.warning("AUTH_REJECTED reason=invalid_api_key")
        raise HTTPException(401, "Invalid API key")


@app.get("/health/live")
def live():
    return {"status": "ok"}


@app.get("/health/ready", dependencies=[Depends(authenticate)])
def ready():
    from redis import Redis
    from redis.exceptions import RedisError
    try:
        with Session() as session:
            session.execute(text("SELECT 1"))
        with Redis.from_url(get_settings().redis_url, socket_connect_timeout=2, socket_timeout=2) as redis:
            redis.ping()
    except (SQLAlchemyError, RedisError) as exc:
        logger.warning("READINESS_FAILED error_type=%s", type(exc).__name__)
        raise HTTPException(503, "Database or broker unavailable") from None
    return {"status": "ready"}


@app.post("/notifications", response_model=EmailStatus, status_code=202, dependencies=[Depends(authenticate)])
def create_email(payload: EmailRequest, idempotency_key: Annotated[str, Header(min_length=1, max_length=128)]):
    digest = hashlib.sha256(payload.model_dump_json().encode()).hexdigest()
    notification = Notification(id=str(uuid4()), idempotency_key=idempotency_key, payload_hash=digest,
                                recipient=str(payload.recipient), subject=payload.subject,
                                text=payload.text, html=payload.html,
                                next_attempt_at=payload.scheduled_at.astimezone(timezone.utc).replace(tzinfo=None)
                                if payload.scheduled_at else utcnow())
    with Session() as session:
        session.add(notification)
        try:
            session.commit()
            logger.info("EMAIL_SAVED notification_id=%s status=%s due_at=%s",
                        notification.id, notification.status, notification.next_attempt_at)
        except IntegrityError:
            session.rollback()
            notification = session.scalar(select(Notification).where(Notification.idempotency_key == idempotency_key))
            if notification is None:
                raise
            if notification.payload_hash != digest:
                logger.warning("IDEMPOTENCY_CONFLICT notification_id=%s", notification.id)
                raise HTTPException(409, "Idempotency key already used with a different payload")
            logger.info("IDEMPOTENCY_REUSED notification_id=%s", notification.id)
        return EmailStatus.model_validate(notification)


@app.get("/notifications", response_model=list[EmailStatus], dependencies=[Depends(authenticate)])
def list_emails(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
    with Session() as session:
        return [EmailStatus.model_validate(row) for row in session.scalars(
            select(Notification).order_by(Notification.created_at.desc(), Notification.id).offset(offset).limit(limit))]


@app.get("/notifications/{notification_id}", response_model=EmailStatus, dependencies=[Depends(authenticate)])
def get_email(notification_id: str):
    with Session() as session:
        row = session.get(Notification, notification_id)
        if row is None:
            raise HTTPException(404, "Notification not found")
        logger.debug("STATUS_READ notification_id=%s status=%s attempts=%s", row.id, row.status, row.attempts)
        return EmailStatus.model_validate(row)


@app.post("/notifications/{notification_id}/cancel", response_model=EmailStatus, dependencies=[Depends(authenticate)])
def cancel_email(notification_id: str):
    with Session.begin() as session:
        result = session.execute(update(Notification).where(Notification.id == notification_id,
                                 Notification.status.in_(["pending", "retrying"]))
                                 .values(status="cancelled"))
        if not result.rowcount:
            if session.get(Notification, notification_id) is None:
                raise HTTPException(404, "Notification not found")
            raise HTTPException(409, "Only pending or retrying notifications can be cancelled")
        response = EmailStatus.model_validate(session.get(Notification, notification_id))
    logger.info("EMAIL_CANCELLED notification_id=%s", response.id)
    return response
