from django.contrib import admin

from . import models


@admin.register(models.AcademicEvent)
class AcademicEventAdmin(admin.ModelAdmin):
    list_display = ["start", "end", "kind", "title", "confirmed"]
    list_filter = ["kind", "confirmed"]
    list_editable = ["confirmed"]


@admin.register(models.Deadline)
class DeadlineAdmin(admin.ModelAdmin):
    list_display = ["scholar", "title", "due_date", "met"]
    list_filter = ["kind", "met"]
    search_fields = ["scholar__prn", "scholar__name"]


@admin.register(models.Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ["recipient", "message", "tier", "created_at", "read_at"]
    list_filter = ["tier"]
