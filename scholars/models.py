from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from phd_rules import durations, policy


class Mode(models.TextChoices):
    FULL_TIME = "FT", "Full-time"
    PART_TIME = "PT", "Part-time"


class Category(models.TextChoices):
    FULL_TIME = "FT", "Full-time"
    PT_SPONSORED = "PT_SPONSORED", "Part-time: Sponsored"
    PT_SISTER = "PT_SISTER", "Part-time: Sister Institution"
    PT_EXTERNAL = "PT_EXTERNAL", "Part-time: External"
    PT_INTERNAL = "PT_INTERNAL", "Part-time: Internal"


class EntryQualification(models.TextChoices):
    BTECH = "BTECH", "B.Tech (23 credits)"
    MTECH = "MTECH", "M.Tech / M.E. / M.Pharm (17 credits)"
    PG = "PG", "Integrated / M.Sc / MCA / MBA / M.Com (20 credits)"


class Phase(models.IntegerChoices):
    ADMISSION = 1, "Admission"
    COURSEWORK = 2, "Coursework"
    SUPERVISOR_TAC = 3, "Supervisor & TAC"
    RESEARCH_PROPOSAL = 4, "Research Proposal"
    PROGRESS = 5, "Progress Monitoring"
    SYNOPSIS = 6, "Pre-submission Synopsis"
    THESIS_SUBMISSION = 7, "Thesis Submission"
    THESIS_EVALUATION = 8, "Thesis Evaluation"
    VIVA = 9, "Dissertation Presentation / Viva"
    DEGREE_AWARD = 10, "Degree Award"


class Status(models.TextChoices):
    ACTIVE = "ACTIVE", "Active"
    AWARDED = "AWARDED", "Degree awarded"
    CANCELLED = "CANCELLED", "Registration cancelled"
    WITHDRAWN = "WITHDRAWN", "Withdrawn"
    ON_BREAK = "ON_BREAK", "Career break / semester break"


class Fellowship(models.TextChoices):
    NONE = "NONE", "None"
    JRF = "JRF", "Junior Research Fellow"
    SRF = "SRF", "Senior Research Fellow"


class AdmissionCycle(models.Model):
    name = models.CharField(max_length=50, unique=True, help_text="e.g. 2026-SEP")
    vacancy_notified_on = models.DateField(null=True, blank=True)
    rpet_date = models.DateField(null=True, blank=True)

    def __str__(self):
        return self.name


class Application(models.Model):
    class Decision(models.TextChoices):
        PENDING = "PENDING", "Pending"
        SELECTED = "SELECTED", "Selected"
        REJECTED = "REJECTED", "Rejected"

    cycle = models.ForeignKey(AdmissionCycle, on_delete=models.PROTECT, related_name="applications")
    name = models.CharField(max_length=200)
    email = models.EmailField()
    category = models.CharField(max_length=20, choices=Category.choices)
    entry_qualification = models.CharField(max_length=10, choices=EntryQualification.choices)
    department = models.ForeignKey("core.Department", on_delete=models.PROTECT)
    statement_of_purpose = models.FileField(upload_to="applications/sop/", blank=True)
    cv = models.FileField(upload_to="applications/cv/", blank=True)
    exemption = models.CharField(max_length=20, blank=True, help_text="GATE / NET / CSIR ... if exempt from RPET")
    rpet_percent = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True,
                                       validators=[MinValueValidator(0), MaxValueValidator(100)])
    interview_percent = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True,
                                            validators=[MinValueValidator(0), MaxValueValidator(100)])
    decision = models.CharField(max_length=10, choices=Decision.choices, default=Decision.PENDING)

    def __str__(self):
        return f"{self.name} ({self.cycle})"

    @property
    def rpet_cleared(self) -> bool:
        if self.exemption and self.exemption.upper() in policy.RPET_EXEMPTIONS:
            return True
        return self.rpet_percent is not None and self.rpet_percent >= policy.RPET_PASS_PERCENT

    @property
    def interview_cleared(self) -> bool:
        return self.interview_percent is not None and self.interview_percent >= policy.INTERVIEW_PASS_PERCENT

    @property
    def eligible_for_selection(self) -> bool:
        return self.rpet_cleared and self.interview_cleared


