from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from ktcPlanning.financial_services import (
    activate_plan,
    allocate_cost_transaction_to_milestones,
    allocate_plan_payment,
    approve_task_delivery,
    evaluate_milestone_eligibility,
    get_effective_task_delivery,
    get_task_delivery_attachment_capabilities,
    get_task_delivery_capabilities,
    get_task_financial_status,
    refresh_plan_cost_allocations,
    reject_task_delivery,
    register_transaction,
    submit_task_delivery,
    validate_progress_transition,
    validate_task_delivery,
    validate_task_start,
)
from ktcPlanning.models import Assignment, CostTransaction, CostTransactionMilestoneAllocation, PaymentMilestone, PaymentTransaction, Resource, ResourceRate, TaskDelivery, TaskDeliveryAttachment, TaskFinancialPlan, TaskReportLog
from ktcPlanning.serializers import TaskFinancialPlanSerializer
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


def make_cost_transaction(task, revision, user, amount="100000000.00", resource_type=Resource.COST, transaction_type="COST", project=None, financial_plan=None):
    resource = Resource.objects.create(
        code=f"RES-{Resource.objects.count() + 1}",
        name=f"{resource_type} Resource {Resource.objects.count() + 1}",
        resource_type=resource_type,
    )
    assignment = Assignment.objects.create(
        revision=revision,
        task=task,
        resource=resource,
        units_percent=Decimal("100.00"),
    )
    payload = {
        "project": project or task.project,
        "revision": revision,
        "task": task,
        "assignment": assignment,
        "transaction_type": transaction_type,
        "transaction_date": timezone.localdate(),
        "created_by": user,
    }
    if financial_plan is not None:
        payload["financial_plan"] = financial_plan
    if transaction_type == "COST":
        payload["amount"] = Decimal(amount)
    else:
        payload["quantity"] = Decimal("1.00")
        payload["resource_rate"] = ResourceRate.objects.create(
            resource=resource,
            effective_from=timezone.localdate(),
            regular_rate=Decimal(amount),
        )
    return CostTransaction.objects.create(**payload)


def make_plan(task, user, amount="100000000.00", direction=TaskFinancialPlan.DIRECTION_PAYABLE):
    return TaskFinancialPlan.objects.create(
        task=task,
        direction=direction,
        contract_amount=Decimal(amount),
        currency="IRR",
        created_by=user,
    )


def plan_payload(task, amount="100000000.00", direction=TaskFinancialPlan.DIRECTION_PAYABLE):
    return {
        "task": str(task.id),
        "direction": direction,
        "contract_amount": amount,
        "currency": "IRR",
    }


def assert_plan_payload_invalid(payload, error_key):
    serializer = TaskFinancialPlanSerializer(data=payload)
    assert serializer.is_valid() is False
    assert error_key in serializer.errors

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


def add_cost_recognition_milestones(plan):
    first = PaymentMilestone.objects.create(
        financial_plan=plan,
        title="M1",
        sequence=1,
        trigger_type=PaymentMilestone.TRIGGER_BEFORE_START,
        amount_type=PaymentMilestone.AMOUNT_FIXED,
        fixed_amount=Decimal("300.00"),
    )
    second = PaymentMilestone.objects.create(
        financial_plan=plan,
        title="M2",
        sequence=2,
        trigger_type=PaymentMilestone.TRIGGER_APPROVED_PROGRESS,
        amount_type=PaymentMilestone.AMOUNT_FIXED,
        fixed_amount=Decimal("400.00"),
        progress_threshold=Decimal("50.00"),
    )
    third = PaymentMilestone.objects.create(
        financial_plan=plan,
        title="M3",
        sequence=3,
        trigger_type=PaymentMilestone.TRIGGER_TASK_COMPLETION,
        amount_type=PaymentMilestone.AMOUNT_FIXED,
        fixed_amount=Decimal("300.00"),
    )
    return first, second, third


def add_before_delivery_milestones(plan):
    first = PaymentMilestone.objects.create(
        financial_plan=plan,
        title="M1 before start",
        sequence=1,
        trigger_type=PaymentMilestone.TRIGGER_BEFORE_START,
        amount_type=PaymentMilestone.AMOUNT_PERCENTAGE,
        percentage=Decimal("30.00"),
    )
    second = PaymentMilestone.objects.create(
        financial_plan=plan,
        title="M2 before delivery",
        sequence=2,
        trigger_type=PaymentMilestone.TRIGGER_BEFORE_DELIVERY,
        amount_type=PaymentMilestone.AMOUNT_PERCENTAGE,
        percentage=Decimal("50.00"),
    )
    third = PaymentMilestone.objects.create(
        financial_plan=plan,
        title="M3 completion",
        sequence=3,
        trigger_type=PaymentMilestone.TRIGGER_TASK_COMPLETION,
        amount_type=PaymentMilestone.AMOUNT_PERCENTAGE,
        percentage=Decimal("20.00"),
    )
    return first, second, third


def make_delivery(task, user, status=TaskDelivery.STATUS_DRAFT, **overrides):
    data = {
        "project": task.project,
        "task": task,
        "status": status,
        "delivery_reference": f"DEL-{TaskDelivery.objects.count() + 1}",
        "description": "Delivery package",
        "created_by": user,
    }
    data.update(overrides)
    return TaskDelivery.objects.create(**data)


def evidence_file(name="evidence-a.pdf", content=b"%PDF-1.4 evidence", content_type="application/pdf"):
    return SimpleUploadedFile(name, content, content_type=content_type)


def approve_progress(task, user, value):
    return TaskReportLog.objects.create(
        task=task,
        user=user,
        status="completed" if value >= 100 else "on-track",
        progress_percent=value,
        time_spent_hours=Decimal("1.00"),
        approval_status="final_approved",
    )


