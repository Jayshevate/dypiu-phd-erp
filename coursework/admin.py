from django.contrib import admin

from . import models


class ComponentInline(admin.TabularInline):
    model = models.AssessmentComponent
    extra = 1


@admin.register(models.Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ["code", "title", "credits", "category", "required_for_all", "has_ethics_submodule"]
    inlines = [ComponentInline]


class ScoreInline(admin.TabularInline):
    model = models.ComponentScore
    extra = 0


@admin.register(models.CourseAttempt)
class CourseAttemptAdmin(admin.ModelAdmin):
    list_display = ["scholar", "course", "attempt_no", "exam_date", "marks", "grade", "grade_point", "ethics_cleared"]
    list_filter = ["course", "grade"]
    search_fields = ["scholar__prn", "scholar__name"]
    readonly_fields = ["grade", "grade_point"]
    inlines = [ScoreInline]
