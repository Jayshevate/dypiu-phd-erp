from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone

from phd_rules import policy

from .roles import Role


class Designation(models.TextChoices):
    ASSISTANT_PROFESSOR = "ASSISTANT_PROFESSOR", "Assistant Professor"
    ASSOCIATE_PROFESSOR = "ASSOCIATE_PROFESSOR", "Associate Professor"
    PROFESSOR = "PROFESSOR", "Professor"
    OTHER = "OTHER", "Other / External"


class School(models.Model):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=200)

    def __str__(self):
        return self.name


class Department(models.Model):
    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name="departments")
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=200)

    def __str__(self):
        return self.name


class Faculty(models.Model):
    """Anyone who can supervise, sit on a TAC or committee, or examine."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    name = models.CharField(max_length=200)
    email = models.EmailField(blank=True)
    designation = models.CharField(max_length=30, choices=Designation.choices)
    department = models.ForeignKey(Department, null=True, blank=True, on_delete=models.PROTECT)
    affiliation = models.CharField(max_length=255, blank=True, help_text="For external faculty")
    is_external = models.BooleanField(default=False)
    superannuation_date = models.DateField(null=True, blank=True)
    capacity_override = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="Only with a recorded DC/VC approval"
    )

    class Meta:
        verbose_name_plural = "faculty"
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def supervision_capacity(self) -> int:
        if self.capacity_override is not None:
            return self.capacity_override
        return policy.SUPERVISOR_CAPACITY.get(self.designation, 0)


class CommitteeType(models.TextChoices):
    DC = "DC", "Doctoral Committee"
    SDRC = "SDRC", "School Doctoral Research Committee"
    DPEP = "DPEP", "Dissertation Presentation Evaluation Panel"
    EXAM = "EXAM", "Examination Committee"
    PROPOSAL = "PROPOSAL", "Research Proposal Evaluation Committee"


class Committee(models.Model):
    type = models.CharField(max_length=10, choices=CommitteeType.choices)
    name = models.CharField(max_length=200)
    school = models.ForeignKey(School, null=True, blank=True, on_delete=models.PROTECT)
    tenure_start = models.DateField(null=True, blank=True)
    tenure_end = models.DateField(null=True, blank=True)

    def __str__(self):
        return f"{self.get_type_display()}: {self.name}"


class CommitteeMembership(models.Model):
    class MemberRole(models.TextChoices):
        CHAIR = "CHAIR", "Chair"
        CONVENER = "CONVENER", "Convener"
        MEMBER = "MEMBER", "Member"
        EXTERNAL = "EXTERNAL", "External Expert"

    committee = models.ForeignKey(Committee, on_delete=models.CASCADE, related_name="memberships")
    faculty = models.ForeignKey(Faculty, on_delete=models.PROTECT)
    role = models.CharField(max_length=10, choices=MemberRole.choices, default=MemberRole.MEMBER)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["committee", "faculty"], name="uniq_committee_member")]


# --- Configurable approval chains --------------------------------------------


class ApprovalChain(models.Model):
    """An ordered list of role sign-offs, looked up by ``code``.

    A chain may be specialised per scholar category; lookup falls back to the
    chain with a blank category. Chains are data, editable in the admin, so
    an amended regulation does not need a code change."""

    code = models.CharField(max_length=60)
    scholar_category = models.CharField(max_length=30, blank=True, help_text="Blank = all categories")
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["code", "scholar_category"], name="uniq_chain_code_category")
        ]

    def __str__(self):
        suffix = f" [{self.scholar_category}]" if self.scholar_category else ""
        return f"{self.code}{suffix}"


class ApprovalStep(models.Model):
    chain = models.ForeignKey(ApprovalChain, on_delete=models.CASCADE, related_name="steps")
    order = models.PositiveSmallIntegerField()
    role = models.CharField(max_length=30, choices=Role.choices)
    own_scholar_only = models.BooleanField(
        default=False,
        help_text="Approver must be this scholar's own supervisor/co-supervisor/TAC member",
    )
    label = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["chain", "order"]
        constraints = [models.UniqueConstraint(fields=["chain", "order"], name="uniq_step_order")]

    def __str__(self):
        return f"{self.chain.code} #{self.order}: {self.get_role_display()}"


class ApprovalStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    APPROVED = "APPROVED", "Approved"
    REJECTED = "REJECTED", "Rejected"
    WITHDRAWN = "WITHDRAWN", "Withdrawn"


class ApprovalRequest(models.Model):
    chain = models.ForeignKey(ApprovalChain, on_delete=models.PROTECT)
    scholar = models.ForeignKey("scholars.Scholar", null=True, blank=True, on_delete=models.CASCADE,
                                related_name="approval_requests")
    content_type = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.CASCADE)
    object_id = models.PositiveBigIntegerField(null=True, blank=True)
    target = GenericForeignKey("content_type", "object_id")
    summary = models.CharField(max_length=255)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    status = models.CharField(max_length=10, choices=ApprovalStatus.choices, default=ApprovalStatus.PENDING)
    current_step = models.PositiveSmallIntegerField(default=1)
    created_at = models.DateTimeField(default=timezone.now)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.chain.code}: {self.summary} ({self.status})"

    @property
    def pending_step(self):
        if self.status != ApprovalStatus.PENDING:
            return None
        return self.chain.steps.filter(order=self.current_step).first()


class ApprovalDecision(models.Model):
    request = models.ForeignKey(ApprovalRequest, on_delete=models.CASCADE, related_name="decisions")
    step = models.ForeignKey(ApprovalStep, on_delete=models.PROTECT)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    approved = models.BooleanField()
    remarks = models.TextField(blank=True)
    decided_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["decided_at"]
