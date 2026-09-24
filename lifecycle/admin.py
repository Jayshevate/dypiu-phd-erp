from django.contrib import admin

from . import models


@admin.register(models.PhaseTransition)
class PhaseTransitionAdmin(admin.ModelAdmin):
    list_display = ["scholar", "from_phase", "to_phase", "from_status", "to_status", "actor", "at"]
    readonly_fields = [f.name for f in models.PhaseTransition._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(models.ResearchProposal)
class ResearchProposalAdmin(admin.ModelAdmin):
    list_display = ["scholar", "attempt_no", "title", "submitted_on", "outcome"]
    list_filter = ["outcome"]


@admin.register(models.ProgressReport)
class ProgressReportAdmin(admin.ModelAdmin):
    list_display = ["scholar", "period_no", "due_date", "submitted_on", "outcome"]
    list_filter = ["outcome"]


@admin.register(models.DCReview)
class DCReviewAdmin(admin.ModelAdmin):
    list_display = ["scholar", "reason", "opened_on", "decision"]


@admin.register(models.Synopsis)
class SynopsisAdmin(admin.ModelAdmin):
    list_display = ["scholar", "submitted_on", "open_seminar_date", "tac_cleared_on"]


class NominationInline(admin.TabularInline):
    model = models.ExaminerNomination
    extra = 0


@admin.register(models.Thesis)
class ThesisAdmin(admin.ModelAdmin):
    list_display = ["scholar", "title", "submitted_on", "plagiarism_percent", "fees_paid"]
    inlines = [NominationInline]


@admin.register(models.ExaminerReport)
class ExaminerReportAdmin(admin.ModelAdmin):
    list_display = ["examiner", "dispatched_on", "reminded_on", "received_on", "recommendation"]


@admin.register(models.Viva)
class VivaAdmin(admin.ModelAdmin):
    list_display = ["thesis", "scheduled_on", "candidate_notified_on", "invitation_published_on", "outcome"]


@admin.register(models.DegreeAward)
class DegreeAwardAdmin(admin.ModelAdmin):
    list_display = ["scholar", "approved_on", "certificate_no", "issued_on", "academic_council_notified_on"]
    readonly_fields = ["approved_on", "approval"]
