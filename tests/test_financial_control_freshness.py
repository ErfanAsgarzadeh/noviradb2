from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
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
from tests.factories import assign_role, make_company_admin, make_member, make_project, make_report, make_revision, make_task


def api(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def aware(day):
    return timezone.make_aware(datetime.combine(day, time(hour=9)))


class FinancialControlFreshnessTests(TestCase):
    status_day = date(2026, 1, 10)
    old_day = date(2026, 1, 5)
    future_day = date(2026, 1, 20)

    def setUp(self):
        self.admin = make_company_admin()
        self.viewer = make_member()
        self.outsider = make_member()
        self.project = make_project(creator=self.admin, scope="intra_unit", name="Freshness Project")
        self.revision = make_revision(self.project, creator=self.admin, approved=True, is_baseline=True)
        self.task, self.task_version = make_task(self.project, self.revision, title="Freshness Task")
        assign_role(self.task, self.revision, self.viewer, role="executor")
        self.url = reverse("financial-control")
        self.resource = Resource.objects.create(code="FRESH-COST", name="Fresh Cost", resource_type=Resource.COST)
        self.assignment = Assignment.objects.create(
            revision=self.revision,
            task=self.task,
            resource=self.resource,
            units_percent=Decimal("100.00"),
        )
        self.source = FundingSource.objects.create(
            title="Approved source",
            source_type="CONTRACT",
            received_date=self.old_day,
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
            allocated_amount=Decimal("100.00"),
            status="APPROVED",
            approved_by=self.admin,
            approved_at=aware(self.old_day),
            created_by=self.admin,
        )
        self.plan = TaskFinancialPlan.objects.create(
            task=self.task,
            direction=TaskFinancialPlan.DIRECTION_PAYABLE,
            contract_amount=Decimal("100.00"),
            currency="IRR",
            status=TaskFinancialPlan.STATUS_ACTIVE,
            created_by=self.admin,
        )
        TaskFinancialPlan.objects.filter(pk=self.plan.pk).update(created_at=aware(self.old_day))
        self.plan.refresh_from_db()
        self.receivable_plan = TaskFinancialPlan.objects.create(
            task=self.task,
            direction=TaskFinancialPlan.DIRECTION_RECEIVABLE,
            contract_amount=Decimal("999.00"),
            currency="IRR",
            status=TaskFinancialPlan.STATUS_DRAFT,
            created_by=self.admin,
        )
        TaskFinancialPlan.objects.filter(pk=self.receivable_plan.pk).update(created_at=aware(self.old_day))
        self.milestone = PaymentMilestone.objects.create(
            financial_plan=self.plan,
            title="Payable gate",
            sequence=1,
            trigger_type=PaymentMilestone.TRIGGER_BEFORE_START,
            amount_type=PaymentMilestone.AMOUNT_FIXED,
            fixed_amount=Decimal("100.00"),
        )
        self.receivable_milestone = PaymentMilestone.objects.create(
            financial_plan=self.receivable_plan,
            title="Receivable gate",
            sequence=1,
            trigger_type=PaymentMilestone.TRIGGER_BEFORE_START,
            amount_type=PaymentMilestone.AMOUNT_FIXED,
            fixed_amount=Decimal("999.00"),
        )
        self.cost = CostTransaction.objects.create(
            project=self.project,
            revision=self.revision,
            task=self.task,
            assignment=self.assignment,
            financial_plan=self.plan,
            transaction_type="COST",
            amount=Decimal("100.00"),
            currency="IRR",
            transaction_date=self.old_day,
            created_by=self.admin,
        )
        self.future_cost = CostTransaction.objects.create(
            project=self.project,
            revision=self.revision,
            task=self.task,
            assignment=self.assignment,
            financial_plan=self.plan,
            transaction_type="COST",
            amount=Decimal("300.00"),
            currency="IRR",
            transaction_date=self.future_day,
            created_by=self.admin,
        )
        self.allocation = CostTransactionMilestoneAllocation.objects.create(
            cost_transaction=self.cost,
            milestone=self.milestone,
            allocated_amount=Decimal("100.00"),
        )
        CostTransactionMilestoneAllocation.objects.filter(pk=self.allocation.pk).update(created_at=aware(self.old_day))
        self.future_allocation = CostTransactionMilestoneAllocation.objects.create(
            cost_transaction=self.future_cost,
            milestone=self.milestone,
            allocated_amount=Decimal("100.00"),
        )
        CostTransactionMilestoneAllocation.objects.filter(pk=self.future_allocation.pk).update(created_at=aware(self.future_day))
        self.payment = PaymentTransaction.objects.create(
            milestone=self.milestone,
            transaction_type=PaymentTransaction.TYPE_PAYMENT,
            amount=Decimal("50.00"),
            transaction_date=self.old_day,
            created_by=self.admin,
        )
        self.refund = PaymentTransaction.objects.create(
            milestone=self.milestone,
            transaction_type=PaymentTransaction.TYPE_REFUND,
            amount=Decimal("10.00"),
            transaction_date=self.old_day,
            created_by=self.admin,
        )
        self.future_payment = PaymentTransaction.objects.create(
            milestone=self.milestone,
            transaction_type=PaymentTransaction.TYPE_PAYMENT,
            amount=Decimal("20.00"),
            transaction_date=self.future_day,
            created_by=self.admin,
        )
        PaymentTransaction.objects.create(
            milestone=self.receivable_milestone,
            transaction_type=PaymentTransaction.TYPE_PAYMENT,
            amount=Decimal("500.00"),
            transaction_date=self.old_day,
            created_by=self.admin,
        )
        self.future_delivery = TaskDelivery.objects.create(
            project=self.project,
            task=self.task,
            status=TaskDelivery.STATUS_SUBMITTED,
            delivery_reference="FUTURE-DEL",
            created_by=self.admin,
            submitted_by=self.admin,
            submitted_at=aware(self.future_day),
        )
        TaskDelivery.objects.filter(pk=self.future_delivery.pk).update(created_at=aware(self.future_day))
        self.old_evm = VarianceReport.objects.create(
            task=self.task,
            revision=self.revision,
            report_date=self.old_day,
            dimension=VarianceReport.DIMENSION_COST,
            currency="IRR",
            budget_at_completion=Decimal("100.00"),
            planned_value=Decimal("80.00"),
            earned_value=Decimal("90.00"),
            actual_cost=Decimal("100.00"),
            spi=Decimal("1.12"),
            cpi=Decimal("0.90"),
            schedule_variance=Decimal("10.00"),
            cost_variance=Decimal("-10.00"),
            estimate_at_completion=Decimal("111.11"),
            estimate_to_complete=Decimal("11.11"),
            variance_at_completion=Decimal("-11.11"),
        )
        self.future_evm = VarianceReport.objects.create(
            task=self.task,
            revision=self.revision,
            report_date=self.future_day,
            dimension=VarianceReport.DIMENSION_COST,
            currency="IRR",
            budget_at_completion=Decimal("999.00"),
            planned_value=Decimal("999.00"),
            earned_value=Decimal("999.00"),
            actual_cost=Decimal("999.00"),
            spi=Decimal("1.00"),
            cpi=Decimal("1.00"),
            schedule_variance=Decimal("0.00"),
            cost_variance=Decimal("0.00"),
            estimate_at_completion=Decimal("999.00"),
            estimate_to_complete=Decimal("0.00"),
            variance_at_completion=Decimal("0.00"),
        )

    def payload(self, user=None, **params):
        response = api(user or self.admin).get(self.url, params)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return response.data

    def row(self, **params):
        return self.payload(project_id=self.project.id, **params)["tasks"][0]

    def codes(self, **params):
        return {item["code"] for item in self.row(**params)["warnings"]}

    def test_cost_transaction_after_status_date_excluded(self):
        row = self.row(status_date=self.status_day)
        self.assertEqual(row["recognized_cost"], "100.00")

    def test_payment_after_status_date_excluded(self):
        row = self.row(status_date=self.status_day)
        self.assertEqual(row["paid_amount"], "40.00")

    def test_refund_before_status_date_included_as_negative(self):
        row = self.row(status_date=self.status_day)
        self.assertEqual(row["paid_amount"], "40.00")

    def test_future_delivery_excluded(self):
        row = self.row(status_date=self.status_day)
        self.assertIsNone(row["delivery_status"])
        self.assertNotIn("delivery_pending_approval", self.codes(status_date=self.status_day))

    def test_cost_snapshot_after_status_date_excluded(self):
        row = self.row(status_date=self.status_day)
        self.assertEqual(row["bac"], "100.00")
        self.assertEqual(row["evm_snapshot_date"], self.old_day.isoformat())

    def test_latest_cost_snapshot_before_status_date_selected(self):
        newer_day = self.status_day - timedelta(days=1)
        VarianceReport.objects.create(
            task=self.task,
            revision=self.revision,
            report_date=newer_day,
            dimension=VarianceReport.DIMENSION_COST,
            currency="IRR",
            budget_at_completion=Decimal("200.00"),
            planned_value=Decimal("100.00"),
            earned_value=Decimal("100.00"),
            actual_cost=Decimal("100.00"),
        )
        row = self.row(status_date=self.status_day)
        self.assertEqual(row["bac"], "200.00")
        self.assertEqual(row["evm_snapshot_date"], newer_day.isoformat())

    def test_effort_snapshot_excluded(self):
        VarianceReport.objects.filter(task=self.task).delete()
        make_report(self.task, self.admin, progress=80, status_val="on-track", approval_status="approved")
        row = self.row(status_date=self.status_day)
        self.assertIsNone(row["evm_snapshot_date"])
        self.assertIn("missing_cost_evm_snapshot", {item["code"] for item in row["warnings"]})

    def test_missing_snapshot_warning(self):
        VarianceReport.objects.filter(task=self.task).delete()
        self.assertIn("missing_cost_evm_snapshot", self.codes(status_date=self.status_day))

    def test_stale_snapshot_warning(self):
        row = self.row(status_date=self.status_day)
        self.assertTrue(row["evm_is_stale"])
        self.assertIn("stale_cost_evm_snapshot", {item["code"] for item in row["warnings"]})

    def test_reconciliation_identity(self):
        row = self.row(status_date=self.status_day)
        recognized = Decimal(row["recognized_cost"])
        allocated = Decimal(row["allocated_recognized_cost"])
        unallocated = Decimal(row["unallocated_recognized_cost"])
        outstanding = Decimal(row["outstanding_payment"])
        contract = Decimal(row["contract_value"])
        paid = Decimal(row["paid_amount"])
        self.assertEqual(recognized, allocated + unallocated)
        self.assertEqual(outstanding, contract - paid)

    def test_allocated_exceeds_recognized_warning(self):
        CostTransactionMilestoneAllocation.objects.filter(pk=self.allocation.pk).update(allocated_amount=Decimal("120.00"))
        self.assertIn("allocated_cost_exceeds_recognized_cost", self.codes(status_date=self.status_day))

    def test_paid_exceeds_contract_warning(self):
        PaymentTransaction.objects.create(
            milestone=self.milestone,
            transaction_type=PaymentTransaction.TYPE_PAYMENT,
            amount=Decimal("80.00"),
            transaction_date=self.old_day,
            created_by=self.admin,
        )
        codes = self.codes(status_date=self.status_day)
        self.assertIn("paid_amount_exceeds_contract", codes)
        self.assertIn("negative_outstanding_payment", codes)

    def test_boundary_warning_tests(self):
        self.assertNotIn("recognized_cost_exceeds_budget", self.codes(status_date=self.status_day))
        self.assertNotIn("recognized_cost_exceeds_contract", self.codes(status_date=self.status_day))
        self.assertNotIn("payment_exceeds_recognized_cost", self.codes(status_date=self.status_day))
        self.assertIn("cpi_below_one", self.codes(status_date=self.status_day))
        self.cost.amount = Decimal("101.00")
        self.cost.save(update_fields=["amount"])
        codes = self.codes(status_date=self.status_day)
        self.assertIn("recognized_cost_exceeds_budget", codes)
        self.assertIn("recognized_cost_exceeds_contract", codes)
        PaymentTransaction.objects.create(
            milestone=self.milestone,
            transaction_type=PaymentTransaction.TYPE_PAYMENT,
            amount=Decimal("70.00"),
            transaction_date=self.old_day,
            created_by=self.admin,
        )
        self.assertIn("payment_exceeds_recognized_cost", self.codes(status_date=self.status_day))
        self.old_evm.earned_value = Decimal("100.00")
        self.old_evm.actual_cost = Decimal("100.00")
        self.old_evm.save(update_fields=["earned_value", "actual_cost"])
        self.assertNotIn("cpi_below_one", self.codes(status_date=self.status_day))

    def test_aggregate_cpi_from_totals(self):
        second, _ = make_task(self.project, self.revision, title="Second CPI Task")
        assign_role(second, self.revision, self.viewer, role="executor")
        VarianceReport.objects.create(
            task=second,
            revision=self.revision,
            report_date=self.old_day,
            dimension=VarianceReport.DIMENSION_COST,
            currency="IRR",
            budget_at_completion=Decimal("100.00"),
            planned_value=Decimal("100.00"),
            earned_value=Decimal("50.00"),
            actual_cost=Decimal("100.00"),
        )
        summary = self.payload(project_id=self.project.id, status_date=self.status_day)["summary"]
        self.assertEqual(summary["cpi"], "0.7000")

    def test_aggregate_spi_from_totals(self):
        second, _ = make_task(self.project, self.revision, title="Second SPI Task")
        assign_role(second, self.revision, self.viewer, role="executor")
        VarianceReport.objects.create(
            task=second,
            revision=self.revision,
            report_date=self.old_day,
            dimension=VarianceReport.DIMENSION_COST,
            currency="IRR",
            budget_at_completion=Decimal("100.00"),
            planned_value=Decimal("120.00"),
            earned_value=Decimal("30.00"),
            actual_cost=Decimal("30.00"),
        )
        summary = self.payload(project_id=self.project.id, status_date=self.status_day)["summary"]
        self.assertEqual(summary["spi"], "0.6000")

    def test_zero_denominator(self):
        VarianceReport.objects.filter(task=self.task).delete()
        VarianceReport.objects.create(
            task=self.task,
            revision=self.revision,
            report_date=self.old_day,
            dimension=VarianceReport.DIMENSION_COST,
            currency="IRR",
            budget_at_completion=Decimal("100.00"),
            planned_value=Decimal("0.00"),
            earned_value=Decimal("0.00"),
            actual_cost=Decimal("0.00"),
        )
        summary = self.payload(project_id=self.project.id, status_date=self.status_day)["summary"]
        self.assertIsNone(summary["cpi"])
        self.assertIsNone(summary["spi"])

    def test_warning_filter_no_duplicates(self):
        TaskDelivery.objects.create(
            project=self.project,
            task=self.task,
            status=TaskDelivery.STATUS_SUBMITTED,
            delivery_reference="OLD-DEL",
            created_by=self.admin,
            submitted_by=self.admin,
            submitted_at=aware(self.old_day),
        )
        payload = self.payload(project_id=self.project.id, status_date=self.status_day, warning="stale_cost_evm_snapshot")
        self.assertEqual(len(payload["tasks"]), 1)

    def test_permission_isolation(self):
        response = api(self.outsider).get(self.url, {"project_id": self.project.id, "status_date": self.status_day})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        payload = self.payload(self.viewer, project_id=self.project.id, status_date=self.status_day)
        self.assertEqual(len(payload["tasks"]), 1)

    def test_no_data_response(self):
        payload = self.payload(self.outsider, status_date=self.status_day)
        self.assertEqual(payload["tasks"], [])
        self.assertEqual(payload["summary"]["recognized_cost"], "0.00")
        self.assertEqual(payload["summary"]["status_date"], self.status_day.isoformat())

    def test_query_count_smoke(self):
        with CaptureQueriesContext(connection) as captured:
            self.payload(project_id=self.project.id, status_date=self.status_day)
        self.assertLessEqual(len(captured), 14)
