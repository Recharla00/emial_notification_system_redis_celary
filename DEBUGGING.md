# Watch Beat, Redis, and the worker

Use this walkthrough with local test emails and Mailpit. Run commands from the project directory in PowerShell. Do not copy Markdown fences into the terminal.

## 1. Build the logging and debugger changes

```powershell
docker compose up -d --build
docker compose logs -f beat worker
```

Beat logs `Scheduler: Sending due task` and `PUBLISHED task=app.tasks.dispatch_due`. Worker logs show the dispatcher being received, `DISPATCH found=...`, `PUBLISHED task=app.tasks.deliver`, and `ENQUEUED notification_id=... task_id=...`. Delivery logs show `SMTP sending` and `DELIVERY recorded ... status=sent`.

Match the task ID in the publisher log with the worker's `Task ... received` log. The notification ID is the database record ID; it is different from the Celery task ID. Multiple task IDs can refer to the same notification on later dispatches.

Ctrl+C stops following logs without stopping containers. These INFO logs do not print credentials, recipients, or email bodies. They use Celery's [publish signal](https://docs.celeryq.dev/en/stable/userguide/signals.html#after-task-publish).

## 2. Watch Beat put a dispatcher in Redis

Stop the normal worker and Beat first. This pauses email processing in this local stack:

```powershell
docker compose stop -t 130 worker beat
```

Submit one test notification in Swagger with a new idempotency key. The API saves it as pending, even though the worker and Beat are stopped.

Start Beat alone:

```powershell
docker compose start beat
docker compose logs -f --tail 10 beat
```

Wait for `PUBLISHED task=app.tasks.dispatch_due`, then press Ctrl+C and stop Beat so dispatcher jobs do not keep accumulating:

```powershell
docker compose stop beat
docker compose exec redis redis-cli LLEN celery
docker compose exec redis redis-cli LRANGE celery 0 9
```

The messages include `headers.task` and `headers.id`. At this point you should see `app.tasks.dispatch_due`; older jobs may also be present. The message body is encoded by Celery. Beat has not queried PostgreSQL or created email delivery jobs: that happens when a worker runs the dispatcher. Do not LPOP this queue: doing so would remove real work. Use `demo:messages` for manual push/pop practice.

## 3. Step through the worker interactively

Keep normal workers and Beat stopped. In terminal A run:

```powershell
docker compose run --rm --no-deps worker python -m app.debug_worker
```

This starts a temporary worker under Python's built-in debugger. It uses the same database and Redis as your application. Type `c` at the first `(Pdb)` prompt to start the worker. It will pause at the first line of `dispatch_due()` or `deliver()`, depending on existing queued work.

| Debugger command | Meaning |
| --- | --- |
| `l` | Show nearby source lines |
| `n` | Execute the current line and pause at the next line |
| `p ids` | Show notification IDs after the database query in dispatch_due |
| `p notification_id` | Show the email ID inside deliver |
| `p job.id` | Show the task ID after deliver.delay has executed |
| `c` | Continue to the next breakpoint |
| `q` | Quit the debugger |

At the dispatcher breakpoint, use `n` until the database query has executed, then `p ids`. Step over `job = deliver.delay(notification_id)`. While still paused in the dispatcher, inspect Redis from terminal B:

```powershell
docker compose exec redis redis-cli LRANGE celery 0 9
```

Now an `app.tasks.deliver` job should be waiting. Type `c` in terminal A. The dispatcher finishes and the worker consumes the delivery job, pausing at the beginning of `deliver()`.

Inspect Redis again: the delivery job has moved out of the waiting list. With late acknowledgments it is in flight until execution finishes. You can inspect the transport's unacknowledged count:

```powershell
docker compose exec redis redis-cli HLEN unacked
```

Use `p notification_id` to identify the database row. Step through the claim, database read, and send. `n` steps over `send_email(notification)`; use `s` on that line to step into SMTP code if desired. Running that line sends the test email to Mailpit. Continue with `c`, then check the database status and http://localhost:8025.

Keep only this one worker running while paused. Claims expire after five minutes; another running worker could reclaim an email after that period. The solo debug pool does not provide the normal prefork task timeout protections. Worker inspect commands may time out while paused because this worker uses one main thread. This debugger is for local learning only.

## 4. Restore normal processing

After finishing the test, stop the interactive worker with Ctrl+C (or `q` at a breakpoint) and wait for it to exit. Then:

```powershell
docker compose start worker beat
docker compose logs -f --tail 30 worker beat
```

If you quit during delivery, its claim may need five minutes to expire before recovery. If SMTP already accepted the email before interruption, retrying can produce a duplicate. For the cleanest walkthrough, continue until `DELIVERY recorded ... status=sent` before exiting.
