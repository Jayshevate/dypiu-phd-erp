from datetime import date

from django.contrib.contenttypes.models import ContentType
from django.template import Context, Template

from .models import FormTemplate, GeneratedDocument


def generate(code: str, scholar, obj=None, user=None) -> GeneratedDocument:
    template = FormTemplate.objects.get(code=code, active=True)
    rendered = Template(template.body).render(Context({"scholar": scholar, "obj": obj, "today": date.today()}))
    doc = GeneratedDocument(template=template, scholar=scholar, rendered=rendered, created_by=user)
    if obj is not None:
        doc.content_type = ContentType.objects.get_for_model(obj)
        doc.object_id = obj.pk
    doc.save()
    return doc
