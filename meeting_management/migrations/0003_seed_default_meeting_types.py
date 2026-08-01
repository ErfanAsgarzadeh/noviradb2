from django.db import migrations


DEFAULT_MEETING_TYPES = [
    ("MTG_OPS_REVIEW", "Operations Review", "Routine operational coordination and issue review."),
    ("MTG_PROJECT_REVIEW", "Project Review", "Project planning, progress, risk, and decision review."),
    ("MTG_QUALITY_REVIEW", "Quality Review", "Quality, inspection, nonconformance, and corrective-action review."),
    ("MTG_MANAGEMENT", "Management Committee", "Formal management meeting with controlled minutes."),
]


def seed_default_meeting_types(apps, schema_editor):
    MeetingType = apps.get_model("meeting_management", "MeetingType")
    for code, name, description in DEFAULT_MEETING_TYPES:
        MeetingType.objects.get_or_create(
            code=code,
            defaults={
                "name": name,
                "description": description,
                "default_confidentiality": "internal",
                "requires_minutes_approval": True,
                "is_active": True,
            },
        )


def unseed_default_meeting_types(apps, schema_editor):
    MeetingType = apps.get_model("meeting_management", "MeetingType")
    MeetingType.objects.filter(code__in=[code for code, _name, _description in DEFAULT_MEETING_TYPES]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("meeting_management", "0002_meetingdecision_resolution_resolutionaction_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_default_meeting_types, unseed_default_meeting_types),
    ]
