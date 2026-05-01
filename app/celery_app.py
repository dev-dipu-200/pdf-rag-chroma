from celery import Celery

from app.dependencies import get_runtime_settings

settings = get_runtime_settings()

celery_app = Celery(
    "pdf_rag_chroma",
    broker=settings.redis_url,
    backend=settings.redis_url,
)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Kolkata",
    enable_utc=False,
)
celery_app.autodiscover_tasks(["app"])
