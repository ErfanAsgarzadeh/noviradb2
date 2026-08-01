"""
test_evm_engine.py  —  تست موتور EVM (Earned Value Management)
پوشش:
  - محاسبه PV (Planned Value)
  - محاسبه EV (Earned Value) از TaskActual
  - SPI و CPI
  - variance/calculate endpoint
"""
import pytest
from django.utils import timezone
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework import status
from datetime import timedelta
from decimal import Decimal

from ktcPlanning.variance_engine import EVMEngine
from ktcPlanning.models import (
    VarianceReport, TaskActual, TaskVersion, Revision,
    UnitOfMeasure, ExpenseType, CostTransaction, TaskReportLog,
    FundingSource, BudgetAllocation, TaskFinancialPlan, PaymentMilestone,
    PaymentTransaction,
)
from .factories import (
    make_company_admin, make_project, make_revision,
    make_task, make_report, make_wbs_node,
)


def api(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def set_task_actual(task_version, progress, actual_start=None):
    from ktcPlanning.models import TaskActual
    ta, _ = TaskActual.objects.get_or_create(
        task_version=task_version,
        defaults={"updated_by": task_version.task.created_by}
    )
    ta.progress = progress
    ta.actual_start = actual_start or timezone.now() - timedelta(hours=4)
    ta.updated_by = task_version.task.created_by
    ta.save()
    return ta


def make_approved_task_budget(project, revision, task, wbs_node, user, amount=Decimal("1000.00")):
    source = FundingSource.objects.create(
        title="Approved source",
        source_type="INTERNAL_CAPITAL",
        received_date=timezone.now().date(),
        total_amount=amount,
        status="APPROVED",
        created_by=user,
    )
    return BudgetAllocation.objects.create(
        funding_source=source,
        project=project,
        revision=revision,
        scope_type="TASK",
        wbs_node=wbs_node,
        task=task,
        cost_type="COST",
        allocated_amount=amount,
        status="APPROVED",
        created_by=user,
    )


# ══════════════════════════════════════════════════════════
#  PV محاسبات
# ══════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestPlannedValue:

    def test_pv_is_full_bac_when_past_finish(self):
        admin = make_company_admin()
        project = make_project(creator=admin)
        revision = make_revision(project, creator=admin, is_baseline=True)
        task, tv = make_task(project, revision, duration_hours=8)

        # planned_finish را در گذشته قرار می‌دهیم
        tv.planned_start = timezone.now() - timedelta(hours=10)
        tv.planned_finish = timezone.now() - timedelta(hours=2)
        tv.save()

        engine = EVMEngine(project_id=project.id, data_datetime=timezone.now())
        pv, bac = engine._calculate_task_pv(tv)
        assert pv == bac

    def test_pv_is_zero_before_start(self):
        admin = make_company_admin()
        project = make_project(creator=admin)
        revision = make_revision(project, creator=admin, is_baseline=True)
        task, tv = make_task(project, revision, duration_hours=8)

        tv.planned_start = timezone.now() + timedelta(hours=10)
        tv.planned_finish = timezone.now() + timedelta(hours=18)
        tv.save()

        # data_datetime قبل از شروع تسک
        engine = EVMEngine(
            project_id=project.id,
            data_datetime=timezone.now()
        )
        pv, bac = engine._calculate_task_pv(tv)
        assert pv == Decimal("0.00")

    def test_pv_partial_when_in_progress(self):
        admin = make_company_admin()
        project = make_project(creator=admin)
        revision = make_revision(project, creator=admin, is_baseline=True)
        task, tv = make_task(project, revision, duration_hours=8)

        now = timezone.now()
        tv.planned_start = now - timedelta(hours=4)
        tv.planned_finish = now + timedelta(hours=4)
        tv.save()

        engine = EVMEngine(project_id=project.id, data_datetime=now)
        pv, bac = engine._calculate_task_pv(tv)
        # باید بین 0 و BAC باشد
        assert Decimal("0") < pv < bac


# ══════════════════════════════════════════════════════════
#  SPI و CPI محاسبه
# ══════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestEVMCalculation:

    def setup_method(self):
        self.admin = make_company_admin()
        self.project = make_project(creator=self.admin)
        self.revision = make_revision(self.project, creator=self.admin, is_baseline=True)
        self.task, self.tv = make_task(
            self.project, self.revision, duration_hours=8
        )
        # تسک در گذشته برنامه‌ریزی شده
        now = timezone.now()
        self.tv.planned_start = now - timedelta(hours=10)
        self.tv.planned_finish = now - timedelta(hours=2)
        self.tv.save()

    def test_spi_equals_one_when_on_schedule(self):
        """EV == PV → SPI = 1"""
        set_task_actual(self.tv, progress=100)

        engine = EVMEngine(project_id=self.project.id)
        engine.run_task_level_variances()

        report = VarianceReport.objects.filter(
            task=self.task, revision=self.revision
        ).first()
        assert report is not None
        assert float(report.spi) == pytest.approx(1.0, abs=0.05)

    def test_spi_less_than_one_when_behind_schedule(self):
        """EV < PV → SPI < 1 → تأخیر"""
        set_task_actual(self.tv, progress=50)

        engine = EVMEngine(project_id=self.project.id)
        engine.run_task_level_variances()

        report = VarianceReport.objects.filter(
            task=self.task, revision=self.revision
        ).first()
        assert report is not None
        assert float(report.spi) < 1.0

    def test_variance_report_stored_in_db(self):
        set_task_actual(self.tv, progress=80)

        engine = EVMEngine(project_id=self.project.id)
        engine.run_task_level_variances()

        assert VarianceReport.objects.filter(
            task=self.task, revision=self.revision
        ).exists()

    def test_bac_matches_task_duration_hours(self):
        set_task_actual(self.tv, progress=100)

        engine = EVMEngine(project_id=self.project.id)
        engine.run_task_level_variances()

        report = VarianceReport.objects.get(
            task=self.task, revision=self.revision
        )
        assert float(report.budget_at_completion) == pytest.approx(
            float(self.tv.duration_hours), abs=0.01
        )

    def test_effort_actual_cost_prefers_approved_report_hours(self):
        set_task_actual(self.tv, progress=100)
        TaskReportLog.objects.create(
            task=self.task,
            user=self.admin,
            status="on-track",
            progress_percent=100,
            time_spent_hours=99,
            is_approved=True,
        )
        unit = UnitOfMeasure.objects.create(code="EA", name="Each")
        expense_type = ExpenseType.objects.create(
            name="Direct Cost",
            unit=unit,
        )
        CostTransaction.objects.create(
            project=self.project,
            revision=self.revision,
            task=self.task,
            transaction_type="EXPENSE",
            transaction_date=timezone.now().date(),
            quantity=Decimal("3.00"),
            expense_rate=Decimal("10.00"),
            expense_type=expense_type,
            created_by=self.admin,
        )

        engine = EVMEngine(project_id=self.project.id)
        engine.run_task_level_variances()

        report = VarianceReport.objects.get(
            task=self.task, revision=self.revision
        )
        assert report.actual_cost == Decimal("99.00")


# ══════════════════════════════════════════════════════════
#  calculate endpoint
# ══════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestVarianceCalculateEndpoint:

    def test_calculate_returns_success(self):
        admin = make_company_admin()
        project = make_project(creator=admin)
        revision = make_revision(project, creator=admin, is_baseline=True)
        task, tv = make_task(project, revision, duration_hours=8)
        now = timezone.now()
        tv.planned_start = now - timedelta(hours=10)
        tv.planned_finish = now - timedelta(hours=2)
        tv.save()
        set_task_actual(tv, progress=80)

        resp = api(admin).post(
            reverse("variance-report-calculate"),
            {"project_id": str(project.id)},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data.get("status")

    def test_calculate_without_project_id_returns_400(self):
        admin = make_company_admin()
        resp = api(admin).post(
            reverse("variance-report-calculate"),
            {},
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_calculate_requires_authentication(self):
        admin = make_company_admin()
        project = make_project(creator=admin)

        resp = APIClient().post(
            reverse("variance-report-calculate"),
            {"project_id": str(project.id)},
            format="json",
        )
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_calculate_uses_selected_revision_when_provided(self):
        admin = make_company_admin()
        project = make_project(creator=admin)
        project.refresh_from_db()
        baseline = project.active_baseline_revision
        task, baseline_tv = make_task(project, baseline, duration_hours=8)
        execution_revision = make_revision(project, creator=admin, approved=True)
        selected_revision = make_revision(project, creator=admin)
        _, selected_wbs = make_wbs_node(project, selected_revision, title="Rev02 WBS")
        now = timezone.now()

        baseline_tv.planned_start = now - timedelta(hours=10)
        baseline_tv.planned_finish = now - timedelta(hours=2)
        baseline_tv.save()
        selected_tv = TaskVersion.objects.create(
            task=task,
            revision=selected_revision,
            wbs_node=selected_wbs,
            title="Rev02 Activity",
            duration_hours=8,
            planned_start=now - timedelta(hours=10),
            planned_finish=now - timedelta(hours=2),
            sequence=1,
        )
        set_task_actual(selected_tv, progress=80, actual_start=now - timedelta(hours=4))
        new_task, _ = make_task(project, selected_revision, title="Rev02 New Activity", duration_hours=6)

        resp = api(admin).post(
            reverse("variance-report-calculate"),
            {
                "project_id": str(project.id),
                "revision_id": str(selected_revision.id),
                "dataDate": now.isoformat(),
            },
            format="json",
        )

        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["revisionId"] == str(selected_revision.id)
        assert VarianceReport.objects.filter(task=task, revision=selected_revision).exists()
        assert VarianceReport.objects.filter(task=new_task, revision=selected_revision).exists()
        assert not VarianceReport.objects.filter(task=task, revision=execution_revision).exists()


@pytest.mark.django_db
class TestCostEVM:

    def setup_method(self):
        self.admin = make_company_admin()
        self.project = make_project(creator=self.admin)
        self.revision = make_revision(self.project, creator=self.admin, is_baseline=True)
        self.task, self.tv = make_task(self.project, self.revision, duration_hours=48)
        self.status_datetime = timezone.now()
        self.tv.planned_start = self.status_datetime - timedelta(days=1)
        self.tv.planned_finish = self.status_datetime + timedelta(days=1)
        self.tv.save()
        make_approved_task_budget(
            self.project,
            self.revision,
            self.task,
            self.tv.wbs_node,
            self.admin,
            Decimal("1000.00"),
        )
        report = make_report(
            self.task,
            self.admin,
            progress=40,
            approval_status="final_approved",
        )
        TaskReportLog.objects.filter(pk=report.pk).update(timestamp=self.status_datetime)
        CostTransaction.objects.create(
            project=self.project,
            revision=self.revision,
            task=self.task,
            transaction_type="COST",
            transaction_date=self.status_datetime.date(),
            amount=Decimal("600.00"),
            created_by=self.admin,
        )

    def test_cost_evm_uses_budget_cost_transactions_and_approved_progress(self):
        rows = EVMEngine(
            project_id=self.project.id,
            data_datetime=self.status_datetime,
        ).run_cost_task_variances(revision_id=self.revision.id)

        assert len(rows) == 1
        row = rows[0]
        assert row["dimension"] == "cost"
        assert row["budget_at_completion"] == "1000.00"
        assert row["planned_value"] == "500.00"
        assert row["earned_value"] == "400.00"
        assert row["actual_cost"] == "600.00"
        assert row["cost_variance"] == "-200.00"
        assert row["schedule_variance"] == "-100.00"
        assert row["cpi"] == "0.6667"
        assert row["spi"] == "0.8000"
        assert row["estimate_at_completion"] == "1500.00"
        assert row["estimate_to_complete"] == "900.00"
        assert row["variance_at_completion"] == "-500.00"
        assert row["tcpi_bac"] == "1.5000"
        assert row["approved_progress_percent"] == "40.00"
        assert row["planned_progress_percent"] == "50.00"

    def test_cost_evm_does_not_include_payment_transactions_in_ac(self):
        plan = TaskFinancialPlan.objects.create(
            task=self.task,
            direction=TaskFinancialPlan.DIRECTION_PAYABLE,
            contract_amount=Decimal("1000.00"),
            status=TaskFinancialPlan.STATUS_ACTIVE,
            created_by=self.admin,
        )
        milestone = PaymentMilestone.objects.create(
            financial_plan=plan,
            title="First",
            sequence=1,
            trigger_type=PaymentMilestone.TRIGGER_MANUAL,
            amount_type=PaymentMilestone.AMOUNT_FIXED,
            fixed_amount=Decimal("200.00"),
            status=PaymentMilestone.STATUS_PAID,
        )
        PaymentTransaction.objects.create(
            milestone=milestone,
            transaction_type=PaymentTransaction.TYPE_PAYMENT,
            amount=Decimal("200.00"),
            transaction_date=self.status_datetime.date(),
            created_by=self.admin,
        )

        rows = EVMEngine(
            project_id=self.project.id,
            data_datetime=self.status_datetime,
        ).run_cost_task_variances(revision_id=self.revision.id)

        assert rows[0]["actual_cost"] == "600.00"

    def test_cost_evm_ac_uses_transaction_amount_when_partially_allocated(self):
        from ktcPlanning.financial_services import allocate_cost_transaction_to_milestones
        from ktcPlanning.models import PaymentMilestone, TaskFinancialPlan

        plan = TaskFinancialPlan.objects.create(
            task=self.task,
            direction=TaskFinancialPlan.DIRECTION_PAYABLE,
            contract_amount=Decimal("300.00"),
            status=TaskFinancialPlan.STATUS_ACTIVE,
            created_by=self.admin,
        )
        PaymentMilestone.objects.create(
            financial_plan=plan,
            title="First",
            sequence=1,
            trigger_type=PaymentMilestone.TRIGGER_BEFORE_START,
            amount_type=PaymentMilestone.AMOUNT_FIXED,
            fixed_amount=Decimal("300.00"),
        )
        tx = CostTransaction.objects.get(task=self.task)
        tx.financial_plan = plan
        tx.save()
        allocate_cost_transaction_to_milestones(tx)

        rows = EVMEngine(
            project_id=self.project.id,
            data_datetime=self.status_datetime,
        ).run_cost_task_variances(revision_id=self.revision.id)

        assert rows[0]["actual_cost"] == "600.00"
        assert rows[0]["has_actual_cost"] is True

    def test_cost_evm_ac_uses_transaction_amount_without_allocation(self):
        rows = EVMEngine(
            project_id=self.project.id,
            data_datetime=self.status_datetime,
        ).run_cost_task_variances(revision_id=self.revision.id)

        assert rows[0]["actual_cost"] == "600.00"

    def test_cost_evm_ac_sums_multiple_transactions(self):
        CostTransaction.objects.create(
            project=self.project,
            revision=self.revision,
            task=self.task,
            transaction_type="COST",
            transaction_date=self.status_datetime.date(),
            amount=Decimal("300.00"),
            created_by=self.admin,
        )

        rows = EVMEngine(
            project_id=self.project.id,
            data_datetime=self.status_datetime,
        ).run_cost_task_variances(revision_id=self.revision.id)

        assert rows[0]["actual_cost"] == "900.00"

    def test_cost_evm_endpoint_rejects_invalid_dimension(self):
        resp = api(self.admin).get(
            reverse("variance-report-list"),
            {"revision_id": str(self.revision.id), "dimension": "money"},
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "dimension" in resp.data

    def test_cost_evm_endpoint_rejects_invalid_status_date(self):
        resp = api(self.admin).get(
            reverse("variance-report-list"),
            {
                "revision_id": str(self.revision.id),
                "dimension": "cost",
                "status_date": "not-a-date",
            },
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "status_date" in resp.data
