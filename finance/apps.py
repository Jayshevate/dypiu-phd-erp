from django.apps import AppConfig


class FinanceConfig(AppConfig):
    name = "finance"
    verbose_name = "Teaching assistantship & grants"

    def ready(self):
        from . import services  # noqa: F401
