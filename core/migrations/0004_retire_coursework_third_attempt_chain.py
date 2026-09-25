"""The DC → VC 'COURSEWORK_THIRD_ATTEMPT' approval chain is superseded by
coursework.ThirdAttemptCase (Dean R&D review → VC decision). Existing chains
and requests are KEPT for the record; the chain is renamed so no new request
can be started against it."""
from django.db import migrations

OLD, NEW = "COURSEWORK_THIRD_ATTEMPT", "SUPERSEDED_COURSEWORK_THIRD_ATTEMPT"


def retire(apps, schema_editor):
    Chain = apps.get_model("core", "ApprovalChain")
    for chain in Chain.objects.filter(code=OLD):
        chain.code = NEW
        chain.name = f"[SUPERSEDED] {chain.name}"
        chain.description = ("Superseded by coursework.ThirdAttemptCase (Dean R&D review, then VC decision). "
                             "Kept for historical requests only. " + chain.description)
        chain.save(update_fields=["code", "name", "description"])


def restore(apps, schema_editor):
    Chain = apps.get_model("core", "ApprovalChain")
    for chain in Chain.objects.filter(code=NEW):
        chain.code = OLD
        chain.name = chain.name.replace("[SUPERSEDED] ", "")
        chain.save(update_fields=["code", "name"])


class Migration(migrations.Migration):
    dependencies = [("core", "0003_university_school_university")]
    operations = [migrations.RunPython(retire, restore)]
