#!/bin/sh
# One-container start for hosts without separate background workers
# (e.g. Render's free web service): schema + demo data, then the Celery
# worker with embedded beat in the background, then the API in front.
set -e

python -m app.init_db ${SEED_DEMO:+--demo}

# Keep the worker alive: if it ever exits, restart it. Escalation is
# idempotent, and the sweeper catches up on anything it missed.
(
  while true; do
    celery -A app.celery_app worker -B --pool=solo --loglevel=INFO -s /tmp/celerybeat-schedule || true
    echo "celery exited, restarting in 5s" >&2
    sleep 5
  done
) &

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --workers "${WEB_CONCURRENCY:-1}" \
  --no-access-log --proxy-headers --forwarded-allow-ips='*'
