"""Session authentication for the SPA: /api/auth/..."""
from django.urls import path

from .api import CsrfView, LoginView, LogoutView

app_name = "auth_api"
urlpatterns = [
    path("csrf/", CsrfView.as_view()),
    path("login/", LoginView.as_view()),
    path("logout/", LogoutView.as_view()),
]
