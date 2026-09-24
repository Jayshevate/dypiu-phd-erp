from django.contrib import admin

from . import models


@admin.register(models.AdmissionCycle)
class AdmissionCycleAdmin(admin.ModelAdmin):
    list_display = ["name", "vacancy_notified_on", "rpet_date"]


@admin.register(models.Application)
class ApplicationAdmin(admin.ModelAdmin):
    list_display = ["name", "cycle", "category", "rpet_percent", "interview_percent", "eligible_for_selection", "decision"]
    list_filter = ["cycle", "category", "decision"]
    search_fields = ["name", "email"]

    @admin.display(boolean=True)
    def eligible_for_selection(self, obj):
        return obj.eligible_for_selection


class ExtensionInline(admin.TabularInline):
    model = models.ExtensionGrant
    extra = 0
    readonly_fields = ["status", "approval"]


class LeaveInline(admin.TabularInline):
    model = models.LeaveRecord
    extra = 0
    readonly_fields = ["approved", "approval"]


@admin.register(models.Scholar)
class ScholarAdmin(admin.ModelAdmin):
    list_display = ["prn", "name", "category", "department", "phase", "status", "fellowship", "registration_date"]
    list_filter = ["phase", "status", "category", "fellowship", "department__school"]
    search_fields = ["prn", "name", "email"]
    readonly_fields = ["phase", "status", "coursework_completed_on"]
    inlines = [ExtensionInline, LeaveInline]
