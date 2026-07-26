from decimal import Decimal

from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from ktcPlanning.models import (
    Assignment,
    BudgetAllocation,
    CostTransaction,
    CostTransactionMilestoneAllocation,
    FundingSource,
    PaymentMilestone,
    PaymentTransaction,
    Resource,
    TaskDelivery,
    TaskFinancialPlan,
    VarianceReport,
)
from tests.factories import assign_role, make_company_admin, make_member, make_project, make_revision, make_task


def api(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


class FinancialControlAcceptanceTests(TestCase):
    def setUp(self):
        self.admin = make_company_admin()
        self.project = make_project(creator=self.admin, scope="intra_unit", name="Financial Control Project")
        self.revision = make_revision(self.project, creator=self.admin, approved=True, is_baseline=True)
        self.task, self.task_version = make_task(self.project, self.revision, title="Financial Control Task")
        self.viewer = make_member()
        assign_role(self.task, self.revision, self.viewer, role="executor")
        self.outsider = make_member()
        self.url = reverse("financial-control")
        self.resource = Resource.objects.create(code="FC-COST", name="Financial Control Cost", resource_type=Resource.COST)
        self.assignment = Assignment.objects.create(
            revision=self.revision,
            task=self.task,
            resource=self.resource,
            units_percent=Decimal("100.00"),
        )
        self.source = FundingSource.objects.create(
            title="Approved budget source",
            source_type="CONTRACT",
            received_date=timezone.localdate(),
            total_amount=Decimal("1000.00"),
            status="APPROVED",
            created_by=self.admin,
        )
        self.budget = BudgetAllocation.objects.create(
            funding_source=self.source,
            project=self.project,
            revision=self.revision,
            scope_type="TASK",
            wbs_node=self.task_version.wbs_node,
            task=self.task,
            cost_type="COST",
            allocated_amount=Decimal("1000.00"),
            status="APPROVED",
            created_by=self.admin,
        )
        self.plan = TaskFinancialPlan.objects.create(
            task=self.task,
            direction=TaskFinancialPlan.DIRECTION_PAYABLE,
            contract_amount=Decimal("1000.00"),
            currency="IRR",
            status=TaskFinancialPlan.STATUS_ACTIVE,
            created_by=self.admin,
        )
        self.milestone = PaymentMilestone.objects.create(
            financial_plan=self.plan,
            title="Gate",
            sequence=1,
            trigger_type=PaymentMilestone.TRIGGER_BEFORE_START,
            amount_type=PaymentMilestone.AMOUNT_FIXED,
            fixed_amount=Decimal("1000.00"),
        )
        self.linked_cost = CostTransaction.objects.create(
            project=self.project,
            revision=self.revision,
            task=self.task,
            assignment=self.assignment,
            financial_plan=self.plan,
            transaction_type="COST",
            amount=Decimal("700.00"),
            currency="IRR",
            transaction_date=timezone.localdate(),
            created_by=self.admin,
        )
        CostTransactionMilestoneAllocation.objects.create(
            cost_transaction=self.linked_cost,
            milestone=self.milestone,
            allocated_amount=Decimal("700.00"),
        )
        self.unplanned_cost = CostTransaction.objects.create(
            project=self.project,
            revision=self.revision,
            task=self.task,
            assignment=self.assignment,
            transaction_type="COST",
            amount=Decimal("200.00"),
            currency="IRR",
            transaction_date=timezone.localdate(),
            created_by=self.admin,
        )
        self.payment = PaymentTransaction.objects.create(
            milestone=self.milestone,
            transaction_type=PaymentTransaction.TYPE_PAYMENT,
            amount=Decimal("250.00"),
            transaction_date=timezone.localdate(),
            created_by=self.admin,
        )
        self.delivery = TaskDelivery.objects.create(
            project=self.project,
            task=self.task,
            status=TaskDelivery.STATUS_SUBMITTED,
            delivery_reference="DEL-FC",
            created_by=self.admin,
            submitted_by=self.admin,
            submitted_at=timezone.now(),
        )
        self.evm = VarianceReport.objects.create(
            task=self.task,
            revision=self.revision,
            report_date=timezone.localdate(),
            dimension=VarianceReport.DIMENSION_COST,
            currency="IRR",
            budget_at_completion=Decimal("1000.00"),
            planned_value=Decimal("800.00"),
            earned_value=Decimal("900.00"),
            actual_cost=Decimal("900.00"),
            spi=Decimal("1.12"),
            cpi=Decimal("1.00"),
            schedule_variance=Decimal("100.00"),
            cost_variance=Decimal("0.00"),
            estimate_at_completion=Decimal("1000.00"),
            estimate_to_complete=Decimal("100.00"),
            variance_at_completion=Decimal("0.00"),
        )

    def get_payload(self, user=None, **params):
        response = api(user or self.admin).get(self.url, params)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return response.data

    def test_summary_totals(self):
        summary = self.get_payload(project_id=self.project.id)["summary"]
        self.assertEqual(summary["approved_budget"], "1000.00")
        self.assertEqual(summary["recognized_cost"], "900.00")
        self.assertEqual(summary["allocated_recognized_cost"], "700.00")
        self.assertEqual(summary["unallocated_recognized_cost"], "200.00")
        self.assertEqual(summary["contract_value"], "1000.00")
        self.assertEqual(summary["paid_amount"], "250.00")
        self.assertEqual(summary["outstanding_payment"], "750.00")

    def test_task_totals(self):
        row = self.get_payload(project_id=self.project.id)["tasks"][0]
        self.assertEqual(row["task_title"], "Financial Control Task")
        self.assertEqual(row["approved_budget"], "1000.00")
        self.assertEqual(row["recognized_cost"], "900.00")
        self.assertEqual(row["allocated_recognized_cost"], "700.00")

    def test_permission_isolation(self):
        response = api(self.outsider).get(self.url, {"project_id": self.project.id})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        viewer_payload = self.get_payload(self.viewer, project_id=self.project.id)
        self.assertEqual(len(viewer_payload["tasks"]), 1)

    def test_recognized_cost_from_cost_transactions(self):
        row = self.get_payload(project_id=self.project.id)["tasks"][0]
        self.assertEqual(row["recognized_cost"], "900.00")

    def test_allocation_totals_are_separate(self):
        row = self.get_payload(project_id=self.project.id)["tasks"][0]
        self.assertEqual(row["allocated_recognized_cost"], "700.00")
        self.assertEqual(row["unallocated_recognized_cost"], "200.00")

    def test_payment_totals_are_separate(self):
        row = self.get_payload(project_id=self.project.id)["tasks"][0]
        self.assertEqual(row["paid_amount"], "250.00")
        self.assertEqual(row["recognized_cost"], "900.00")

    def test_cost_evm_ac_unchanged(self):
        row = self.get_payload(project_id=self.project.id)["tasks"][0]
        self.assertEqual(row["ac"], "900.00")
        self.assertEqual(VarianceReport.objects.get(pk=self.evm.pk).actual_cost, Decimal("900.00"))

    def test_aggregate_cpi_from_totals(self):
        second_task, _ = make_task(self.project, self.revision, title="Second EVM Task")
        assign_role(second_task, self.revision, self.viewer, role="executor")
        VarianceReport.objects.create(
            task=second_task,
            revision=self.revision,
            report_date=timezone.localdate(),
            dimension=VarianceReport.DIMENSION_COST,
            currency="IRR",
            budget_at_completion=Decimal("1000.00"),
            planned_value=Decimal("1000.00"),
            earned_value=Decimal("500.00"),
            actual_cost=Decimal("1000.00"),
            spi=Decimal("0.50"),
            cpi=Decimal("0.50"),
            schedule_variance=Decimal("-500.00"),
            cost_variance=Decimal("-500.00"),
            estimate_at_completion=Decimal("2000.00"),
            estimate_to_complete=Decimal("1500.00"),
            variance_at_completion=Decimal("-1000.00"),
        )
        summary = self.get_payload(project_id=self.project.id)["summary"]
        self.assertEqual(summary["cpi"], "0.7368")

    def test_warnings(self):
        codes = {item["code"] for item in self.get_payload(project_id=self.project.id)["warnings"]}
        self.assertIn("unallocated_recognized_cost", codes)
        self.assertIn("delivery_pending_approval", codes)
        self.assertIn("cost_transaction_without_financial_plan", codes)

    def test_health_classification(self):
        row = self.get_payload(project_id=self.project.id)["tasks"][0]
        self.assertEqual(row["financial_health"], "warning")
        PaymentTransaction.objects.create(
            milestone=self.milestone,
            transaction_type=PaymentTransaction.TYPE_PAYMENT,
            amount=Decimal("1000.00"),
            transaction_date=timezone.localdate(),
            created_by=self.admin,
        )
        row = self.get_payload(project_id=self.project.id)["tasks"][0]
        self.assertEqual(row["financial_health"], "critical")

    def test_transaction_without_plan_warning(self):
        codes = {item["code"] for item in self.get_payload(project_id=self.project.id)["tasks"][0]["warnings"]}
        self.assertIn("cost_transaction_without_financial_plan", codes)

    def test_delivery_pending_warning(self):
        codes = {item["code"] for item in self.get_payload(project_id=self.project.id)["tasks"][0]["warnings"]}
        self.assertIn("delivery_pending_approval", codes)

    def test_invalid_status_date(self):
        response = api(self.admin).get(self.url, {"status_date": "bad-date"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("status_date", response.data)

    def test_inaccessible_project(self):
        response = api(self.outsider).get(self.url, {"project_id": self.project.id})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_no_data_response(self):
        payload = self.get_payload(self.outsider)
        self.assertEqual(payload["tasks"], [])
        self.assertEqual(payload["summary"]["recognized_cost"], "0.00")
        self.assertEqual(payload["warnings"], [])

    def test_query_count_smoke(self):
        with CaptureQueriesContext(connection) as captured:
            self.get_payload(project_id=self.project.id)
        self.assertLessEqual(len(captured), 14)
