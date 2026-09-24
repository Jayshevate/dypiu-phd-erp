from django.contrib import admin

from . import models


@admin.register(models.Semester)
class SemesterAdmin(admin.ModelAdmin):
    list_display = ["code", "start_date", "end_date", "fee_deadline"]


class AssignmentInline(admin.TabularInline):
    model = models.TAAssignment
    extra = 0


class FeedbackInline(admin.TabularInline):
    model = models.TAFeedback
    extra = 0


@admin.register(models.TARegistration)
class TARegistrationAdmin(admin.ModelAdmin):
    list_display = ["scholar", "semester", "form_submitted_on", "state"]
    list_filter = ["semester", "state"]
    inlines = [AssignmentInline, FeedbackInline]


@admin.register(models.GrantClaim)
class GrantClaimAdmin(admin.ModelAdmin):
    list_display = ["scholar", "event_name", "event_date", "amount_claimed", "amount_approved", "state"]
    list_filter = ["state"]
    readonly_fields = ["state", "approval"]
