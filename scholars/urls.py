from django.urls import path

from . import views

app_name = "scholars"
urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("<str:prn>/", views.scholar_detail, name="detail"),
]
