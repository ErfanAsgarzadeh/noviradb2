from decimal import Decimal
from datetime import timedelta

from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from ktcPlanning.models import TaskActual, VarianceReport
from tests.factories import make_company_admin, make_project, make_revision, make_task


class PlannerDraftReportTests(APITestCase):
    def test_planner_draft_handles_decimal_weights_and_progress(self):
        admin = make_company_admin()
        project = make_project(creator=admin)
        revision = make_revision(project, creator=admin, approved=True)
        _, task_version = make_task(project, revision, duration_hours=8)
        task_version.weight = Decimal("2.50")
        task_version.save(update_fields=["weight"])
        TaskActual.objects.create(
            task_version=task_version,
            actual_start=timezone.now(),
            progress=Decimal("80.00"),
            updated_by=admin,
        )

        self.client.force_authenticate(user=admin)
        response = self.client.get(f"/api/reports/planner/draft/{project.id}/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["suggested_overall_progress"], 80.0)

    def test_planner_draft_uses_latest_evm_variance_per_task(self):
        admin = make_company_admin()
        project = make_project(creator=admin)
        revision = make_revision(project, creator=admin, approved=True)
        task, task_version = make_task(project, revision, duration_hours=8)
        today = timezone.localdate()
        for day, cpi in ((today - timedelta(days=1), Decimal("0.50")), (today, Decimal("0.75"))):
            VarianceReport.objects.create(
                task=task,
                revision=revision,
                report_date=day,
                budget_at_completion=Decimal("8.00"),
                planned_value=Decimal("8.00"),
                earned_value=Decimal("4.00"),
                actual_cost=Decimal("8.00"),
                spi=Decimal("0.50"),
                cpi=cpi,
                action_required=True,
            )

        self.client.force_authenticate(user=admin)
        response = self.client.get(f"/api/reports/planner/draft/{project.id}/")

        evm_rows = [
            row for row in response.data["suggested_bottlenecks"]
            if row["task_id"] == str(task.id)
            and row["issue_type"] == "انحراف شاخص‌های زمانی/هزینه‌ای (EVM)"
        ]
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(evm_rows), 1)
        self.assertIn("0.75", evm_rows[0]["description"])
