from django.contrib import admin

from .models import ImportBatch


@admin.register(ImportBatch)
class ImportBatchAdmin(admin.ModelAdmin):
    """Read-only: imports happen only through the audited import workflow."""
    list_display = ("id", "import_type", "status", "created_by", "created_at", "rows_total", "rows_created")
    list_filter = ("import_type", "status")

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
