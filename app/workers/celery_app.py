from celery import Celery
from celery.utils.log import get_task_logger

from app.config import settings
from app.core.logging import get_logger

# Module-level logger for non-task context (startup, config)
logger = get_logger("app.workers.celery")

# Task logger — use this inside @celery_app.task functions
task_logger = get_task_logger(__name__)

celery_app = Celery(
    "pratikshya_workers",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)

logger.info(
    "Celery configured broker=%s backend=%s",
    settings.CELERY_BROKER_URL,
    settings.CELERY_RESULT_BACKEND,
)
