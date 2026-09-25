"""Identity models are READ-ONLY in the Django admin, including for
superusers. Grants and person changes go through identity.services, which
authorise the actor and write the audit trail."""
from django.contrib import admin

from .models import AuditEvent, CapabilityAssignment, Person


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Person)
class PersonAdmin(ReadOnlyAdmin):
    list_display = ["full_name", "email", "user", "faculty_profile", "scholar_profile", "is_active"]
    search_fields = ["full_name", "email"]


@admin.register(CapabilityAssignment)
class CapabilityAssignmentAdmin(ReadOnlyAdmin):
    list_display = ["person", "capability", "scope_type", "valid_from", "valid_to", "revoked_at", "granted_by"]
    list_filter = ["capability", "scope_type"]


@admin.register(AuditEvent)
class AuditEventAdmin(ReadOnlyAdmin):
    list_display = ["created_at", "actor", "action", "resource_type", "resource_id", "allowed"]
    list_filter = ["allowed", "action"]
