from django.apps import AppConfig


class ScholarsConfig(AppConfig):
    name = "scholars"
    verbose_name = "Admissions & scholars"

    def ready(self):
        from . import services  # noqa: F401  (registers approval callbacks)
