import smtplib
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import Mock

from app import tasks
from app.database import Notification, Session, utcnow


def create(client, headers, payload):
    response = client.post("/notifications", headers=headers, json=payload)
    assert response.status_code == 202
    return response.json()["id"]


def test_auth_and_validation(client, headers, payload):
    assert client.post("/notifications", json=payload).status_code == 401
    assert client.post("/notifications", headers=headers, json={**payload, "recipient": "bad"}).status_code == 422
    assert client.post("/notifications", headers=headers, json={**payload, "subject": "Hi\r\nBcc: evil"}).status_code == 422
    assert client.post("/notifications", headers=headers, json={**payload, "scheduled_at": "2030-01-01T10:00:00"}).status_code == 422


def test_idempotency(client, headers, payload):
    first = create(client, headers, payload)
    assert create(client, headers, payload) == first
    assert client.post("/notifications", headers=headers, json={**payload, "text": "Changed"}).status_code == 409
    assert len(client.get("/notifications", headers=headers).json()) == 1


def test_delivery_and_duplicate_jobs(client, headers, payload, monkeypatch):
    notification_id = create(client, headers, payload)
    send = Mock()
    monkeypatch.setattr(tasks, "send_email", send)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(tasks.deliver.run, [notification_id, notification_id]))
    tasks.deliver.run(notification_id)
    send.assert_called_once()
    status = client.get(f"/notifications/{notification_id}", headers=headers).json()
    assert status["status"] == "sent"
    assert status["attempts"] == 1
    assert status["sent_at"] is not None


def test_retry_then_success(client, headers, payload, monkeypatch):
    notification_id = create(client, headers, payload)
    send = Mock(side_effect=[smtplib.SMTPDataError(451, b"Temporary error"), None])
    monkeypatch.setattr(tasks, "send_email", send)
    tasks.deliver.run(notification_id)
    with Session.begin() as session:
        row = session.get(Notification, notification_id)
        assert row.status == "retrying"
        assert row.next_attempt_at > utcnow()
        row.next_attempt_at = utcnow() - timedelta(seconds=1)
    tasks.deliver.run(notification_id)
    assert client.get(f"/notifications/{notification_id}", headers=headers).json()["status"] == "sent"


def test_permanent_failure(client, headers, payload, monkeypatch):
    notification_id = create(client, headers, payload)
    monkeypatch.setattr(tasks, "send_email", Mock(side_effect=smtplib.SMTPRecipientsRefused(
        {"person@example.com": (550, b"Recipient rejected")})))
    tasks.deliver.run(notification_id)
    status = client.get(f"/notifications/{notification_id}", headers=headers).json()
    assert status["status"] == "failed"
    assert status["last_error"] == "SMTPRecipientsRefused"


def test_attempt_limit(client, headers, payload, monkeypatch):
    notification_id = create(client, headers, payload)
    with Session.begin() as session:
        session.get(Notification, notification_id).attempts = 4
    monkeypatch.setattr(tasks, "send_email", Mock(side_effect=TimeoutError()))
    tasks.deliver.run(notification_id)
    assert client.get(f"/notifications/{notification_id}", headers=headers).json()["status"] == "failed"


def test_schedule_cancel_and_dispatch(client, headers, payload, monkeypatch):
    future_id = create(client, headers, {**payload, "scheduled_at": "2099-01-01T00:00:00Z"})
    send = Mock()
    monkeypatch.setattr(tasks, "send_email", send)
    tasks.deliver.run(future_id)
    send.assert_not_called()
    enqueue = Mock()
    monkeypatch.setattr(tasks.deliver, "delay", enqueue)
    assert tasks.dispatch_due.run() == 0
    notification_id = create(client, {**headers, "Idempotency-Key": "due"}, payload)
    assert tasks.dispatch_due.run() == 1
    enqueue.assert_called_once_with(notification_id)
    assert client.post(f"/notifications/{notification_id}/cancel", headers=headers).json()["status"] == "cancelled"
    tasks.deliver.run(notification_id)
    send.assert_not_called()


def test_expired_claim_recovery(client, headers, payload, monkeypatch):
    notification_id = create(client, headers, payload)
    with Session.begin() as session:
        row = session.get(Notification, notification_id)
        row.status = "sending"
        row.lease_until = utcnow() - timedelta(seconds=1)
        row.claim_token = "old-worker"
    send = Mock()
    monkeypatch.setattr(tasks, "send_email", send)
    tasks.deliver.run(notification_id)
    send.assert_called_once()


def test_smtp_message(monkeypatch):
    from app import mailer
    smtp = Mock()
    smtp.__enter__ = Mock(return_value=smtp)
    smtp.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(mailer.smtplib, "SMTP", Mock(return_value=smtp))
    mailer.send_email(Notification(id="123", recipient="person@example.com", subject="Hi",
                                   text="Hello", html="<p>Hello</p>"))
    message = smtp.send_message.call_args.args[0]
    assert message["Message-ID"] == "<123@example.com>"
    assert message.get_body(preferencelist=("html",)).get_content().strip() == "<p>Hello</p>"


def test_live_claim_is_not_stolen(client, headers, payload, monkeypatch):
    notification_id = create(client, headers, payload)
    with Session.begin() as session:
        row = session.get(Notification, notification_id)
        row.status = "sending"
        row.lease_until = utcnow() + timedelta(minutes=2)
        row.claim_token = "active-worker"
    send = Mock()
    monkeypatch.setattr(tasks, "send_email", send)
    tasks.deliver.run(notification_id)
    send.assert_not_called()
    assert client.post(f"/notifications/{notification_id}/cancel", headers=headers).status_code == 409


def test_interrupted_attempt_limit(client, headers, payload, monkeypatch):
    notification_id = create(client, headers, payload)
    with Session.begin() as session:
        row = session.get(Notification, notification_id)
        row.status = "sending"
        row.attempts = 5
        row.lease_until = utcnow() - timedelta(minutes=1)
    send = Mock()
    monkeypatch.setattr(tasks, "send_email", send)
    tasks.deliver.run(notification_id)
    send.assert_not_called()
    assert client.get(f"/notifications/{notification_id}", headers=headers).json()["status"] == "failed"
