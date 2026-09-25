from django.apps import AppConfig


class CourseworkConfig(AppConfig):
    name = "coursework"
    verbose_name = "Academic: coursework & examinations"

    def ready(self):
        from .academic import policies

        policies.register()