class Scholar(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    application = models.OneToOneField(Application, null=True, blank=True, on_delete=models.SET_NULL)
    prn = models.CharField("PRN", max_length=30, unique=True)
    name = models.CharField(max_length=200)
    email = models.EmailField(blank=True)
    gender = models.CharField(max_length=1, choices=[("F", "Female"), ("M", "Male"), ("O", "Other")])
    pwd_percent = models.PositiveSmallIntegerField("PwD %", default=0, validators=[MaxValueValidator(100)])
    category = models.CharField(max_length=20, choices=Category.choices)
    entry_qualification = models.CharField(max_length=10, choices=EntryQualification.choices)
    department = models.ForeignKey("core.Department", on_delete=models.PROTECT)
    research_area = models.CharField(max_length=255, blank=True)

    admission_date = models.DateField()
    registration_date = models.DateField(null=True, blank=True, help_text="Provisional registration")
    registration_fee_paid = models.BooleanField(default=False)
    sponsorship_letter = models.FileField(upload_to="scholars/sponsorship/", blank=True,
                                          help_text="Required for part-time sponsored / sister-institution")

    fellowship = models.CharField(max_length=4, choices=Fellowship.choices, default=Fellowship.NONE)
    fellowship_start = models.DateField(null=True, blank=True)

    phase = models.PositiveSmallIntegerField(choices=Phase.choices, default=Phase.ADMISSION)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    coursework_completed_on = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["prn"]

    def __str__(self):
        return f"{self.prn} {self.name}"

    @property
    def mode(self) -> str:
        return Mode.FULL_TIME if self.category == Category.FULL_TIME else Mode.PART_TIME

    @property
    def relaxation_eligible(self) -> bool:
        return durations.relaxation_eligible(self.gender, self.pwd_percent)

    def has_extension(self, kind) -> bool:
        return self.extensions.filter(kind=kind, status=ExtensionGrant.State.GRANTED).exists()

    @property
    def programme_ceiling(self):
        if not self.registration_date:
            return None
        return durations.programme_ceiling(
            self.registration_date,
            re_registered=self.has_extension(ExtensionGrant.Kind.RE_REGISTRATION),
            relaxation_granted=self.has_extension(ExtensionGrant.Kind.RELAXATION),
        )

    @property
    def stipend(self) -> Decimal:
        return {Fellowship.JRF: policy.JRF_STIPEND, Fellowship.SRF: policy.SRF_STIPEND}.get(self.fellowship, Decimal(0))


class ExtensionGrant(models.Model):
    class Kind(models.TextChoices):
        SYNOPSIS = "SYNOPSIS", "Synopsis +1 year"
        THESIS_SUBMISSION = "THESIS_SUBMISSION", "Thesis submission +3 months"
        RE_REGISTRATION = "RE_REGISTRATION", "Re-registration +2 years"
        RELAXATION = "RELAXATION", "Female / PwD relaxation +2 years"

    class State(models.TextChoices):
        REQUESTED = "REQUESTED", "Requested"
        GRANTED = "GRANTED", "Granted"
        REFUSED = "REFUSED", "Refused"

    CHAIN_FOR_KIND = {
        Kind.SYNOPSIS: "SYNOPSIS_EXTENSION",
        Kind.THESIS_SUBMISSION: "THESIS_SUBMISSION_EXTENSION",
        Kind.RE_REGISTRATION: "RE_REGISTRATION",
        Kind.RELAXATION: "DURATION_RELAXATION",
    }

    scholar = models.ForeignKey(Scholar, on_delete=models.CASCADE, related_name="extensions")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    reason = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=State.choices, default=State.REQUESTED)
    approval = models.OneToOneField("core.ApprovalRequest", null=True, blank=True, on_delete=models.SET_NULL)
    requested_on = models.DateField(auto_now_add=True)

    class Meta:
        constraints = [
            # Each extension can be granted once per scholar.
            models.UniqueConstraint(fields=["scholar", "kind"], condition=models.Q(status="GRANTED"),
                                    name="uniq_granted_extension")
        ]

    def __str__(self):
        return f"{self.scholar.prn}: {self.get_kind_display()} ({self.status})"


class LeaveRecord(models.Model):
    class Type(models.TextChoices):
        ANNUAL = "ANNUAL", "Annual leave"
        MATERNITY = "MATERNITY", "Maternity leave"
        CAREER_BREAK = "CAREER_BREAK", "Career break"
        SEMESTER_BREAK = "SEMESTER_BREAK", "Semester break"

    scholar = models.ForeignKey(Scholar, on_delete=models.CASCADE, related_name="leaves")
    type = models.CharField(max_length=15, choices=Type.choices)
    start_date = models.DateField()
    end_date = models.DateField()
    reason = models.TextField(blank=True)
    approved = models.BooleanField(default=False)
    approval = models.OneToOneField("core.ApprovalRequest", null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ["-start_date"]

    @property
    def days(self) -> int:
        return (self.end_date - self.start_date).days + 1