@pytest.mark.django_db
class TestCostRecognitionMilestoneAllocation:
    def test_cost_transaction_without_plan_has_no_allocation(self, financial_setup):
        tx = make_cost_transaction(financial_setup["task"], financial_setup["revision"], financial_setup["admin"], amount="600.00")

        result = allocate_cost_transaction_to_milestones(tx)

        assert result["allocated_amount"] == "0.00"
        assert result["unallocated_amount"] == "600.00"
        assert CostTransactionMilestoneAllocation.objects.count() == 0

    def test_sequential_allocation_stops_at_first_ineligible_and_refreshes_remainder(self, financial_setup):
        plan = make_plan(financial_setup["task"], financial_setup["admin"], amount="1000.00")
        first, second, third = add_cost_recognition_milestones(plan)
        approve_progress(financial_setup["task"], financial_setup["admin"], 40)
        tx = make_cost_transaction(financial_setup["task"], financial_setup["revision"], financial_setup["admin"], amount="600.00", financial_plan=plan)

        first_result = allocate_cost_transaction_to_milestones(tx)

        assert first_result["unallocated_amount"] == "300.00"
        assert first_result["stopped_at_milestone"] == second.id
        assert first.cost_allocations.get().allocated_amount == Decimal("300.00")
        assert not second.cost_allocations.exists()
        assert not third.cost_allocations.exists()

        approve_progress(financial_setup["task"], financial_setup["admin"], 50)
        second_result = refresh_plan_cost_allocations(plan)

        assert second_result["unallocated_recognized_cost_amount"] == "0.00"
        assert second.cost_allocations.get().allocated_amount == Decimal("300.00")
        assert not third.cost_allocations.exists()

    def test_multiple_transactions_use_deterministic_order_and_completion_unlocks_third_milestone(self, financial_setup):
        plan = make_plan(financial_setup["task"], financial_setup["admin"], amount="1000.00")
        first, second, third = add_cost_recognition_milestones(plan)
        approve_progress(financial_setup["task"], financial_setup["admin"], 50)
        tx1 = make_cost_transaction(financial_setup["task"], financial_setup["revision"], financial_setup["admin"], amount="600.00", financial_plan=plan)
        tx2 = make_cost_transaction(financial_setup["task"], financial_setup["revision"], financial_setup["admin"], amount="300.00", financial_plan=plan)
        allocate_cost_transaction_to_milestones(tx1)
        second_result = allocate_cost_transaction_to_milestones(tx2)

        assert first.cost_allocations.get(cost_transaction=tx1).allocated_amount == Decimal("300.00")
        assert second.cost_allocations.get(cost_transaction=tx1).allocated_amount == Decimal("300.00")
        assert second.cost_allocations.get(cost_transaction=tx2).allocated_amount == Decimal("100.00")
        assert second_result["unallocated_amount"] == "200.00"
        assert not third.cost_allocations.exists()

        approve_progress(financial_setup["task"], financial_setup["admin"], 100)
        refresh_plan_cost_allocations(plan)

        assert third.cost_allocations.get(cost_transaction=tx2).allocated_amount == Decimal("200.00")

    def test_pending_progress_is_ignored_for_cost_eligibility(self, financial_setup):
        plan = make_plan(financial_setup["task"], financial_setup["admin"], amount="1000.00")
        first, second, third = add_cost_recognition_milestones(plan)
        TaskReportLog.objects.create(
            task=financial_setup["task"],
            user=financial_setup["admin"],
            progress_percent=80,
            approval_status="pending",
        )

        result = evaluate_milestone_eligibility(second)

        assert result["eligible"] is False
        assert result["reason"] == "approved_progress_below_threshold"

    def test_manual_milestone_requires_explicit_approval(self, financial_setup):
        plan = make_plan(financial_setup["task"], financial_setup["admin"], amount="300.00")
        manual = PaymentMilestone.objects.create(
            financial_plan=plan,
            title="Manual",
            sequence=1,
            trigger_type=PaymentMilestone.TRIGGER_MANUAL,
            amount_type=PaymentMilestone.AMOUNT_FIXED,
            fixed_amount=Decimal("300.00"),
        )
        tx = make_cost_transaction(financial_setup["task"], financial_setup["revision"], financial_setup["admin"], amount="300.00", financial_plan=plan)

        result = allocate_cost_transaction_to_milestones(tx)

        assert result["unallocated_amount"] == "300.00"
        assert result["stop_reason"] == "manual_approval_required"
        assert not manual.cost_allocations.exists()

        manual.manual_approved = True
        manual.manual_approved_by = financial_setup["admin"]
        manual.manual_approved_at = timezone.now()
        manual.save()
        refresh_plan_cost_allocations(plan)

        assert manual.cost_allocations.get().allocated_amount == Decimal("300.00")

    def test_duplicate_refresh_is_idempotent(self, financial_setup):
        plan = make_plan(financial_setup["task"], financial_setup["admin"], amount="300.00")
        add_cost_recognition_milestones(plan)[0]
        tx = make_cost_transaction(financial_setup["task"], financial_setup["revision"], financial_setup["admin"], amount="300.00", financial_plan=plan)

        refresh_plan_cost_allocations(plan)
        refresh_plan_cost_allocations(plan)
        refresh_plan_cost_allocations(plan)

        assert CostTransactionMilestoneAllocation.objects.filter(cost_transaction=tx).count() == 1
        assert tx.milestone_allocations.aggregate(total=Sum("allocated_amount"))["total"] == Decimal("300.00")

    def test_amount_increase_allocates_only_remainder(self, financial_setup):
        plan = make_plan(financial_setup["task"], financial_setup["admin"], amount="600.00")
        PaymentMilestone.objects.create(financial_plan=plan, title="M1", sequence=1, trigger_type=PaymentMilestone.TRIGGER_BEFORE_START, amount_type=PaymentMilestone.AMOUNT_FIXED, fixed_amount=Decimal("600.00"))
        tx = make_cost_transaction(financial_setup["task"], financial_setup["revision"], financial_setup["admin"], amount="300.00", financial_plan=plan)
        allocate_cost_transaction_to_milestones(tx)
        tx.amount = Decimal("600.00")
        tx.save()
        allocate_cost_transaction_to_milestones(tx)

        assert tx.milestone_allocations.aggregate(total=Sum("allocated_amount"))["total"] == Decimal("600.00")
        assert tx.milestone_allocations.count() == 1

    def test_task_project_change_with_plan_is_rejected(self, financial_setup):
        plan = make_plan(financial_setup["task"], financial_setup["admin"], amount="300.00")
        tx = make_cost_transaction(financial_setup["task"], financial_setup["revision"], financial_setup["admin"], amount="300.00", financial_plan=plan)
        other_project = make_project(creator=financial_setup["admin"])

        tx.project = other_project

        with pytest.raises(ValidationError):
            tx.full_clean()

    def test_milestone_trigger_change_with_allocation_is_rejected(self, financial_setup):
        plan = make_plan(financial_setup["task"], financial_setup["admin"], amount="300.00")
        milestone = PaymentMilestone.objects.create(financial_plan=plan, title="M1", sequence=1, trigger_type=PaymentMilestone.TRIGGER_BEFORE_START, amount_type=PaymentMilestone.AMOUNT_FIXED, fixed_amount=Decimal("300.00"))
        tx = make_cost_transaction(financial_setup["task"], financial_setup["revision"], financial_setup["admin"], amount="300.00", financial_plan=plan)
        allocate_cost_transaction_to_milestones(tx)
        milestone.trigger_type = PaymentMilestone.TRIGGER_FIXED_DATE
        milestone.due_date = timezone.localdate()

        with pytest.raises(ValidationError):
            milestone.full_clean()

    def test_plan_summary_ignores_payment_transactions(self, financial_setup):
        plan = make_plan(financial_setup["task"], financial_setup["admin"], amount="300.00")
        milestone = PaymentMilestone.objects.create(financial_plan=plan, title="M1", sequence=1, trigger_type=PaymentMilestone.TRIGGER_BEFORE_START, amount_type=PaymentMilestone.AMOUNT_FIXED, fixed_amount=Decimal("300.00"))
        tx = make_cost_transaction(financial_setup["task"], financial_setup["revision"], financial_setup["admin"], amount="300.00", financial_plan=plan)
        allocate_cost_transaction_to_milestones(tx)
        register_transaction(milestone, PaymentTransaction.TYPE_PAYMENT, Decimal("200.00"), timezone.localdate(), financial_setup["admin"])

        status_payload = get_task_financial_status(financial_setup["task"])

        assert status_payload["recognized_cost_amount"] == "300.00"
        assert status_payload["allocated_recognized_cost_amount"] == "300.00"
        assert status_payload["unallocated_recognized_cost_amount"] == "0.00"
        assert status_payload["linked_cost_transaction_count"] == 1

    def test_existing_allocations_are_not_rolled_back_when_progress_drops(self, financial_setup):
        plan = make_plan(financial_setup["task"], financial_setup["admin"], amount="1000.00")
        first, second, third = add_cost_recognition_milestones(plan)
        approve_progress(financial_setup["task"], financial_setup["admin"], 50)
        tx = make_cost_transaction(financial_setup["task"], financial_setup["revision"], financial_setup["admin"], amount="600.00", financial_plan=plan)
        allocate_cost_transaction_to_milestones(tx)
        approve_progress(financial_setup["task"], financial_setup["admin"], 10)

        refresh_plan_cost_allocations(plan)

        assert second.cost_allocations.get(cost_transaction=tx).allocated_amount == Decimal("300.00")

    def test_amount_decrease_below_allocated_is_rejected(self, financial_setup):
        plan = make_plan(financial_setup["task"], financial_setup["admin"], amount="1000.00")
        first, second, third = add_cost_recognition_milestones(plan)
        tx = make_cost_transaction(financial_setup["task"], financial_setup["revision"], financial_setup["admin"], amount="300.00", financial_plan=plan)
        allocate_cost_transaction_to_milestones(tx)

        tx.amount = Decimal("299.00")

        with pytest.raises(ValidationError):
            tx.full_clean()

    def test_plan_change_with_existing_allocation_is_rejected(self, financial_setup):
        plan = make_plan(financial_setup["task"], financial_setup["admin"], amount="1000.00")
        other_plan = make_plan(financial_setup["task"], financial_setup["admin"], amount="1000.00")
        add_cost_recognition_milestones(plan)
        tx = make_cost_transaction(financial_setup["task"], financial_setup["revision"], financial_setup["admin"], amount="300.00", financial_plan=plan)
        allocate_cost_transaction_to_milestones(tx)

        tx.financial_plan = other_plan

        with pytest.raises(ValidationError):
            tx.full_clean()


