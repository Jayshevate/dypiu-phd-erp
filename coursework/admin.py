"""Academic records are READ-ONLY in the Django admin (including for
superusers). All academic mutations go through coursework.academic services,
which authorise the actor server-side and write the audit trail."""
from django.contrib import admin

from . import models


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class ReadOnlyInline(admin.TabularInline):
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


class ComponentInline(ReadOnlyInline):
    model = models.AssessmentComponent


@admin.register(models.Course)
class CourseAdmin(ReadOnlyAdmin):
    list_display = ["code", "title", "credits", "category", "activity_type", "evaluation_body", "is_active"]
    inlines = [ComponentInline]


class ScoreInline(ReadOnlyInline):
    model = models.ComponentScore


@admin.register(models.CourseAttempt)
class CourseAttemptAdmin(ReadOnlyAdmin):
    list_display = ["scholar", "course", "attempt_no", "status", "exam_date", "grade", "grade_point"]
    list_filter = ["course", "status"]
    search_fields = ["scholar__prn", "scholar__name"]
    inlines = [ScoreInline]


@admin.register(models.AcademicRuleParameter)
class AcademicRuleParameterAdmin(ReadOnlyAdmin):
    list_display = ["key", "version", "status", "effective_from", "reference", "created_by"]
    list_filter = ["status"]
    search_fields = ["key"]


@admin.register(models.CourseResult)
class CourseResultAdmin(ReadOnlyAdmin):
    list_display = ["attempt", "outcome", "grade", "status", "is_provisional", "prepared_by", "verified_by",
                    "ratified_by"]
    list_filter = ["status", "outcome", "is_provisional"]
