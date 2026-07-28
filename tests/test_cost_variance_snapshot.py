from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from ktcPlanning.models import Assignment, CostTransaction, Resource, TaskFinancialPlan, VarianceReport
from ktcPlanning.models import BudgetAllocation, CostTransactionMilestoneAllocation, FundingSource, PaymentMilestone, PaymentTransaction
from ktcPlanning.cost_variance_snapshots import generate_cost_variance_reports
from tests.factories import make_company_admin, make_member, make_project, make_report, make_revision, make_task


def api(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


class CostVarianceSnapshotTests(TestCase):
    def setUp(self):
        self.admin = make_company_admin()
        self.project = make_project(creator=self.admin, scope="intra_unit", name="Cost Snapshot Project")
        self.revision = make_revision(self.project, creator=self.admin, approved=True, is_baseline=True)
        self.task, _ = make_task(self.project, self.revision, title="Snapshot Task")
        self.url = reverse("financial-control")
        self.generate_url = reverse("financial-control-generate-cost-snapshots")
        self.outsider = make_member()
        resource = Resource.objects.create(code="SNAP-COST", name="Snapshot Cost", resource_type=Resource.COST)
        assignment = Assignment.objects.create(revision=self.revision, task=self.task, resource=resource, units_percent=Decimal("100.00"))
        self.funding_source = FundingSource.objects.create(title="Snapshot Funding", source_type="CONTRACT", received_date=timezone.localdate(), total_amount=Decimal("100.00"), currency="IRR", status="APPROVED", created_by=self.admin)
        self.budget = BudgetAllocation.objects.create(funding_source=self.funding_source, project=self.project, revision=self.revision, scope_type="TASK", task=self.task, cost_type="COST", allocated_amount=Decimal("100.00"), status="APPROVED", created_by=self.admin)
        self.usd_source = FundingSource.objects.create(title="Snapshot USD Funding", source_type="CONTRACT", received_date=timezone.localdate(), total_amount=Decimal("50.00"), currency="USD", status="APPROVED", created_by=self.admin)
        self.usd_budget = BudgetAllocation.objects.create(funding_source=self.usd_source, project=self.project, revision=self.revision, scope_type="TASK", task=self.task, cost_type="COST", allocated_amount=Decimal("50.00"), status="APPROVED", created_by=self.admin)
        self.plan = TaskFinancialPlan.objects.create(task=self.task, direction=TaskFinancialPlan.DIRECTION_PAYABLE, contract_amount=Decimal("100.00"), currency="IRR", status=TaskFinancialPlan.STATUS_ACTIVE, created_by=self.admin)
        self.milestone = PaymentMilestone.objects.create(financial_plan=self.plan, title="Snapshot Gate", sequence=1, trigger_type=PaymentMilestone.TRIGGER_BEFORE_START, amount_type=PaymentMilestone.AMOUNT_FIXED, fixed_amount=Decimal("100.00"))
        self.cost = CostTransaction.objects.create(project=self.project, revision=self.revision, task=self.task, assignment=assignment, financial_plan=self.plan, transaction_type="COST", amount=Decimal("25.00"), currency="IRR", transaction_date=timezone.localdate(), created_by=self.admin)
        self.allocation = CostTransactionMilestoneAllocation.objects.create(cost_transaction=self.cost, milestone=self.milestone, allocated_amount=Decimal("20.00"))
        self.payment = PaymentTransaction.objects.create(milestone=self.milestone, transaction_type=PaymentTransaction.TYPE_PAYMENT, amount=Decimal("10.00"), transaction_date=timezone.localdate(), created_by=self.admin)
        make_report(self.task, self.admin, progress=50, approval_status="reviewer_approved")

    def test_missing_cost_snapshot_is_reported_without_auto_generation(self):
        before = VarianceReport.objects.filter(dimension=VarianceReport.DIMENSION_COST).count()
        response = api(self.admin).get(self.url, {"project_id": self.project.id})
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertIn("missing_cost_evm_snapshot", {item["code"] for item in response.data["warnings"]})
        self.assertEqual(VarianceReport.objects.filter(dimension=VarianceReport.DIMENSION_COST).count(), before)

    def test_cost_snapshot_requires_currency_for_financial_control(self):
        VarianceReport.objects.create(task=self.task, revision=self.revision, report_date=timezone.localdate(), dimension=VarianceReport.DIMENSION_COST, currency="IRR", budget_at_completion=Decimal("100.00"), planned_value=Decimal("50.00"), earned_value=Decimal("25.00"), actual_cost=Decimal("25.00"))
        response = api(self.admin).get(self.url, {"project_id": self.project.id})
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["summary_by_currency"][0]["ac"], "25.00")

    def generate(self, **payload):
        data = {
            "project_id": self.project.id,
            "status_date": timezone.localdate().isoformat(),
            "currency": "IRR",
            **payload,
        }
        response = api(self.admin).post(self.generate_url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return response.data

    def test_creates_cost_snapshot_with_required_identity(self):
        result = self.generate()
        self.assertEqual(result["created"], 1)
        snapshot = VarianceReport.objects.get(task=self.task, dimension=VarianceReport.DIMENSION_COST, currency="IRR")
        self.assertEqual(snapshot.revision_id, self.revision.id)
        self.assertEqual(snapshot.actual_cost, Decimal("25.00"))

    def test_required_currency_and_invalid_date_are_rejected(self):
        missing = api(self.admin).post(self.generate_url, {"project_id": self.project.id, "status_date": timezone.localdate().isoformat()}, format="json")
        self.assertEqual(missing.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("currency", missing.data)
        invalid = api(self.admin).post(self.generate_url, {"project_id": self.project.id, "status_date": "bad", "currency": "IRR"}, format="json")
        self.assertEqual(invalid.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("status_date", invalid.data)
        invalid_currency = api(self.admin).post(self.generate_url, {"project_id": self.project.id, "status_date": timezone.localdate().isoformat(), "currency": "bad!"}, format="json")
        self.assertEqual(invalid_currency.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("currency", invalid_currency.data)

    def test_effort_reports_are_untouched(self):
        effort = VarianceReport.objects.create(task=self.task, revision=self.revision, report_date=timezone.localdate(), dimension=VarianceReport.DIMENSION_EFFORT, budget_at_completion=Decimal("1.00"))
        self.generate()
        effort.refresh_from_db()
        self.assertEqual(effort.dimension, VarianceReport.DIMENSION_EFFORT)
        self.assertIsNone(effort.currency)

    def test_same_date_currencies_coexist_and_rerun_is_idempotent(self):
        self.generate(currency="IRR")
        usd = self.generate(currency="USD")
        self.assertEqual(usd["created"], 1)
        rerun = self.generate(currency="IRR")
        self.assertEqual(rerun["created"], 0)
        self.assertGreaterEqual(rerun["skipped"], 1)
        self.assertEqual(VarianceReport.objects.filter(task=self.task, dimension=VarianceReport.DIMENSION_COST).count(), 2)

    def test_source_change_updates_existing_snapshot(self):
        self.generate()
        self.cost.amount = Decimal("40.00")
        self.cost.save(update_fields=["amount"])
        result = self.generate()
        self.assertEqual(result["updated"], 1)
        snapshot = VarianceReport.objects.get(task=self.task, dimension=VarianceReport.DIMENSION_COST, currency="IRR")
        self.assertEqual(snapshot.actual_cost, Decimal("40.00"))

    def test_financial_control_reads_matching_currency_only(self):
        VarianceReport.objects.create(task=self.task, revision=self.revision, report_date=timezone.localdate(), dimension=VarianceReport.DIMENSION_COST, currency="USD", budget_at_completion=Decimal("50.00"), planned_value=Decimal("10.00"), earned_value=Decimal("5.00"), actual_cost=Decimal("5.00"))
        response = api(self.admin).get(self.url, {"project_id": self.project.id, "currency": "IRR"})
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertIn("missing_cost_evm_snapshot", {item["code"] for item in response.data["warnings"]})
        self.generate(currency="IRR")
        response = api(self.admin).get(self.url, {"project_id": self.project.id, "currency": "IRR"})
        self.assertNotIn("missing_cost_evm_snapshot", {item["code"] for item in response.data["warnings"]})

    def test_missing_snapshot_remains_for_other_currency(self):
        self.generate(currency="IRR")
        response = api(self.admin).get(self.url, {"project_id": self.project.id, "currency": "USD"})
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertIn("missing_cost_evm_snapshot", {item["code"] for item in response.data["warnings"]})

    def test_payment_only_currency_does_not_require_cost_snapshot(self):
        payment_task, _ = make_task(self.project, self.revision, title="Payment Currency Task")
        plan = TaskFinancialPlan.objects.create(
            task=payment_task,
            direction=TaskFinancialPlan.DIRECTION_PAYABLE,
            contract_amount=Decimal("100.00"),
            currency="IRR",
            status=TaskFinancialPlan.STATUS_ACTIVE,
            created_by=self.admin,
        )
        milestone = PaymentMilestone.objects.create(
            financial_plan=plan,
            title="Payment Currency Gate",
            sequence=1,
            trigger_type=PaymentMilestone.TRIGGER_MANUAL,
            amount_type=PaymentMilestone.AMOUNT_FIXED,
            fixed_amount=Decimal("100.00"),
        )
        PaymentTransaction.objects.create(
            milestone=milestone,
            transaction_type=PaymentTransaction.TYPE_PAYMENT,
            amount=Decimal("10.00"),
            currency="USD",
            transaction_date=timezone.localdate(),
            created_by=self.admin,
        )
        response = api(self.admin).get(self.url, {"project_id": self.project.id, "task_id": payment_task.id})
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        codes = {item["code"] for item in response.data["warnings"]}
        self.assertIn("payment_currency_differs_from_plan", codes)
        self.assertNotIn("missing_cost_evm_snapshot", codes)

    def test_permission_denied_and_task_project_mismatch(self):
        denied = api(self.outsider).post(self.generate_url, {"project_id": self.project.id, "status_date": timezone.localdate().isoformat(), "currency": "IRR"}, format="json")
        self.assertEqual(denied.status_code, status.HTTP_403_FORBIDDEN)
        other_project = make_project(creator=self.admin, scope="intra_unit", name="Other Snapshot Project")
        other_revision = make_revision(other_project, creator=self.admin, approved=True, is_baseline=True)
        other_task, _ = make_task(other_project, other_revision, title="Other Task")
        mismatch = api(self.admin).post(self.generate_url, {"project_id": self.project.id, "status_date": timezone.localdate().isoformat(), "currency": "IRR", "task_ids": [str(other_task.id)]}, format="json")
        self.assertEqual(mismatch.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("task_ids", mismatch.data)

    def test_ledger_objects_are_unchanged(self):
        before = (self.cost.amount, self.payment.amount, self.allocation.allocated_amount)
        self.generate()
        self.cost.refresh_from_db()
        self.payment.refresh_from_db()
        self.allocation.refresh_from_db()
        self.assertEqual((self.cost.amount, self.payment.amount, self.allocation.allocated_amount), before)

    def test_service_skips_non_calculable_task_and_limits_task_ids(self):
        empty_task, _ = make_task(self.project, self.revision, title="Empty Snapshot Task")
        result = generate_cost_variance_reports(project=self.project, status_date=timezone.localdate().isoformat(), currency="IRR", task_ids=[empty_task.id], actor=self.admin)
        self.assertEqual(result["created"], 0)
        self.assertEqual(result["skipped"], 1)
        self.assertFalse(VarianceReport.objects.filter(task=empty_task, dimension=VarianceReport.DIMENSION_COST).exists())