def add_30_40_30(plan):
    first = PaymentMilestone.objects.create(
        financial_plan=plan,
        title="Advance",
        sequence=1,
        trigger_type=PaymentMilestone.TRIGGER_BEFORE_START,
        amount_type=PaymentMilestone.AMOUNT_PERCENTAGE,
        percentage=Decimal("30.00"),
        blocks_task_start=True,
    )
    second = PaymentMilestone.objects.create(
        financial_plan=plan,
        title="Progress",
        sequence=2,
        trigger_type=PaymentMilestone.TRIGGER_APPROVED_PROGRESS,
        amount_type=PaymentMilestone.AMOUNT_PERCENTAGE,
        percentage=Decimal("40.00"),
        progress_threshold=Decimal("50.00"),
        blocks_progress_after_threshold=True,
    )
    third = PaymentMilestone.objects.create(
        financial_plan=plan,
        title="Final",
        sequence=3,
        trigger_type=PaymentMilestone.TRIGGER_BEFORE_DELIVERY,
        amount_type=PaymentMilestone.AMOUNT_PERCENTAGE,
        percentage=Decimal("30.00"),
        blocks_task_delivery=True,
    )
    return first, second, third


@pytest.mark.django_db
class TestTaskFinancialPlanService:
    def test_create_payable_and_receivable_plans(self, financial_setup):
        payable = make_plan(financial_setup["task"], financial_setup["admin"])
        other_task, _ = make_task(financial_setup["project"], financial_setup["revision"], title="Task B")
        receivable = make_plan(other_task, financial_setup["admin"], direction=TaskFinancialPlan.DIRECTION_RECEIVABLE)

        assert payable.direction == "payable"
        assert receivable.direction == "receivable"


    def test_payable_and_receivable_plans_do_not_require_cost_transaction(self, financial_setup):
        payable = make_plan(financial_setup["task"], financial_setup["admin"])
        other_task, _ = make_task(financial_setup["project"], financial_setup["revision"], title="Task B")
        receivable = make_plan(other_task, financial_setup["admin"], direction=TaskFinancialPlan.DIRECTION_RECEIVABLE)

        assert payable.cost_transactions.count() == 0
        assert receivable.cost_transactions.count() == 0

    def test_cost_transaction_link_allocates_actual_cost_across_30_40_30_milestones(self, financial_setup):
        plan = make_plan(financial_setup["task"], financial_setup["admin"], amount="100.00")
        add_30_40_30(plan)
        activate_plan(plan)

        make_cost_transaction(financial_setup["task"], financial_setup["revision"], financial_setup["admin"], amount="75.00", financial_plan=plan)

        status_payload = get_task_financial_status(financial_setup["task"])
        assert status_payload["total_incurred"] == "75.00"
        assert status_payload["cost_outstanding"] == "25.00"
        assert [row["incurred_amount"] for row in status_payload["milestones"]] == ["30.00", "40.00", "5.00"]
        assert [row["cost_outstanding"] for row in status_payload["milestones"]] == ["0.00", "0.00", "25.00"]
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

    def test_plan_level_payment_allocates_across_open_milestones(self, financial_setup):
        plan = make_plan(financial_setup["task"], financial_setup["admin"])
        advance, middle, final = add_30_30_40(plan)
        activate_plan(plan)

        transactions = allocate_plan_payment(
            plan,
            Decimal("100000000.00"),
            timezone.localdate(),
            financial_setup["admin"],
            reference_number="PAY-100",
        )

        assert [tx.milestone_id for tx in transactions] == [advance.id, middle.id, final.id]
        assert [tx.amount for tx in transactions] == [Decimal("30000000.00"), Decimal("30000000.00"), Decimal("40000000.00")]
        status_payload = get_task_financial_status(financial_setup["task"])
        assert status_payload["outstanding"] == "0.00"
        assert status_payload["can_start"] is True
        assert status_payload["can_deliver"] is True

    def test_plan_level_payment_rejects_overpayment(self, financial_setup):
        plan = make_plan(financial_setup["task"], financial_setup["admin"])
        add_30_30_40(plan)
        activate_plan(plan)

        with pytest.raises(Exception):
            allocate_plan_payment(plan, Decimal("100000001.00"), timezone.localdate(), financial_setup["admin"])

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


