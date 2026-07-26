from datetime import date, timedelta
from decimal import Decimal

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from ktcPlanning.models import CostTransaction, Resource, Assignment, VarianceReport
from tests.factories import assign_role, make_company_admin, make_member, make_project, make_revision, make_task


def api(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


class FinancialControlPaginationTests(APITestCase):
    task_count = 60

    def setUp(self):
        self.admin = make_company_admin()
        self.viewer = make_member()
        self.project = make_project(creator=self.admin, scope="intra_unit", name="Pagination Project")
        self.revision = make_revision(self.project, creator=self.admin, approved=True, is_baseline=True)
        self.url = reverse("financial-control")
        self.resource = Resource.objects.create(code="PAGE-COST", name="Pagination Cost", resource_type=Resource.COST)
        self.today = timezone.localdate()
        self.tasks = []
        for index in range(self.task_count):
            task, version = make_task(self.project, self.revision, title=f"Page Task {index:03d}")
            assign_role(task, self.revision, self.viewer, role="executor")
            assignment = Assignment.objects.create(
                revision=self.revision,
                task=task,
                resource=self.resource,
                units_percent=Decimal("100.00"),
            )
            if index < 8:
                CostTransaction.objects.create(
                    project=self.project,
                    revision=self.revision,
                    task=task,
                    assignment=assignment,
                    transaction_type="COST",
                    amount=Decimal("10.00") + Decimal(index),
                    currency="IRR",
                    transaction_date=self.today,
                    created_by=self.admin,
                )
            VarianceReport.objects.create(
                task=task,
                revision=self.revision,
                report_date=self.today,
                dimension=VarianceReport.DIMENSION_COST,
                currency="IRR",
                budget_at_completion=Decimal("100.00"),
                planned_value=Decimal("50.00"),
                earned_value=Decimal("40.00") + Decimal(index),
                actual_cost=Decimal("20.00") + Decimal(index),
                cpi=Decimal("1.00"),
                spi=Decimal("1.00"),
            )
            self.tasks.append(task)

    def payload(self, **params):
        response = api(self.admin).get(self.url, params)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return response.data

    def test_default_page_size_is_50(self):
        payload = self.payload(project_id=self.project.id)
        self.assertEqual(len(payload["tasks"]), 50)

    def test_pagination_metadata_total_items(self):
        payload = self.payload(project_id=self.project.id)
        self.assertEqual(payload["pagination"]["total_items"], self.task_count)

    def test_pagination_metadata_total_pages(self):
        payload = self.payload(project_id=self.project.id)
        self.assertEqual(payload["pagination"]["total_pages"], 2)

    def test_second_page_returns_remaining_rows(self):
        payload = self.payload(project_id=self.project.id, page=2)
        self.assertEqual(len(payload["tasks"]), 10)

    def test_has_next_and_previous(self):
        first = self.payload(project_id=self.project.id)
        second = self.payload(project_id=self.project.id, page=2)
        self.assertTrue(first["pagination"]["has_next"])
        self.assertTrue(second["pagination"]["has_previous"])

    def test_page_size_parameter(self):
        payload = self.payload(project_id=self.project.id, page_size=25)
        self.assertEqual(len(payload["tasks"]), 25)
        self.assertEqual(payload["pagination"]["page_size"], 25)

    def test_page_size_is_capped(self):
        payload = self.payload(project_id=self.project.id, page_size=500)
        self.assertEqual(payload["pagination"]["page_size"], 200)

    def test_invalid_page_is_rejected(self):
        response = api(self.admin).get(self.url, {"project_id": self.project.id, "page": "bad"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("page", response.data)

    def test_invalid_page_size_is_rejected(self):
        response = api(self.admin).get(self.url, {"project_id": self.project.id, "page_size": "bad"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("page_size", response.data)

    def test_summary_is_not_limited_to_current_page(self):
        payload = self.payload(project_id=self.project.id, page_size=10)
        self.assertEqual(payload["summary"]["bac"], "6000.00")

    def test_warning_summary_is_not_limited_to_current_page(self):
        payload = self.payload(project_id=self.project.id, page_size=10)
        self.assertEqual(payload["warnings_summary"]["by_code"]["missing_approved_budget"], 8)

    def test_page_warnings_only_follow_current_page(self):
        payload = self.payload(project_id=self.project.id, page=2, page_size=25)
        self.assertEqual(payload["warnings"], [])

    def test_search_filters_server_side_before_pagination(self):
        payload = self.payload(project_id=self.project.id, search="Page Task 005")
        self.assertEqual(payload["pagination"]["total_items"], 1)
        self.assertEqual(payload["tasks"][0]["task_title"], "Page Task 005")

    def test_task_filter(self):
        payload = self.payload(project_id=self.project.id, task_id=self.tasks[3].id)
        self.assertEqual(payload["pagination"]["total_items"], 1)
        self.assertEqual(payload["tasks"][0]["task_id"], str(self.tasks[3].id))

    def test_warning_filter(self):
        payload = self.payload(project_id=self.project.id, warning="missing_approved_budget")
        self.assertEqual(payload["pagination"]["total_items"], 8)

    def test_health_filter(self):
        payload = self.payload(project_id=self.project.id, health="warning")
        self.assertGreaterEqual(payload["pagination"]["total_items"], 8)
        self.assertTrue(all(row["financial_health"] == "warning" for row in payload["tasks"]))

    def test_invalid_health_filter_is_rejected(self):
        response = api(self.admin).get(self.url, {"project_id": self.project.id, "health": "bad"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("health", response.data)

    def test_ordering_by_task_title_desc(self):
        payload = self.payload(project_id=self.project.id, ordering="-task_title", page_size=5)
        titles = [row["task_title"] for row in payload["tasks"]]
        self.assertEqual(titles, sorted(titles, reverse=True))

    def test_ordering_by_numeric_field(self):
        payload = self.payload(project_id=self.project.id, ordering="-recognized_cost", page_size=3)
        values = [Decimal(row["recognized_cost"]) for row in payload["tasks"]]
        self.assertEqual(values, sorted(values, reverse=True))

    def test_invalid_ordering_is_rejected(self):
        response = api(self.admin).get(self.url, {"project_id": self.project.id, "ordering": "created_at"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("ordering", response.data)

    def test_status_date_uses_latest_cost_dimension_snapshot(self):
        target = self.tasks[0]
        VarianceReport.objects.create(
            task=target,
            revision=self.revision,
            report_date=self.today + timedelta(days=1),
            dimension=VarianceReport.DIMENSION_EFFORT,
            budget_at_completion=Decimal("999.00"),
        )
        payload = self.payload(project_id=self.project.id, task_id=target.id, status_date=self.today.isoformat())
        self.assertEqual(payload["tasks"][0]["bac"], "100.00")

    def test_response_contains_only_page_rows(self):
        payload = self.payload(project_id=self.project.id, page_size=7)
        self.assertEqual(len(payload["tasks"]), 7)
        self.assertEqual(payload["pagination"]["total_items"], self.task_count)

    def test_query_count_does_not_grow_with_second_page(self):
        with CaptureQueriesContext(connection) as first:
            self.payload(project_id=self.project.id, page=1, page_size=10)
        with CaptureQueriesContext(connection) as second:
            self.payload(project_id=self.project.id, page=2, page_size=10)
        self.assertLessEqual(abs(len(first) - len(second)), 2)

    def test_project_permission_scopes_rows(self):
        payload = self.payload()
        self.assertEqual(payload["pagination"]["total_items"], self.task_count)
        viewer_payload = api(self.viewer).get(self.url, {"project_id": self.project.id, "page_size": 5})
        self.assertEqual(viewer_payload.status_code, status.HTTP_200_OK, viewer_payload.data)
        self.assertEqual(len(viewer_payload.data["tasks"]), 5)
