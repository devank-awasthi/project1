from celery import Celery
from celery.schedules import crontab

from app.config import settings

celery_app = Celery("vishanti", broker=settings.REDIS_URL, backend=None, include=["app.worker"])

celery_app.conf.update(
    task_acks_late=True,  # a task is only removed once it finished; a crash means redelivery
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    timezone="UTC",
    # Redis redelivers unacknowledged messages after visibility_timeout. ETA
    # tasks sit unacknowledged until their ETA, so with the 1-hour default a
    # 48-hour timer would be delivered many times. Our tasks are idempotent
    # anyway, but a long timeout avoids pointless duplicates.
    broker_transport_options={"visibility_timeout": 60 * 60 * 24 * 8},
    task_always_eager=settings.CELERY_ALWAYS_EAGER,
    beat_schedule={
        "sla-sweep": {"task": "vishanti.sweep", "schedule": 60.0},
        "sla-warn": {"task": "vishanti.warn_sla", "schedule": 60.0},
        "verify-chains": {"task": "vishanti.verify_chains", "schedule": float(settings.VERIFY_INTERVAL_SECONDS)},
        "dashboard-refresh": {"task": "vishanti.refresh_dashboard", "schedule": 60.0},
        # 00:05 IST
        "daily-checkpoint": {"task": "vishanti.daily_checkpoint", "schedule": crontab(hour=18, minute=35)},
    },
)
