from django.db import migrations


def repair_legacy_revision_spine(apps, schema_editor):
    Project = apps.get_model("ktcPlanning", "Project")
    Revision = apps.get_model("ktcPlanning", "Revision")
    WBSNode = apps.get_model("ktcPlanning", "WBSNode")
    WBSNodeVersion = apps.get_model("ktcPlanning", "WBSNodeVersion")
    OrgUnit = apps.get_model("CustomUser", "OrgUnit")

    planning_manager_id = OrgUnit.objects.filter(
        is_planning_unit=True,
        manager_id__isnull=False,
    ).values_list("manager_id", flat=True).first()

    for project in Project.objects.all().iterator():
        revision = (
            Revision.objects.filter(project_id=project.pk, is_deleted=False)
            .order_by("number", "created_at")
            .first()
        )
        if revision is None:
            last_number = (
                Revision.objects.filter(project_id=project.pk)
                .order_by("-number")
                .values_list("number", flat=True)
                .first()
            )
            next_number = 0 if last_number is None else last_number + 1
            if project.scope == "company":
                approver_id = planning_manager_id
            else:
                approver_id = None
                if project.owner_unit_id:
                    approver_id = OrgUnit.objects.filter(
                        pk=project.owner_unit_id
                    ).values_list("manager_id", flat=True).first()
                if not approver_id and project.created_by_id:
                    creator_unit_id = project.created_by.unit_id
                    if creator_unit_id:
                        approver_id = OrgUnit.objects.filter(
                            pk=creator_unit_id
                        ).values_list("manager_id", flat=True).first()
            approver_id = approver_id or project.created_by_id
            revision = Revision.objects.create(
                project_id=project.pk,
                number=next_number,
                description="Initial Automatic Base Version (legacy repair)",
                is_baseline=True,
                created_by_id=project.created_by_id,
                designated_approver_id=approver_id,
                project_start=project.start_date or project.created_at,
                project_end=project.end_date or project.start_date or project.created_at,
            )
            root = WBSNode.objects.create(project_id=project.pk)
            WBSNodeVersion.objects.create(
                node_id=root.pk,
                revision_id=revision.pk,
                title=f"Root: {project.name}",
                sequence=1,
                tree_id=1,
                lft=1,
                rght=2,
                level=0,
            )

        updates = {}
        if project.active_baseline_revision_id is None:
            updates["active_baseline_revision_id"] = revision.pk
        if project.current_execution_revision_id is None:
            updates["current_execution_revision_id"] = revision.pk
        if project.current_forecast_revision_id is None:
            updates["current_forecast_revision_id"] = revision.pk
        if project.working_revision_id is None and revision.approved_at is None:
            updates["working_revision_id"] = revision.pk
        if project.current_data_date is None:
            updates["current_data_date"] = project.start_date or project.created_at
        if updates:
            Project.objects.filter(pk=project.pk).update(**updates)


class Migration(migrations.Migration):
    dependencies = [("ktcPlanning", "0020_project_official_revision_spine")]

    operations = [
        migrations.RunPython(repair_legacy_revision_spine, migrations.RunPython.noop),
    ]