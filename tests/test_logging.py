import logging
from unittest.mock import Mock

from app import tasks
from app.celery_app import log_publish


def test_trace_logs_exclude_message_and_credentials(client, headers, payload, monkeypatch, caplog):
    application_logger = logging.getLogger("app")
    application_logger.addHandler(caplog.handler)
    caplog.set_level(logging.INFO)
    try:
        response = client.post("/notifications", headers=headers, json=payload)
        assert response.status_code == 202
        notification_id = response.json()["id"]
        monkeypatch.setattr(tasks, "send_email", Mock())
        log_publish(sender="app.tasks.deliver", headers={"id": "test-task-id"},
                    routing_key="celery", body={"private": payload})
        tasks.deliver.run(notification_id)
        assert "EMAIL_SAVED" in caplog.text
        assert "TASK_PUBLISHED" in caplog.text
        assert "status=sent" in caplog.text
        assert notification_id in caplog.text
        for private_value in [payload["recipient"], payload["subject"], payload["text"],
                              headers["X-API-Key"], headers["Idempotency-Key"]]:
            assert private_value not in caplog.text
    finally:
        application_logger.removeHandler(caplog.handler)
