from django.test import TestCase

from core import testing as t
from documents.models import FormTemplate
from documents.services import generate


class GenerateTests(TestCase):
    def test_render_is_frozen(self):
        s = t.scholar(name="Asha Rao")
        tpl = FormTemplate.objects.create(code="TEST-1", title="Test", body="Scholar {{ scholar.name }} ({{ scholar.prn }})")
        doc = generate("TEST-1", s, obj=s)
        self.assertEqual(doc.rendered, f"Scholar Asha Rao ({s.prn})")
        tpl.body = "changed"
        tpl.save()
        doc.refresh_from_db()
        self.assertIn("Asha Rao", doc.rendered)
        self.assertEqual(doc.obj, s)
