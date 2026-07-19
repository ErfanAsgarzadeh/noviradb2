from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from ktcPlanning.financial_services import (
    activate_plan,
    get_task_financial_status,
    register_transaction,
    validate_progress_transition,
    validate_task_delivery,
    validate_task_start,
)
from ktcPlanning.models import PaymentMilestone, PaymentTransaction, TaskFinancialPlan, TaskReportLog, CostTransaction
from tests.factories import assign_role, make_company_admin, make_member, make_project, make_revision, make_task


def api(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def financial_setup():
    admin = make_company_admin()
    project = make_project(creator=admin, scope="intra_unit")
    revision = make_revision(project, creator=admin, approved=True)
    task, task_version = make_task(project, revision, title="Task A")
    reviewer = make_member()
    assign_role(task, revision, reviewer, role="reviewer")
    return {"admin": admin, "project": project, "revision": revision, "task": task, "task_version": task_version, "reviewer": reviewer}


def make_plan(task, user, amount="100000000.00", direction=TaskFinancialPlan.DIRECTION_PAYABLE):
    return TaskFinancialPlan.objects.create(
        task=task,
        direction=direction,
        contract_amount=Decimal(amount),
        currency="IRR",
        created_by=user,
    )


def add_30_30_40(plan):
    advance = PaymentMilestone.objects.create(
        financial_plan=plan,
        title="Advance",
        sequence=1,
        trigger_type=PaymentMilestone.TRIGGER_BEFORE_START,
        amount_type=PaymentMilestone.AMOUNT_PERCENTAGE,
        percentage=Decimal("30.00"),
        blocks_task_start=True,
    )
    middle = PaymentMilestone.objects.create(
        financial_plan=plan,
        title="Progress 50",
        sequence=2,
        trigger_type=PaymentMilestone.TRIGGER_APPROVED_PROGRESS,
        amount_type=PaymentMilestone.AMOUNT_PERCENTAGE,
        percentage=Decimal("30.00"),
        progress_threshold=Decimal("50.00"),
        blocks_progress_after_threshold=True,
    )
    final = PaymentMilestone.objects.create(
        financial_plan=plan,
        title="Final settlement",
        sequence=3,
        trigger_type=PaymentMilestone.TRIGGER_BEFORE_DELIVERY,
        amount_type=PaymentMilestone.AMOUNT_PERCENTAGE,
        percentage=Decimal("40.00"),
        blocks_task_delivery=True,
    )
    return advance, middle, final


@pytest.mark.django_db
class TestTaskFinancialPlanService:
    def test_create_payable_and_receivable_plans(self, financial_setup):
        payable = make_plan(financial_setup["task"], financial_setup["admin"])
        other_task, _ = make_task(financial_setup["project"], financial_setup["revision"], title="Task B")
        receivable = make_plan(other_task, financial_setup["admin"], direction=TaskFinancialPlan.DIRECTION_RECEIVABLE)

        assert payable.direction == "payable"
        assert receivable.direction == "receivable"

    def test_activation_requires_complete_milestone_total(self, financial_setup):
        plan = make_plan(financial_setup["task"], financial_setup["admin"])
        PaymentMilestone.objects.create(
            financial_plan=plan,
            title="Only 30",
            sequence=1,
            trigger_type=PaymentMilestone.TRIGGER_MANUAL,
            amount_type=PaymentMilestone.AMOUNT_PERCENTAGE,
            percentage=Decimal("30.00"),
        )

        with pytest.raises(Exception):
            activate_plan(plan)

    def test_30_30_40_status_and_milestone_amounts(self, financial_setup):
        plan = make_plan(financial_setup["task"], financial_setup["admin"])
        advance, middle, final = add_30_30_40(plan)
        activate_plan(plan)

        status_payload = get_task_financial_status(financial_setup["task"])
        assert status_payload["contract_amount"] == "100000000.00"
        assert status_payload["can_start"] is False
        assert status_payload["can_deliver"] is False
        assert status_payload["milestones"][0]["calculated_amount"] == "30000000.00"
        assert status_payload["milestones"][2]["calculated_amount"] == "40000000.00"

    def test_payment_refund_adjustment_net_paid_and_outstanding(self, financial_setup):
        plan = make_plan(financial_setup["task"], financial_setup["admin"])
        advance, _, _ = add_30_30_40(plan)
        activate_plan(plan)

        register_transaction(advance, PaymentTransaction.TYPE_PAYMENT, Decimal("20000000.00"), timezone.localdate(), financial_setup["admin"])
        register_transaction(advance, PaymentTransaction.TYPE_ADJUSTMENT, Decimal("5000000.00"), timezone.localdate(), financial_setup["admin"])
        register_transaction(advance, PaymentTransaction.TYPE_REFUND, Decimal("2000000.00"), timezone.localdate(), financial_setup["admin"])

        status_payload = get_task_financial_status(financial_setup["task"])
        first = status_payload["milestones"][0]
        assert first["paid_amount"] == "23000000.00"
        assert first["outstanding"] == "7000000.00"
        assert first["status"] == PaymentMilestone.STATUS_PARTIALLY_PAID

    def test_overpayment_is_rejected(self, financial_setup):
        plan = make_plan(financial_setup["task"], financial_setup["admin"])
        advance, _, _ = add_30_30_40(plan)
        activate_plan(plan)

        with pytest.raises(Exception):
            register_transaction(advance, PaymentTransaction.TYPE_PAYMENT, Decimal("30000001.00"), timezone.localdate(), financial_setup["admin"])

    def test_start_progress_and_delivery_gates(self, financial_setup):
        plan = make_plan(financial_setup["task"], financial_setup["admin"])
        advance, middle, final = add_30_30_40(plan)
        activate_plan(plan)

        with pytest.raises(Exception):
            validate_task_start(financial_setup["task"])

        register_transaction(advance, PaymentTransaction.TYPE_PAYMENT, Decimal("30000000.00"), timezone.localdate(), financial_setup["admin"])
        validate_task_start(financial_setup["task"])

        TaskReportLog.objects.create(
            task=financial_setup["task"],
            user=financial_setup["reviewer"],
            progress_percent=50,
            approval_status="final_approved",
        )
        validate_progress_transition(financial_setup["task"], Decimal("50.00"))
        with pytest.raises(Exception):
            validate_progress_transition(financial_setup["task"], Decimal("51.00"))

        register_transaction(middle, PaymentTransaction.TYPE_PAYMENT, Decimal("30000000.00"), timezone.localdate(), financial_setup["admin"])
        validate_progress_transition(financial_setup["task"], Decimal("80.00"))

        with pytest.raises(Exception):
            validate_task_delivery(financial_setup["task"])
        register_transaction(final, PaymentTransaction.TYPE_PAYMENT, Decimal("40000000.00"), timezone.localdate(), financial_setup["admin"])
        validate_task_delivery(financial_setup["task"])

    def test_unapproved_report_does_not_unlock_progress_milestone(self, financial_setup):
        plan = make_plan(financial_setup["task"], financial_setup["admin"])
        advance, middle, _ = add_30_30_40(plan)
        activate_plan(plan)
        register_transaction(advance, PaymentTransaction.TYPE_PAYMENT, Decimal("30000000.00"), timezone.localdate(), financial_setup["admin"])
        TaskReportLog.objects.create(
            task=financial_setup["task"],
            user=financial_setup["reviewer"],
            progress_percent=50,
            approval_status="pending",
        )

        status_payload = get_task_financial_status(financial_setup["task"])
        progress_row = next(item for item in status_payload["milestones"] if item["id"] == middle.id)
        assert progress_row["status"] == PaymentMilestone.STATUS_LOCKED

    def test_payment_transaction_does_not_create_cost_transaction_or_ac(self, financial_setup):
        plan = make_plan(financial_setup["task"], financial_setup["admin"])
        advance, _, _ = add_30_30_40(plan)
        activate_plan(plan)
        register_transaction(advance, PaymentTransaction.TYPE_PAYMENT, Decimal("30000000.00"), timezone.localdate(), financial_setup["admin"])

        assert PaymentTransaction.objects.count() == 1
        assert CostTransaction.objects.count() == 0


@pytest.mark.django_db
class TestTaskFinancialPlanApi:
    def test_api_create_activate_and_record_payment(self, financial_setup):
        client = api(financial_setup["admin"])
        plan_resp = client.post(reverse("task-financial-plan-list"), {
            "task": str(financial_setup["task"].id),
            "direction": "payable",
            "contract_amount": "100000000.00",
            "currency": "IRR",
        }, format="json")
        assert plan_resp.status_code == status.HTTP_201_CREATED
        plan_id = plan_resp.data["id"]

        for payload in [
            {"title": "Advance", "sequence": 1, "trigger_type": "before_start", "amount_type": "percentage", "percentage": "30.00", "blocks_task_start": True},
            {"title": "Progress", "sequence": 2, "trigger_type": "approved_progress", "amount_type": "percentage", "percentage": "30.00", "progress_threshold": "50.00", "blocks_progress_after_threshold": True},
            {"title": "Final", "sequence": 3, "trigger_type": "before_delivery", "amount_type": "percentage", "percentage": "40.00", "blocks_task_delivery": True},
        ]:
            payload["financial_plan"] = plan_id
            resp = client.post(reverse("payment-milestone-list"), payload, format="json")
            assert resp.status_code == status.HTTP_201_CREATED

        activate_resp = client.post(reverse("task-financial-plan-activate", kwargs={"pk": plan_id}))
        assert activate_resp.status_code == status.HTTP_200_OK
        milestone_id = activate_resp.data["milestones"][0]["id"]
        tx_resp = client.post(reverse("payment-milestone-record-transaction", kwargs={"pk": milestone_id}), {
            "transaction_type": "payment",
            "amount": "30000000.00",
            "transaction_date": timezone.localdate().isoformat(),
        }, format="json")
        assert tx_resp.status_code == status.HTTP_201_CREATED

        status_resp = client.get(reverse("task-financial-plan-status-summary"), {"task_id": str(financial_setup["task"].id)})
        assert status_resp.status_code == status.HTTP_200_OK
        assert status_resp.data["can_start"] is True