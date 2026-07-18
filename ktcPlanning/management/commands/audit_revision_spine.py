import json

from django.core.management.base import BaseCommand, CommandError

from ktcPlanning.models import Project


class Command(BaseCommand):
    help = "Audit official baseline/execution/forecast/working revision pointers."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", dest="as_json")
        parser.add_argument("--fail-on-error", action="store_true")

    def handle(self, *args, **options):
        rows = []
        error_count = 0
        warning_count = 0
        projects = Project.objects.select_related(
            "active_baseline_revision",
            "current_execution_revision",
            "current_forecast_revision",
            "working_revision",
        ).order_by("name")

        for project in projects:
            errors = []
            warnings = []
            roles = {
                "baseline": project.active_baseline_revision,
                "execution": project.current_execution_revision,
                "forecast": project.current_forecast_revision,
                "working": project.working_revision,
            }
            for role, revision in roles.items():
                if revision and (revision.project_id != project.pk or revision.is_deleted):
                    errors.append(f"invalid_{role}_revision")
            if not roles["baseline"]:
                errors.append("missing_active_baseline")
            elif not roles["baseline"].is_baseline:
                errors.append("baseline_not_marked")
            if not roles["execution"]:
                errors.append("missing_execution_revision")
            if not roles["forecast"]:
                errors.append("missing_forecast_revision")
            if roles["working"] and roles["working"].approved_at:
                errors.append("working_revision_is_approved")
            if not project.current_data_date:
                warnings.append("missing_data_date")

            open_revisions = project.revisions.filter(
                is_deleted=False, approved_at__isnull=True
            )
            if open_revisions.count() > 1:
                warnings.append("multiple_nonofficial_open_revisions")
            expected_approver = project.get_default_approver()
            if roles["working"] and expected_approver:
                if roles["working"].designated_approver_id != expected_approver.pk:
                    warnings.append("working_revision_approver_mismatch")

            error_count += len(errors)
            warning_count += len(warnings)
            rows.append({
                "project_id": str(project.pk),
                "project": project.name,
                "lifecycle": project.lifecycle_status,
                "data_date": project.current_data_date.isoformat() if project.current_data_date else None,
                "baseline_revision": roles["baseline"].number if roles["baseline"] else None,
                "execution_revision": roles["execution"].number if roles["execution"] else None,
                "forecast_revision": roles["forecast"].number if roles["forecast"] else None,
                "working_revision": roles["working"].number if roles["working"] else None,
                "errors": errors,
                "warnings": warnings,
            })

        summary = {
            "projects": len(rows),
            "errors": error_count,
            "warnings": warning_count,
            "rows": rows,
        }
        if options["as_json"]:
            self.stdout.write(json.dumps(summary, ensure_ascii=False, indent=2))
        else:
            self.stdout.write(f"Projects={len(rows)} Errors={error_count} Warnings={warning_count}")
            for row in rows:
                state = "OK" if not row["errors"] else "ERROR"
                self.stdout.write(
                    f"[{state}] {row['project']} | B={row['baseline_revision']} "
                    f"E={row['execution_revision']} F={row['forecast_revision']} "
                    f"W={row['working_revision']} | "
                    f"errors={','.join(row['errors']) or '-'} "
                    f"warnings={','.join(row['warnings']) or '-'}"
                )
        if options["fail_on_error"] and error_count:
            raise CommandError(f"Revision spine audit found {error_count} errors.")
