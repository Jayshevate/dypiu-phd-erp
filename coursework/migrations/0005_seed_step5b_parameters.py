"""Seed version 1 of the Step 5B academic parameters (frozen copy)."""
import datetime

from django.db import migrations

PARAMETERS = [['result.absence_policy',
  None,
  'UNRESOLVED',
  'Not stated. The only documented notion is the special grade AB (Brief §5)',
  ''],
 ['attendance.basis',
  'PER_COURSE',
  'AMBIGUOUS',
  "React journeyData.ts (Regulations July 2026 transcription): 'min 75% attendance' — per course or overall "
  'is not stated',
  'RD-26'],
 ['semester_registration.required',
  True,
  'CONFIGURABLE',
  'React journeyData.ts (Regulations July 2026 transcription) M7: semester course registration every '
  'semester',
  ''],
 ['semester_registration.requires_fee_clearance',
  True,
  'CONFIGURABLE',
  'React journeyData.ts (Regulations July 2026 transcription) M7: prescribed fees paid every semester until '
  'thesis submission',
  ''],
 ['elective.list_approval_chain',
  ['DC_MEMBER', 'DEAN_RND'],
  'AMBIGUOUS',
  "React journeyData.ts (Regulations July 2026 transcription) M9.1: 'Elective list approved jointly by DC "
  "and Dean of Research' (order not stated)",
  'RD-38'],
 ['elective.proposal_requires_approved_list',
  True,
  'CONFIGURABLE',
  'React journeyData.ts (Regulations July 2026 transcription) M9.1: electives chosen from the approved list',
  'RD-38'],
 ['question_paper.setter_appointed_by',
  ['DC_MEMBER'],
  'CONFIGURABLE',
  "React journeyData.ts (Regulations July 2026 transcription) M11: 'DC appoints experts to set the question "
  "paper'",
  ''],
 ['revaluation.enabled', None, 'UNRESOLVED', 'No revaluation provision in the supplied sources', ''],
 ['revaluation.request_window_days', None, 'UNRESOLVED', 'Not stated', ''],
 ['revaluation.reviewer', None, 'UNRESOLVED', 'Not stated', ''],
 ['activity.scoring_scheme.INDUSTRIAL_TRAINING',
  None,
  'UNRESOLVED',
  "Requirements brief (Django docs/REQUIREMENTS.md): '5-component rubric incl. poster' — components and "
  'weights not given',
  'RD-37'],
 ['activity.scoring_scheme.CONFERENCE_WORKSHOP',
  None,
  'UNRESOLVED',
  "Requirements brief (Django docs/REQUIREMENTS.md): 'points-accumulation matrix + reflective-note gate' — "
  'matrix not given',
  'RD-37'],
 ['activity.scoring_scheme.RESEARCH_SEMINAR',
  None,
  'UNRESOLVED',
  "Requirements brief (Django docs/REQUIREMENTS.md): '3-criteria rubric' — criteria and weights not given",
  'RD-37']]


def seed(apps, schema_editor):
    Param = apps.get_model("coursework", "AcademicRuleParameter")
    for key, value, status, source, reference in PARAMETERS:
        if not Param.objects.filter(key=key).exists():
            Param.objects.create(key=key, version=1, value=value, status=status, source=source,
                                 reference=reference, effective_from=datetime.date(2026, 7, 1),
                                 change_reason="Initial seed (Step 5B)")


class Migration(migrations.Migration):
    dependencies = [("coursework", "0004_academic_hardening")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
