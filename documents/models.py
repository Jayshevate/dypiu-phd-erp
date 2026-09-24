from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone


class FormTemplate(models.Model):
    """One of the official appendix forms/letters. ``body`` is a Django
    template rendered with ``scholar``, ``obj`` (the workflow record the form
    belongs to) and ``today``. Official wording must be copied from the
    Regulations appendices by the R&D office."""

    code = models.CharField(max_length=40, unique=True, help_text="e.g. APPX-12")
    title = models.CharField(max_length=255)
    phase = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Lifecycle phase it belongs to")
    body = models.TextField()
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} {self.title}"


class GeneratedDocument(models.Model):
    template = models.ForeignKey(FormTemplate, on_delete=models.PROTECT)
    scholar = models.ForeignKey("scholars.Scholar", on_delete=models.CASCADE, related_name="documents")
    content_type = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.SET_NULL)
    object_id = models.PositiveBigIntegerField(null=True, blank=True)
    obj = GenericForeignKey("content_type", "object_id")
    rendered = models.TextField(help_text="Frozen at generation time; never re-rendered")
    signed_copy = models.FileField(upload_to="documents/signed/", blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]
