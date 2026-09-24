from django.apps import AppConfig


class LifecycleConfig(AppConfig):
    name = "lifecycle"
    verbose_name = "Scholar lifecycle"

    def ready(self):
        from . import services  # noqa: F401
