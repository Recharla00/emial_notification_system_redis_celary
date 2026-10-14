# Follow one email through the logs

Rebuild to load these source changes:

```powershell
docker compose up -d --build
docker compose logs -f --tail 100 api beat worker
```

Application logs contain UTC timestamps, severity, module, process, and event details. Add `APP_LOG_LEVEL=DEBUG` to `.env` for SMTP connection stages and status reads, then run `docker compose up -d --force-recreate api worker beat`. Leave Celery's own log level at INFO to avoid unnecessary library debug output.

The current Beat schedule is 60 seconds. Submit an email in Swagger and copy the returned notification ID.

| Event | Meaning |
| --- | --- |
| `EMAIL_SAVED` | API committed the notification to the database |
| `TASK_PUBLISHED task=app.tasks.dispatch_due` | Beat published a dispatcher job to Redis |
| `TASK_RECEIVED` | Worker received a job from Redis |
| `TASK_STARTED` | Worker began executing the task |
| `DISPATCH found=...` | Dispatcher queried due database records |
| `TASK_PUBLISHED task=app.tasks.deliver` | A delivery job was published to Redis |
| `ENQUEUED` | Links the notification ID to its delivery task ID |
| `DELIVERY starting` | Delivery function started; may skip an ineligible record |
| `SMTP sending` | Claim and attempt counter were committed |
| `SMTP_ACCEPTED` | SMTP accepted the message, before the status update |
| `DELIVERY recorded ... status=sent` | Database status update committed |
| `DELIVERY_ERROR` | Error class, attempt count, intended status and retry time |
| `TASK_FINISHED` | Celery function execution ended |

`TASK_FINISHED celery_state=SUCCESS` does not guarantee email delivery: the function can successfully record a retry or failure. Use `DELIVERY recorded` or the status API for email status. Broker publish logs confirm publication, not permanent retention or delivery. Very fast workers can emit receive logs before the producer emits its publish log.

Filter by notification ID:

```powershell
docker compose logs --since 10m api worker | Select-String 'PASTE_NOTIFICATION_ID'
```

Use the `task_id` from ENQUEUED to find matching publish/receive/start/finish lines. Dispatcher jobs have their own task IDs, separate from delivery jobs.

Custom application logs omit API keys, idempotency keys, SMTP credentials, recipient addresses, subjects and bodies. They log exception class names rather than raw SMTP/database errors. Framework logs are configured independently; do not enable SQL echo or SMTP protocol debugging when handling private messages.

To inspect waiting jobs without removing them:

```powershell
docker compose exec redis redis-cli LLEN celery
docker compose exec redis redis-cli LRANGE celery 0 9
```

Workers may consume jobs before you inspect Redis. See DEBUGGING.md for the interactive pause-and-step walkthrough; use the currently configured 60-second Beat interval when waiting for a dispatcher.
