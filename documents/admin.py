from django.contrib import admin

from . import models


@admin.register(models.FormTemplate)
class FormTemplateAdmin(admin.ModelAdmin):
    list_display = ["code", "title", "phase", "active"]
    list_filter = ["phase", "active"]


@admin.register(models.GeneratedDocument)
class GeneratedDocumentAdmin(admin.ModelAdmin):
    list_display = ["template", "scholar", "created_by", "created_at"]
    readonly_fields = ["rendered", "created_at"]
