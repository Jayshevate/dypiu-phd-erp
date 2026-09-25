from django.contrib import admin

from . import models


@admin.register(models.University)
class UniversityAdmin(admin.ModelAdmin):
    list_display = ["code", "name"]


@admin.register(models.School)
class SchoolAdmin(admin.ModelAdmin):
    list_display = ["code", "name"]


@admin.register(models.Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "school"]
    list_filter = ["school"]


@admin.register(models.Faculty)
class FacultyAdmin(admin.ModelAdmin):
    list_display = ["name", "designation", "department", "is_external", "supervision_capacity"]
    list_filter = ["designation", "is_external", "department__school"]
    search_fields = ["name", "email"]


class MembershipInline(admin.TabularInline):
    model = models.CommitteeMembership
    extra = 1
    autocomplete_fields = ["faculty"]


@admin.register(models.Committee)
class CommitteeAdmin(admin.ModelAdmin):
    list_display = ["name", "type", "school", "tenure_start", "tenure_end"]
    list_filter = ["type"]
    inlines = [MembershipInline]


class StepInline(admin.TabularInline):
    model = models.ApprovalStep
    extra = 1


@admin.register(models.ApprovalChain)
class ApprovalChainAdmin(admin.ModelAdmin):
    list_display = ["code", "scholar_category", "name"]
    inlines = [StepInline]


class DecisionInline(admin.TabularInline):
    model = models.ApprovalDecision
    extra = 0
    readonly_fields = ["step", "decided_by", "approved", "remarks", "decided_at"]
    can_delete = False


@admin.register(models.ApprovalRequest)
class ApprovalRequestAdmin(admin.ModelAdmin):
    list_display = ["summary", "chain", "scholar", "status", "current_step", "created_at"]
    list_filter = ["status", "chain__code"]
    readonly_fields = ["status", "current_step", "closed_at"]
    inlines = [DecisionInline]
