"""Celery application factory with Flask app context support."""

from celery import Celery
from flask import Flask


def make_celery(app: Flask) -> Celery:
    """Create and configure Celery with Flask app context."""
    celery = Celery(
        app.import_name,
        broker=app.config["REDIS_URL"],
        backend=app.config["REDIS_URL"],
    )
    celery.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        result_expires=3600,
        task_context=True,
    )

    class ContextTask(celery.Task):
        def __call__(self, *args, **kwargs):
            with app.app_context():
                return self.run(*args, **kwargs)

    celery.Task = ContextTask
    return celery

