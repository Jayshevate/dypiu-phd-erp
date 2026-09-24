from django.apps import AppConfig


class SupervisionConfig(AppConfig):
    name = "supervision"
    verbose_name = "Supervisors & TAC"

    def ready(self):
        from . import services  # noqa: F401
