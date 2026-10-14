# Redis + Celery email notifications

See [LOGGING.md](LOGGING.md) for tracing API, Beat, Redis publication, worker execution, and SMTP events.

For logs, Redis queue inspection, and interactive worker breakpoints, follow [the debugging walkthrough](DEBUGGING.md).

A FastAPI email service with SMTP delivery, Redis as Celery's broker, and PostgreSQL as a durable email outbox. SQLite is available for local development and tests.

## Run the complete local stack

Install Docker Desktop with Linux containers, then run from this directory:

```powershell
Copy-Item .env.example .env
# Replace API_KEY in .env with a long random secret (at least 24 characters).
docker compose up --build -d
```

- API and interactive Swagger UI: http://localhost:8000/docs
- Test email inbox (Mailpit): http://localhost:8025
- Logs: `docker compose logs -f api worker beat`
- Stop: `docker compose down` (database and Redis volumes are retained)

Compose intentionally routes all emails to Mailpit. It starts PostgreSQL, Redis, database initialization, API, Celery workers, and one Celery Beat scheduler. API and Mailpit ports bind only to localhost. Redis and PostgreSQL are internal to the Compose network.

## Send an email

Use your configured API key. Each logical request needs a unique `Idempotency-Key`; retry the same request with the same key to retrieve the existing notification. Reusing the key with a different payload returns HTTP 409.

```powershell
$headers = @{
    'X-API-Key' = 'your-secret-from-env'
    'Idempotency-Key' = [guid]::NewGuid().ToString()
}
$body = @{
    recipient = 'person@example.com'
    subject = 'Welcome!'
    text = 'Your account is ready.'
    html = '<h1>Welcome!</h1><p>Your account is ready.</p>'
} | ConvertTo-Json
$email = Invoke-RestMethod -Method Post -Uri http://localhost:8000/notifications -Headers $headers -ContentType 'application/json' -Body $body
Invoke-RestMethod -Uri "http://localhost:8000/notifications/$($email.id)" -Headers $headers
```

Optional `scheduled_at` accepts an ISO 8601 timestamp with a timezone, such as `2030-01-01T09:00:00+05:30`. Past timestamps send as soon as the dispatcher picks them up. Each request addresses one recipient to keep delivery status independent and recipient addresses private. Submit one request per recipient for batches.

| Endpoint | Purpose |
| --- | --- |
| `POST /notifications` | Save an email; return 202 with its ID and status |
| `GET /notifications/{id}` | Read status and attempt count |
| `GET /notifications?limit=50&offset=0` | Paginated notification history |
| `POST /notifications/{id}/cancel` | Cancel a pending or retrying email |
| `GET /health/live` | Public process liveness |
| `GET /health/ready` | Authenticated database and Redis readiness |

The Swagger **Authorize** button accepts `X-API-Key`. Status timestamps are stored and returned as UTC. Email bodies are not returned by the status API. This is a trusted internal service: the single API key grants access to all notification records.

## Delivery behavior

1. The API validates the payload and commits it to the database before acknowledging it.
2. Beat dispatches up to 100 due records every 60 seconds. Future emails stay in the database rather than occupying long-running Redis ETA tasks.
3. A worker atomically claims a record for five minutes. Concurrent duplicate jobs cannot claim an active email.
4. SMTP acceptance marks the record `sent`. Temporary errors retry with exponential backoff and jitter; permanent SMTP 5xx errors fail immediately. The default limit is five attempts.
5. Expired claims are recovered after worker interruption. Cancelled and completed records are ignored.

Retries live in the durable outbox rather than Celery's retry state. Run exactly one Beat scheduler; multiple workers are supported. Redis outages leave accepted requests safely pending in PostgreSQL. The dispatcher recovers them when the broker returns. Worker task limits (90 seconds soft, 120 hard) stay below the claim lease; use Linux workers.

SMTP does not guarantee exactly-once delivery: a worker may crash after SMTP acceptance but before recording success, or the server may accept a message before a timeout. Such retries can duplicate delivery. A stable Message-ID helps correlation but does not guarantee deduplication. `sent` means the SMTP server accepted the email, not that it reached an inbox. Bounce/complaint tracking requires provider-specific webhooks and is not implemented.

## Use a real SMTP provider

For Compose, change the SMTP overrides under `x-app.environment` in `compose.yaml` to read your `.env` values, or remove those six SMTP overrides. Set `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`, and `SMTP_SECURITY` in `.env`. Use `starttls` (usually port 587) or `ssl` (usually port 465) as required by your provider; `none` is for local testing. Verify the sender domain with the provider and configure its DNS records.

## Development and tests

Python 3.11+ is required. On Windows use Docker/WSL for Celery workers.

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
.\.venv\Scripts\python -m pytest -q
```

Tests use an isolated temporary SQLite database and mocked SMTP/broker calls. For a manual end-to-end check, start Compose, submit the example, poll until `sent`, and check Mailpit. Run database initialization once before starting workers outside Compose:

```sh
python -m app.database
uvicorn app.main:app --reload
celery -A app.celery_app:celery worker --loglevel=info
celery -A app.celery_app:celery beat --loglevel=info
```

For production, add schema migrations before modifying the database schema, use managed secrets and unique database credentials, put the API behind TLS and rate limiting, and establish email retention and worker/queue monitoring. This initial version creates tables automatically; it does not migrate existing tables. No provider credentials are included.

Implementation references: [Celery Redis transport](https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/redis.html) and [FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/).
