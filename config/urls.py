from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

admin.site.site_header = "DYPIU PhD ERP"
admin.site.site_title = "DYPIU PhD ERP"

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="scholars:dashboard", permanent=False)),
    path("admin/", admin.site.urls),
    path("scholars/", include("scholars.urls")),
    path("identity/", include("identity.urls")),
    path("api/auth/", include("identity.api_urls")),
    path("api/academic/", include("coursework.api.urls")),
]
