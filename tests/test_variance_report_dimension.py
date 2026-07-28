from datetime import date, timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from ktcPlanning.models import VarianceReport
from tests.factories import assign_role, make_company_admin, make_member, make_project, make_report, make_revision, make_task


def api(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


class VarianceReportDimensionTests(APITestCase):
    def setUp(self):
        self.admin = make_company_admin()
        self.viewer = make_member()
        self.project = make_project(creator=self.admin, scope="intra_unit", name="Variance Dimension Project")
        self.revision = make_revision(self.project, creator=self.admin, approved=True, is_baseline=True)
        self.task, self.task_version = make_task(self.project, self.revision, title="Dimension Task")
        assign_role(self.task, self.revision, self.viewer, role="executor")
        self.url = reverse("variance-report-list")
        self.today = timezone.localdate()

    def create_report(self, *, dimension, task=None, day=None, ev=Decimal("10.00"), currency=None):
        return VarianceReport.objects.create(
            task=task or self.task,
            revision=self.revision,
            report_date=day or self.today,
            dimension=dimension,
            currency=currency if dimension == VarianceReport.DIMENSION_COST else None,
            budget_at_completion=Decimal("100.00"),
            planned_value=Decimal("20.00"),
            earned_value=ev,
            actual_cost=Decimal("5.00"),
        )

    def test_dimension_choices_are_official(self):
        self.assertEqual(VarianceReport.DIMENSION_EFFORT, "effort")
        self.assertEqual(VarianceReport.DIMENSION_COST, "cost")
        self.assertEqual({value for value, _ in VarianceReport.DIMENSION_CHOICES}, {"effort", "cost"})

    def test_default_dimension_is_effort_for_legacy_rows(self):
        report = VarianceReport.objects.create(task=self.task, revision=self.revision, report_date=self.today)
        self.assertEqual(report.dimension, VarianceReport.DIMENSION_EFFORT)

    def test_cost_and_effort_can_share_task_revision_and_date(self):
        effort = self.create_report(dimension=VarianceReport.DIMENSION_EFFORT)
        cost = self.create_report(dimension=VarianceReport.DIMENSION_COST, currency="IRR")
        self.assertNotEqual(effort.pk, cost.pk)

    def test_duplicate_same_dimension_identity_is_rejected(self):
        self.create_report(dimension=VarianceReport.DIMENSION_COST, currency="IRR")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.create_report(dimension=VarianceReport.DIMENSION_COST, currency="IRR")

    def test_effort_list_defaults_to_effort_dimension(self):
        self.create_report(dimension=VarianceReport.DIMENSION_EFFORT)
        self.create_report(dimension=VarianceReport.DIMENSION_COST, currency="IRR")
        response = api(self.viewer).get(self.url, {"revision_id": self.revision.id})
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["dimension"], "effort")

    def test_dimension_filter_returns_cost_rows(self):
        self.create_report(dimension=VarianceReport.DIMENSION_EFFORT)
        self.create_report(dimension=VarianceReport.DIMENSION_COST, currency="IRR")
        response = api(self.viewer).get(self.url, {"revision_id": self.revision.id, "dimension": "cost"})
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["dimension"], "cost")

    def test_invalid_dimension_filter_is_rejected(self):
        response = api(self.viewer).get(self.url, {"revision_id": self.revision.id, "dimension": "cash"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("dimension", response.data)

    def test_latest_task_queryset_is_dimension_scoped(self):
        self.create_report(dimension=VarianceReport.DIMENSION_EFFORT, day=self.today - timedelta(days=1))
        self.create_report(dimension=VarianceReport.DIMENSION_COST, day=self.today, currency="IRR")
        response = api(self.viewer).get(self.url, {"revision_id": self.revision.id, "dimension": "effort", "page": 1, "page_size": 10})
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["total"], 1)
        self.assertEqual(response.data["results"][0]["dimension"], "effort")

    def test_serializer_accepts_explicit_cost_dimension(self):
        payload = {
            "task": str(self.task.id),
            "revision": self.revision.id,
            "report_date": self.today.isoformat(),
            "dimension": "cost",
            "currency": "IRR",
            "budget_at_completion": "100.00",
            "planned_value": "20.00",
            "earned_value": "10.00",
            "actual_cost": "5.00",
        }
        response = api(self.admin).post(self.url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["dimension"], "cost")

    def test_effort_reports_from_task_progress_do_not_create_cost_snapshots(self):
        make_report(self.task, self.admin, progress=80, status_val="on-track", approval_status="reviewer_approved")
        self.assertFalse(VarianceReport.objects.filter(task=self.task, dimension=VarianceReport.DIMENSION_COST).exists())

    def test_unique_identity_allows_same_date_on_another_task(self):
        other, _ = make_task(self.project, self.revision, title="Other Dimension Task")
        self.create_report(dimension=VarianceReport.DIMENSION_COST, currency="IRR")
        report = self.create_report(dimension=VarianceReport.DIMENSION_COST, task=other, currency="IRR")
        self.assertEqual(report.task_id, other.id)

    def test_cost_reports_are_unique_per_currency(self):
        irr = self.create_report(dimension=VarianceReport.DIMENSION_COST, currency="IRR")
        usd = self.create_report(dimension=VarianceReport.DIMENSION_COST, currency="USD")
        self.assertNotEqual(irr.pk, usd.pk)

    def test_cost_evm_series_uses_each_cost_snapshot_report_date(self):
        yesterday = self.today - timedelta(days=1)
        self.create_report(dimension=VarianceReport.DIMENSION_COST, day=yesterday, ev=Decimal("10.00"), currency="IRR")
        self.create_report(dimension=VarianceReport.DIMENSION_COST, day=self.today, ev=Decimal("30.00"), currency="IRR")

        response = api(self.viewer).get(self.url, {
            "revision_id": self.revision.id,
            "dimension": "cost",
            "currency": "IRR",
            "page": 1,
            "page_size": 10,
        })

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(
            response.data["series"],
            [
                {"date": yesterday.isoformat(), "plannedValue": 20.0, "earnedValue": 10.0, "actualCost": 5.0},
                {"date": self.today.isoformat(), "plannedValue": 20.0, "earnedValue": 30.0, "actualCost": 5.0},
            ],
        )
