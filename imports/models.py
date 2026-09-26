"""Immutable record of every institutional data import (Step A3).

A batch is created when a file is previewed (validated in a rolled-back dry run)
and becomes COMMITTED once, when the same file is imported. After that it can
never change or be deleted. It stores file metadata, the confirmed column
mapping, per-row outcomes and counts, but never the file itself and never any
password or activation token."""
from django.db import models
from django.utils import timezone


class ImportBatch(models.Model):
    class Status(models.TextChoices):
        PREVIEWED = "PREVIEWED", "Previewed (nothing written)"
        COMMITTED = "COMMITTED", "Committed"

    import_type = models.CharField(max_length=30)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PREVIEWED)
    created_by = models.ForeignKey("identity.Person", on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)

    file_name = models.CharField(max_length=255)
    file_type = models.CharField(max_length=5)
    file_size = models.PositiveIntegerField()
    file_sha256 = models.CharField(max_length=64)
    sheet_name = models.CharField(max_length=100, blank=True)
    header_row = models.PositiveIntegerField(help_text="1-based row number of the header in the sheet")

    mapping = models.JSONField(help_text="Confirmed mapping: column index (string) → field name")
    mapping_sources = models.JSONField(default=dict, help_text="How each column was mapped (exact/alias/ai/manual)")
    options = models.JSONField(default=dict)
    rows_total = models.PositiveIntegerField()
    counts = models.JSONField(default=dict, help_text="Rows per status in the preview")
    report = models.JSONField(default=list, help_text="Per-row status, key and reasons")

    committed_by = models.ForeignKey("identity.Person", null=True, blank=True, on_delete=models.PROTECT,
                                     related_name="+")
    committed_at = models.DateTimeField(null=True, blank=True)
    rows_created = models.PositiveIntegerField(default=0)
    rows_skipped = models.PositiveIntegerField(default=0)
    created_refs = models.JSONField(default=list, help_text="Model label and id of each record created")
    provisioned_person_ids = models.JSONField(default=list)
    audit_event_id = models.PositiveBigIntegerField(null=True, blank=True,
                                                    help_text="Audit event recording the commit (correlation)")

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                name="import_commit_fields",
                condition=(models.Q(status="PREVIEWED", committed_at__isnull=True)
                           | models.Q(status="COMMITTED", committed_at__isnull=False, committed_by__isnull=False)),
            ),
        ]

    def __str__(self):
        return f"Import #{self.pk} {self.import_type} ({self.status})"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            stored = type(self).objects.filter(pk=self.pk).values_list("status", flat=True).first()
            if stored == self.Status.COMMITTED:
                raise PermissionError("committed import records are immutable")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status == self.Status.COMMITTED:
            raise PermissionError("committed import records are immutable")
        return super().delete(*args, **kwargs)
