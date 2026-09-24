from django.contrib import admin

from . import models


@admin.register(models.SupervisorAssignment)
class SupervisorAssignmentAdmin(admin.ModelAdmin):
    list_display = ["scholar", "faculty", "kind", "start_date", "approved_on", "end_date"]
    list_filter = ["kind"]
    search_fields = ["scholar__prn", "scholar__name", "faculty__name"]
    readonly_fields = ["approved_on", "approval"]


@admin.register(models.TACMembership)
class TACMembershipAdmin(admin.ModelAdmin):
    list_display = ["scholar", "faculty", "kind", "start_date", "approved_on", "end_date"]
    list_filter = ["kind"]
    search_fields = ["scholar__prn", "faculty__name"]
    readonly_fields = ["approved_on", "approval"]
