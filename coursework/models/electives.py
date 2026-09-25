"""Elective catalogue = Course(category=ELECTIVE). A scholar's elective choice
goes through an ElectiveProposal before enrollment."""
from django.db import models
from django.db.models import Q
from django.utils import timezone


class ElectiveProposal(models.Model):
    class Status(models.TextChoices):
        SUBMITTED = "SUBMITTED", "Submitted"
        SUPERVISOR_RECOMMENDED = "SUPERVISOR_RECOMMENDED", "Recommended by supervisor"
        SUPERVISOR_NOT_RECOMMENDED = "SUPERVISOR_NOT_RECOMMENDED", "Not recommended by supervisor"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"
        WITHDRAWN = "WITHDRAWN", "Withdrawn"

    scholar = models.ForeignKey("scholars.Scholar", on_delete=models.PROTECT, related_name="elective_proposals")
    semester = models.ForeignKey("coursework.Semester", on_delete=models.PROTECT, related_name="+")
    course = models.ForeignKey("coursework.Course", null=True, blank=True, on_delete=models.PROTECT,
                               related_name="+", help_text="An elective already in the catalogue")
    proposed_code = models.CharField(max_length=20, blank=True, help_text="For a course not yet in the catalogue")
    proposed_title = models.CharField(max_length=200, blank=True)
    credits = models.PositiveSmallIntegerField()
    justification = models.TextField()
    research_relevance = models.TextField(blank=True)
    status = models.CharField(max_length=26, choices=Status.choices, default=Status.SUBMITTED)
    supervisor_by = models.ForeignKey("identity.Person", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    supervisor_at = models.DateTimeField(null=True, blank=True)
    supervisor_remarks = models.TextField(blank=True)
    decided_by = models.ForeignKey("identity.Person", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    decided_as = models.CharField(max_length=30, blank=True, help_text="Capability used for the decision")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_remarks = models.TextField(blank=True)
    submitted_by = models.ForeignKey("identity.Person", on_delete=models.PROTECT, related_name="+")
    submitted_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.CheckConstraint(name="elective_proposal_credits", condition=Q(credits__gt=0)),
            models.CheckConstraint(name="elective_proposal_target",
                                   condition=Q(course__isnull=False) | ~Q(proposed_title="")),
        ]

    @property
    def owner_scholar(self):
        return self.scholar


class ElectiveProposalEvent(models.Model):
    proposal = models.ForeignKey(ElectiveProposal, on_delete=models.PROTECT, related_name="events")
    action = models.CharField(max_length=30)
    actor = models.ForeignKey("identity.Person", on_delete=models.PROTECT, related_name="+")
    capability = models.CharField(max_length=30)
    remarks = models.TextField(blank=True)
    at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["at", "id"]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise PermissionError("proposal history is append-only")
        super().save(*args, **kwargs)