def response_rows(response):
    data = response.data
    return data.get("results", data) if isinstance(data, dict) else data


@pytest.mark.django_db
class TestTaskFinancialPlanCostTransactionApi:
    def test_api_create_payable_without_cost_transaction(self, financial_setup):
        response = api(financial_setup["admin"]).post(reverse("task-financial-plan-list"), {
            "task": str(financial_setup["task"].id),
            "direction": TaskFinancialPlan.DIRECTION_PAYABLE,
            "contract_amount": "100000000.00",
            "currency": "IRR",
        }, format="json")

        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert "cost_transaction" not in response.data

    def test_api_links_cost_transaction_to_valid_payable_plan(self, financial_setup):
        plan = make_plan(financial_setup["task"], financial_setup["admin"], amount="100.00")

        response = api(financial_setup["admin"]).post(reverse("cost-transaction-list"), {
            "project": financial_setup["project"].id,
            "task": str(financial_setup["task"].id),
            "revision": financial_setup["revision"].id,
            "transaction_type": "COST",
            "transaction_date": timezone.localdate().isoformat(),
            "amount": "75.00",
            "financial_plan": plan.id,
        }, format="json")

        assert response.status_code == status.HTTP_201_CREATED, response.data
        tx = CostTransaction.objects.get(pk=response.data["id"])
        assert tx.financial_plan == plan

    def test_api_rejects_inaccessible_financial_plan_injection(self, financial_setup):
        plan = make_plan(financial_setup["task"], financial_setup["admin"], amount="100.00")

        response = api(make_member()).post(reverse("cost-transaction-list"), {
            "project": financial_setup["project"].id,
            "task": str(financial_setup["task"].id),
            "revision": financial_setup["revision"].id,
            "transaction_type": "COST",
            "transaction_date": timezone.localdate().isoformat(),
            "amount": "75.00",
            "financial_plan": plan.id,
        }, format="json")

        assert response.status_code in {status.HTTP_400_BAD_REQUEST, status.HTTP_403_FORBIDDEN}

    def test_cost_transaction_rejects_receivable_plan_link(self, financial_setup):
        plan = make_plan(
            financial_setup["task"],
            financial_setup["admin"],
            direction=TaskFinancialPlan.DIRECTION_RECEIVABLE,
        )

        serializer = TaskFinancialPlanSerializer(data=plan_payload(financial_setup["task"]))
        assert serializer.is_valid(), serializer.errors

        tx = CostTransaction(
            project=financial_setup["project"],
            revision=financial_setup["revision"],
            task=financial_setup["task"],
            transaction_type="COST",
            transaction_date=timezone.localdate(),
            amount=Decimal("100.00"),
            financial_plan=plan,
            created_by=financial_setup["admin"],
        )
        with pytest.raises(ValidationError):
            tx.full_clean()

    def test_plan_with_payment_cannot_change_financial_fields(self, financial_setup):
        plan = make_plan(financial_setup["task"], financial_setup["admin"], amount="100.00")
        milestone = PaymentMilestone.objects.create(
            financial_plan=plan,
            title="Advance",
            sequence=1,
            trigger_type=PaymentMilestone.TRIGGER_MANUAL,
            amount_type=PaymentMilestone.AMOUNT_FIXED,
            fixed_amount=Decimal("100.00"),
        )
        register_transaction(milestone, PaymentTransaction.TYPE_PAYMENT, Decimal("100.00"), timezone.localdate(), financial_setup["admin"])
        response = api(financial_setup["admin"]).patch(reverse("task-financial-plan-detail", kwargs={"pk": plan.id}), {
            "contract_amount": "125.00",
        }, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "contract_amount" in response.data

    def test_receivable_without_cost_transaction_still_works(self, financial_setup):
        response = api(financial_setup["admin"]).post(reverse("task-financial-plan-list"), {
            "task": str(financial_setup["task"].id),
            "direction": TaskFinancialPlan.DIRECTION_RECEIVABLE,
            "contract_amount": "100000000.00",
            "currency": "IRR",
        }, format="json")

        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert "cost_transaction" not in response.data


@pytest.mark.django_db
class TestPaymentTransactionFilters:
    def test_payment_transaction_filters_and_project_scope(self, financial_setup):
        plan = make_plan(financial_setup["task"], financial_setup["admin"], amount="100.00")
        milestone = PaymentMilestone.objects.create(
            financial_plan=plan,
            title="Advance",
            sequence=1,
            trigger_type=PaymentMilestone.TRIGGER_MANUAL,
            amount_type=PaymentMilestone.AMOUNT_FIXED,
            fixed_amount=Decimal("100.00"),
        )
        tx = register_transaction(milestone, PaymentTransaction.TYPE_PAYMENT, Decimal("100.00"), timezone.localdate(), financial_setup["admin"])
        client = api(financial_setup["admin"])

        for params in [
            {"milestone_id": milestone.id},
            {"financial_plan_id": plan.id},
            {"task_id": str(financial_setup["task"].id)},
            {"project_id": financial_setup["project"].id},
        ]:
            response = client.get(reverse("payment-transaction-list"), params)
            assert response.status_code == status.HTTP_200_OK, response.data
            assert [row["id"] for row in response_rows(response)] == [tx.id]

        response = api(make_member()).get(reverse("payment-transaction-list"), {"project_id": financial_setup["project"].id})
        assert response.status_code == status.HTTP_200_OK, response.data
        assert response_rows(response) == []


@pytest.mark.django_db
class TestTaskDeliveryWorkflow:
    def test_create_draft_and_reject_project_task_mismatch(self, financial_setup):
        other_project = make_project(creator=financial_setup["admin"])
        response = api(financial_setup["admin"]).post(reverse("task-delivery-list"), {
            "project": financial_setup["project"].id,
            "task": str(financial_setup["task"].id),
            "delivery_reference": "PKG-001",
            "description": "Initial package",
        }, format="json")

        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert response.data["status"] == TaskDelivery.STATUS_DRAFT
        assert response.data["created_by"] == financial_setup["admin"].id

        response = api(financial_setup["admin"]).post(reverse("task-delivery-list"), {
            "project": other_project.id,
            "task": str(financial_setup["task"].id),
            "delivery_reference": "BAD",
        }, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "task" in response.data

    def test_inaccessible_task_injection_rejected(self, financial_setup):
        response = api(make_member()).post(reverse("task-delivery-list"), {
            "project": financial_setup["project"].id,
            "task": str(financial_setup["task"].id),
            "delivery_reference": "NOPE",
        }, format="json")

        assert response.status_code in {status.HTTP_400_BAD_REQUEST, status.HTTP_403_FORBIDDEN}

    def test_submit_rejected_resubmit_and_actor_fields(self, financial_setup):
        delivery = make_delivery(financial_setup["task"], financial_setup["admin"])

        response = api(financial_setup["admin"]).post(reverse("task-delivery-submit", kwargs={"pk": delivery.id}))
        delivery.refresh_from_db()

        assert response.status_code == status.HTTP_200_OK, response.data
        assert delivery.status == TaskDelivery.STATUS_SUBMITTED
        assert delivery.submitted_by == financial_setup["admin"]
        assert delivery.submitted_at is not None

        delivery = reject_task_delivery(delivery, user=financial_setup["reviewer"], reason="Missing documents")
        assert delivery.status == TaskDelivery.STATUS_REJECTED
        assert delivery.rejection_reason == "Missing documents"

        delivery = submit_task_delivery(delivery, user=financial_setup["admin"])
        assert delivery.status == TaskDelivery.STATUS_SUBMITTED
        assert delivery.rejection_reason == ""
        assert delivery.rejected_by is None

    def test_approve_reject_cancel_invalid_transitions_and_patch_status(self, financial_setup):
        delivery = make_delivery(financial_setup["task"], financial_setup["admin"])
        response = api(financial_setup["admin"]).patch(reverse("task-delivery-detail", kwargs={"pk": delivery.id}), {
            "status": TaskDelivery.STATUS_APPROVED,
            "description": "edited",
        }, format="json")
        delivery.refresh_from_db()

        assert response.status_code == status.HTTP_200_OK, response.data
        assert delivery.status == TaskDelivery.STATUS_DRAFT
        assert delivery.description == "edited"

        with pytest.raises(ValidationError):
            approve_task_delivery(delivery, user=financial_setup["reviewer"])

        cancelled = cancel_task_delivery(delivery, user=financial_setup["admin"])
        assert cancelled.status == TaskDelivery.STATUS_CANCELLED
        with pytest.raises(ValidationError):
            submit_task_delivery(cancelled, user=financial_setup["admin"])

        submitted = submit_task_delivery(make_delivery(financial_setup["task"], financial_setup["admin"]), user=financial_setup["admin"])
        with pytest.raises(ValidationError):
            reject_task_delivery(submitted, user=financial_setup["reviewer"], reason="")
        rejected = reject_task_delivery(submitted, user=financial_setup["reviewer"], reason="Incomplete")
        assert rejected.status == TaskDelivery.STATUS_REJECTED
        cancelled_rejected = cancel_task_delivery(rejected, user=financial_setup["admin"])
        assert cancelled_rejected.status == TaskDelivery.STATUS_CANCELLED

        approved = submit_task_delivery(make_delivery(financial_setup["task"], financial_setup["admin"]), user=financial_setup["admin"])
        approved, _ = approve_task_delivery(approved, user=financial_setup["reviewer"])
        with pytest.raises(ValidationError):
            cancel_task_delivery(approved, user=financial_setup["admin"])
        with pytest.raises(ValidationError):
            reject_task_delivery(approved, user=financial_setup["reviewer"], reason="late")

    def test_latest_approved_delivery_and_as_of_cutoff(self, financial_setup):
        first = submit_task_delivery(make_delivery(financial_setup["task"], financial_setup["admin"], delivery_reference="A"), user=financial_setup["admin"])
        first, _ = approve_task_delivery(first, user=financial_setup["reviewer"])
        cutoff = first.approved_at + timedelta(seconds=1)
        second = submit_task_delivery(make_delivery(financial_setup["task"], financial_setup["admin"], delivery_reference="B"), user=financial_setup["admin"])
        second, _ = approve_task_delivery(second, user=financial_setup["reviewer"])

        assert get_effective_task_delivery(financial_setup["task"]).id == second.id
        assert get_effective_task_delivery(financial_setup["task"], as_of_date=cutoff).id == first.id
        assert get_effective_task_delivery(financial_setup["task"], as_of_date=first.approved_at - timedelta(seconds=1)) is None

    @pytest.mark.parametrize("delivery_status,reason", [
        (None, "delivery_not_submitted"),
        (TaskDelivery.STATUS_DRAFT, "delivery_not_submitted"),
        (TaskDelivery.STATUS_SUBMITTED, "delivery_pending_approval"),
        (TaskDelivery.STATUS_REJECTED, "delivery_rejected"),
        (TaskDelivery.STATUS_CANCELLED, "delivery_cancelled"),
    ])
    def test_before_delivery_eligibility_states(self, financial_setup, delivery_status, reason):
        plan = make_plan(financial_setup["task"], financial_setup["admin"], amount="1000.00")
        _, milestone, _ = add_before_delivery_milestones(plan)
        if delivery_status:
            make_delivery(financial_setup["task"], financial_setup["admin"], status=delivery_status)

        result = evaluate_milestone_eligibility(milestone)

        assert result["eligible"] is False
        assert result["reason"] == reason

    def test_approved_delivery_unlocks_allocation_without_payment_or_cost_side_effects(self, financial_setup):
        plan = make_plan(financial_setup["task"], financial_setup["admin"], amount="1000.00")
        first, second, third = add_before_delivery_milestones(plan)
        tx = make_cost_transaction(financial_setup["task"], financial_setup["revision"], financial_setup["admin"], amount="800.00", financial_plan=plan)

        before = allocate_cost_transaction_to_milestones(tx)
        ac_before = CostTransaction.objects.filter(task=financial_setup["task"]).aggregate(total=Sum("amount"))["total"]
        paid_before = PaymentTransaction.objects.count()

        assert before["unallocated_amount"] == "500.00"
        assert before["stop_reason"] == "delivery_not_submitted"
        assert first.cost_allocations.get().allocated_amount == Decimal("300.00")
        assert not second.cost_allocations.exists()
        assert not third.cost_allocations.exists()

        draft = make_delivery(financial_setup["task"], financial_setup["admin"])
        assert evaluate_milestone_eligibility(second)["reason"] == "delivery_not_submitted"
        submitted = submit_task_delivery(draft, user=financial_setup["admin"])
        assert evaluate_milestone_eligibility(second)["reason"] == "delivery_pending_approval"
        rejected = reject_task_delivery(submitted, user=financial_setup["reviewer"], reason="Missing")
        assert evaluate_milestone_eligibility(second)["reason"] == "delivery_rejected"
        submitted = submit_task_delivery(rejected, user=financial_setup["admin"])
        approved, summaries = approve_task_delivery(submitted, user=financial_setup["reviewer"])
        after_status = get_task_financial_status(financial_setup["task"])
        ac_after = CostTransaction.objects.filter(task=financial_setup["task"]).aggregate(total=Sum("amount"))["total"]

        assert approved.status == TaskDelivery.STATUS_APPROVED
        assert summaries
        assert evaluate_milestone_eligibility(second)["reason"] == "delivery_approved"
        assert second.cost_allocations.get().allocated_amount == Decimal("500.00")
        assert not third.cost_allocations.exists()
        assert after_status["unallocated_recognized_cost_amount"] == "0.00"
        assert CostTransactionMilestoneAllocation.objects.filter(cost_transaction=tx).count() == 2
        assert PaymentTransaction.objects.count() == paid_before
        assert CostTransaction.objects.filter(task=financial_setup["task"]).count() == 1
        assert tx.amount == Decimal("800.00")
        assert ac_before == Decimal("800.00")
        assert ac_after == Decimal("800.00")

        approved_again, _ = approve_task_delivery(approved, user=financial_setup["reviewer"])
        assert approved_again.id == approved.id
        assert CostTransactionMilestoneAllocation.objects.filter(cost_transaction=tx).count() == 2

    def test_unauthorized_approval_rejected(self, financial_setup):
        delivery = submit_task_delivery(make_delivery(financial_setup["task"], financial_setup["admin"]), user=financial_setup["admin"])
        viewer = make_member()
        assign_role(financial_setup["task"], financial_setup["revision"], viewer, role="executor")

        response = api(viewer).post(reverse("task-delivery-approve", kwargs={"pk": delivery.id}))

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_delivery_capability_matrix_for_editor_and_reviewer(self, financial_setup):
        editor = make_member()
        project = make_project(creator=editor, scope="intra_unit")
        revision = make_revision(project, creator=editor, approved=True)
        task, _ = make_task(project, revision, title="Capability Task")
        reviewer = make_member()
        viewer = make_member()
        assign_role(task, revision, reviewer, role="reviewer")
        assign_role(task, revision, viewer, role="executor")

        draft = make_delivery(task, editor, status=TaskDelivery.STATUS_DRAFT)
        submitted = submit_task_delivery(make_delivery(task, editor), user=editor)
        rejected = make_delivery(task, editor, status=TaskDelivery.STATUS_REJECTED)
        approved = make_delivery(task, editor, status=TaskDelivery.STATUS_APPROVED)
        cancelled = make_delivery(task, editor, status=TaskDelivery.STATUS_CANCELLED)

        assert get_task_delivery_capabilities(draft, editor) == {
            "can_edit": True,
            "can_submit": True,
            "can_approve": False,
            "can_reject": False,
            "can_cancel": True,
        }
        assert get_task_delivery_capabilities(submitted, editor)["can_approve"] is False
        assert get_task_delivery_capabilities(submitted, editor)["can_reject"] is False
        assert get_task_delivery_capabilities(submitted, reviewer)["can_approve"] is True
        assert get_task_delivery_capabilities(submitted, reviewer)["can_reject"] is True
        assert get_task_delivery_capabilities(rejected, editor)["can_submit"] is True
        assert all(value is False for value in get_task_delivery_capabilities(approved, editor).values())
        assert all(value is False for value in get_task_delivery_capabilities(cancelled, reviewer).values())
        assert get_task_delivery_capabilities(submitted, viewer) == {
            "can_edit": False,
            "can_submit": False,
            "can_approve": False,
            "can_reject": False,
            "can_cancel": False,
        }

    def test_delivery_capabilities_in_list_detail_and_action_response(self, financial_setup):
        delivery = make_delivery(financial_setup["task"], financial_setup["admin"])
        submitted = submit_task_delivery(delivery, user=financial_setup["admin"])

        list_response = api(financial_setup["reviewer"]).get(reverse("task-delivery-list"), {"task_id": str(financial_setup["task"].id)})
        detail_response = api(financial_setup["reviewer"]).get(reverse("task-delivery-detail", kwargs={"pk": submitted.id}))

        assert list_response.status_code == status.HTTP_200_OK, list_response.data
        assert detail_response.status_code == status.HTTP_200_OK, detail_response.data
        list_row = next(row for row in response_rows(list_response) if row["id"] == str(submitted.id))
        assert list_row["capabilities"] == detail_response.data["capabilities"]
        assert detail_response.data["capabilities"]["can_approve"] is True
        assert detail_response.data["capabilities"]["can_reject"] is True

        approve_response = api(financial_setup["reviewer"]).post(reverse("task-delivery-approve", kwargs={"pk": submitted.id}))

        assert approve_response.status_code == status.HTTP_200_OK, approve_response.data
        assert approve_response.data["delivery"]["status"] == TaskDelivery.STATUS_APPROVED
        assert all(value is False for value in approve_response.data["delivery"]["capabilities"].values())

    def test_capability_false_direct_requests_rejected(self, financial_setup):
        editor = make_member()
        project = make_project(creator=editor, scope="intra_unit")
        revision = make_revision(project, creator=editor, approved=True)
        task, _ = make_task(project, revision, title="Direct Reject Task")
        viewer = make_member()
        assign_role(task, revision, viewer, role="executor")
        submitted = submit_task_delivery(make_delivery(task, editor), user=editor)

        assert get_task_delivery_capabilities(submitted, viewer)["can_submit"] is False
        assert get_task_delivery_capabilities(submitted, viewer)["can_approve"] is False
        assert get_task_delivery_capabilities(submitted, viewer)["can_reject"] is False

        viewer_api = api(viewer)
        assert viewer_api.post(reverse("task-delivery-submit", kwargs={"pk": submitted.id})).status_code == status.HTTP_403_FORBIDDEN
        assert viewer_api.post(reverse("task-delivery-approve", kwargs={"pk": submitted.id})).status_code == status.HTTP_403_FORBIDDEN
        assert viewer_api.post(reverse("task-delivery-reject", kwargs={"pk": submitted.id}), {"reason": "no"}, format="json").status_code == status.HTTP_403_FORBIDDEN

    def test_financial_status_payload_includes_delivery_create_capability(self, financial_setup):
        viewer = make_member()
        assign_role(financial_setup["task"], financial_setup["revision"], viewer, role="executor")

        editor_response = api(financial_setup["admin"]).get(reverse("task-financial-plan-status-summary"), {"task_id": str(financial_setup["task"].id)})
        viewer_response = api(viewer).get(reverse("task-financial-plan-status-summary"), {"task_id": str(financial_setup["task"].id)})

        assert editor_response.status_code == status.HTTP_200_OK, editor_response.data
        assert viewer_response.status_code == status.HTTP_200_OK, viewer_response.data
        assert editor_response.data["delivery_capabilities"]["can_create"] is True
        assert viewer_response.data["delivery_capabilities"]["can_create"] is False


@pytest.mark.django_db
class TestTaskDeliveryEvidenceAttachments:
    def test_upload_draft_metadata_count_detail_download_and_delete(self, financial_setup, settings, tmp_path):
        settings.MEDIA_ROOT = tmp_path
        delivery = make_delivery(financial_setup["task"], financial_setup["admin"])
        client = api(financial_setup["admin"])

        response = client.post(reverse("task-delivery-attachments", kwargs={"pk": delivery.id}), {
            "file": evidence_file("../unsafe evidence.pdf"),
            "description": "Signed handover",
        }, format="multipart")

        assert response.status_code == status.HTTP_201_CREATED, response.data
        attachment = TaskDeliveryAttachment.objects.get()
        assert attachment.original_filename == "unsafe_evidence.pdf"
        assert attachment.content_type == "application/pdf"
        assert attachment.size_bytes > 0
        assert attachment.uploaded_by == financial_setup["admin"]
        assert response.data["delivery"]["attachment_count"] == 1
        assert response.data["delivery"]["has_attachments"] is True
        assert response.data["delivery"]["attachment_capabilities"] == {"can_upload": True, "can_delete": True}

        list_response = client.get(reverse("task-delivery-list"), {"task_id": str(financial_setup["task"].id)})
        detail_response = client.get(reverse("task-delivery-detail", kwargs={"pk": delivery.id}))
        list_row = response_rows(list_response)[0]
        assert list_row["attachment_count"] == 1
        assert list_row["attachments"] == []
        assert detail_response.data["attachments"][0]["original_filename"] == "unsafe_evidence.pdf"
        assert detail_response.data["attachments"][0]["download_url"].endswith("/download/")

        download_response = client.get(reverse("task-delivery-attachment-download", kwargs={"pk": attachment.id}))
        assert download_response.status_code == status.HTTP_200_OK
        assert "unsafe_evidence.pdf" in download_response["Content-Disposition"]

        stored_path = tmp_path / attachment.file.name
        assert stored_path.exists()
        delete_response = client.delete(reverse("task-delivery-attachment-detail", kwargs={"pk": attachment.id}))
        assert delete_response.status_code == status.HTTP_204_NO_CONTENT
        assert not stored_path.exists()
        assert TaskDeliveryAttachment.objects.count() == 0

    @pytest.mark.parametrize("delivery_status", [
        TaskDelivery.STATUS_SUBMITTED,
        TaskDelivery.STATUS_APPROVED,
        TaskDelivery.STATUS_CANCELLED,
    ])
    def test_upload_to_locked_statuses_rejected(self, financial_setup, settings, tmp_path, delivery_status):
        settings.MEDIA_ROOT = tmp_path
        delivery = make_delivery(financial_setup["task"], financial_setup["admin"], status=delivery_status)

        response = api(financial_setup["admin"]).post(reverse("task-delivery-attachments", kwargs={"pk": delivery.id}), {
            "file": evidence_file(),
        }, format="multipart")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert TaskDeliveryAttachment.objects.count() == 0

    @pytest.mark.parametrize("delivery_status", [
        TaskDelivery.STATUS_SUBMITTED,
        TaskDelivery.STATUS_APPROVED,
        TaskDelivery.STATUS_CANCELLED,
    ])
    def test_delete_from_locked_statuses_rejected(self, financial_setup, settings, tmp_path, delivery_status):
        settings.MEDIA_ROOT = tmp_path
        draft = make_delivery(financial_setup["task"], financial_setup["admin"])
        upload = api(financial_setup["admin"]).post(reverse("task-delivery-attachments", kwargs={"pk": draft.id}), {
            "file": evidence_file(),
        }, format="multipart")
        attachment_id = upload.data["attachment"]["id"]
        draft.status = delivery_status
        draft.save(update_fields=["status", "updated_at"])

        response = api(financial_setup["admin"]).delete(reverse("task-delivery-attachment-detail", kwargs={"pk": attachment_id}))

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert TaskDeliveryAttachment.objects.filter(pk=attachment_id).exists()

    def test_rejected_delivery_allows_evidence_replacement_and_lifecycle_preserves_files(self, financial_setup, settings, tmp_path):
        settings.MEDIA_ROOT = tmp_path
        client = api(financial_setup["admin"])
        draft = make_delivery(financial_setup["task"], financial_setup["admin"])
        upload_a = client.post(reverse("task-delivery-attachments", kwargs={"pk": draft.id}), {
            "file": evidence_file("evidence-a.pdf"),
        }, format="multipart")
        evidence_a_id = upload_a.data["attachment"]["id"]

        submitted = submit_task_delivery(draft, user=financial_setup["admin"])
        assert TaskDeliveryAttachment.objects.filter(pk=evidence_a_id).exists()
        rejected = reject_task_delivery(submitted, user=financial_setup["reviewer"], reason="Replace file")

        delete_a = client.delete(reverse("task-delivery-attachment-detail", kwargs={"pk": evidence_a_id}))
        upload_b = client.post(reverse("task-delivery-attachments", kwargs={"pk": rejected.id}), {
            "file": evidence_file("evidence-b.pdf"),
        }, format="multipart")
        resubmitted = submit_task_delivery(rejected, user=financial_setup["admin"])
        approved, _ = approve_task_delivery(resubmitted, user=financial_setup["reviewer"])

        assert delete_a.status_code == status.HTTP_204_NO_CONTENT
        assert upload_b.status_code == status.HTTP_201_CREATED, upload_b.data
        assert TaskDeliveryAttachment.objects.count() == 1
        assert TaskDeliveryAttachment.objects.get().original_filename == "evidence-b.pdf"
        assert approved.attachments.count() == 1
        assert get_task_delivery_attachment_capabilities(approved, financial_setup["admin"]) == {"can_upload": False, "can_delete": False}

    def test_rejected_upload_and_delete_capabilities(self, financial_setup, settings, tmp_path):
        settings.MEDIA_ROOT = tmp_path
        delivery = make_delivery(financial_setup["task"], financial_setup["admin"], status=TaskDelivery.STATUS_REJECTED)

        response = api(financial_setup["admin"]).post(reverse("task-delivery-attachments", kwargs={"pk": delivery.id}), {
            "file": evidence_file("rejected.pdf"),
        }, format="multipart")

        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert response.data["delivery"]["attachment_capabilities"] == {"can_upload": True, "can_delete": True}
        assert response.data["attachment"]["capabilities"]["can_delete"] is True

    def test_inaccessible_upload_download_and_delete_rejected(self, financial_setup, settings, tmp_path):
        settings.MEDIA_ROOT = tmp_path
        delivery = make_delivery(financial_setup["task"], financial_setup["admin"])
        upload = api(financial_setup["admin"]).post(reverse("task-delivery-attachments", kwargs={"pk": delivery.id}), {
            "file": evidence_file(),
        }, format="multipart")
        attachment_id = upload.data["attachment"]["id"]
        outsider = api(make_member())

        upload_response = outsider.post(reverse("task-delivery-attachments", kwargs={"pk": delivery.id}), {
            "file": evidence_file("outsider.pdf"),
        }, format="multipart")
        download_response = outsider.get(reverse("task-delivery-attachment-download", kwargs={"pk": attachment_id}))
        delete_response = outsider.delete(reverse("task-delivery-attachment-detail", kwargs={"pk": attachment_id}))

        assert upload_response.status_code == status.HTTP_404_NOT_FOUND
        assert download_response.status_code == status.HTTP_404_NOT_FOUND
        assert delete_response.status_code == status.HTTP_404_NOT_FOUND

    def test_unsafe_and_oversized_files_rejected(self, financial_setup, settings, tmp_path):
        settings.MEDIA_ROOT = tmp_path
        delivery = make_delivery(financial_setup["task"], financial_setup["admin"])
        client = api(financial_setup["admin"])

        script_response = client.post(reverse("task-delivery-attachments", kwargs={"pk": delivery.id}), {
            "file": evidence_file("run.exe", b"bad", "application/x-msdownload"),
        }, format="multipart")
        oversized_response = client.post(reverse("task-delivery-attachments", kwargs={"pk": delivery.id}), {
            "file": evidence_file("large.pdf", b"x" * (10 * 1024 * 1024 + 1)),
        }, format="multipart")

        assert script_response.status_code == status.HTTP_400_BAD_REQUEST
        assert oversized_response.status_code == status.HTTP_400_BAD_REQUEST
        assert TaskDeliveryAttachment.objects.count() == 0

    def test_attachment_upload_and_delete_have_no_financial_side_effects(self, financial_setup, settings, tmp_path):
        settings.MEDIA_ROOT = tmp_path
        plan = make_plan(financial_setup["task"], financial_setup["admin"], amount="1000.00")
        first, _, _ = add_cost_recognition_milestones(plan)
        tx = make_cost_transaction(financial_setup["task"], financial_setup["revision"], financial_setup["admin"], amount="300.00", financial_plan=plan)
        allocate_cost_transaction_to_milestones(tx)
        register_transaction(first, PaymentTransaction.TYPE_PAYMENT, Decimal("100.00"), timezone.localdate(), financial_setup["admin"])
        delivery = make_delivery(financial_setup["task"], financial_setup["admin"])
        before = {
            "allocation_count": CostTransactionMilestoneAllocation.objects.count(),
            "cost_count": CostTransaction.objects.count(),
            "cost_amount": CostTransaction.objects.aggregate(total=Sum("amount"))["total"],
            "payment_count": PaymentTransaction.objects.count(),
            "paid": get_task_financial_status(financial_setup["task"])["total_paid"],
        }

        upload = api(financial_setup["admin"]).post(reverse("task-delivery-attachments", kwargs={"pk": delivery.id}), {
            "file": evidence_file(),
        }, format="multipart")
        attachment_id = upload.data["attachment"]["id"]
        delete_response = api(financial_setup["admin"]).delete(reverse("task-delivery-attachment-detail", kwargs={"pk": attachment_id}))
        after = {
            "allocation_count": CostTransactionMilestoneAllocation.objects.count(),
            "cost_count": CostTransaction.objects.count(),
            "cost_amount": CostTransaction.objects.aggregate(total=Sum("amount"))["total"],
            "payment_count": PaymentTransaction.objects.count(),
            "paid": get_task_financial_status(financial_setup["task"])["total_paid"],
        }

        assert upload.status_code == status.HTTP_201_CREATED, upload.data
        assert delete_response.status_code == status.HTTP_204_NO_CONTENT
        assert after == before

    def test_delivery_list_attachment_count_avoids_per_row_queries(self, financial_setup, settings, tmp_path, django_assert_num_queries):
        settings.MEDIA_ROOT = tmp_path
        deliveries = [make_delivery(financial_setup["task"], financial_setup["admin"], delivery_reference=f"DEL-{index}") for index in range(3)]
        client = api(financial_setup["admin"])
        for delivery in deliveries:
            client.post(reverse("task-delivery-attachments", kwargs={"pk": delivery.id}), {
                "file": evidence_file(f"{delivery.delivery_reference}.pdf"),
            }, format="multipart")

        with django_assert_num_queries(5):
            response = client.get(reverse("task-delivery-list"), {"task_id": str(financial_setup["task"].id)})

        assert response.status_code == status.HTTP_200_OK, response.data
        assert all(row["attachment_count"] == 1 for row in response_rows(response)[:3])
