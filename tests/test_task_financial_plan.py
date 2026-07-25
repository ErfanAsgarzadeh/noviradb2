from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.core.exceptions import ValidationError
from django.db.models.deletion import ProtectedError
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from ktcPlanning.financial_services import (
    activate_plan,
    allocate_plan_payment,
    get_task_financial_status,
    register_transaction,
    validate_progress_transition,
    validate_task_delivery,
    validate_task_start,
)
from ktcPlanning.models import Assignment, CostTransaction, PaymentMilestone, PaymentTransaction, Resource, ResourceRate, TaskFinancialPlan, TaskReportLog
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


def make_cost_transaction(task, revision, user, amount="100000000.00", resource_type=Resource.COST, transaction_type="COST", project=None):
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


def make_plan(task, user, amount="100000000.00", direction=TaskFinancialPlan.DIRECTION_PAYABLE, cost_transaction="auto"):
    if direction == TaskFinancialPlan.DIRECTION_PAYABLE and cost_transaction == "auto":
        cost_transaction = make_cost_transaction(task, task.project.current_execution_revision, user, amount=amount)
    elif cost_transaction == "auto":
        cost_transaction = None
    return TaskFinancialPlan.objects.create(
        task=task,
        cost_transaction=cost_transaction,
        direction=direction,
        contract_amount=Decimal(amount),
        currency="IRR",
        created_by=user,
    )


def plan_payload(task, amount="100000000.00", direction=TaskFinancialPlan.DIRECTION_PAYABLE, cost_transaction=None):
    payload = {
        "task": str(task.id),
        "direction": direction,
        "contract_amount": amount,
        "currency": "IRR",
    }
    if cost_transaction is not None:
        payload["cost_transaction"] = cost_transaction.id
    return payload


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


@pytest.mark.django_db
class TestTaskFinancialPlanService:
    def test_create_payable_and_receivable_plans(self, financial_setup):
        payable = make_plan(financial_setup["task"], financial_setup["admin"])
        other_task, _ = make_task(financial_setup["project"], financial_setup["revision"], title="Task B")
        receivable = make_plan(other_task, financial_setup["admin"], direction=TaskFinancialPlan.DIRECTION_RECEIVABLE)

        assert payable.direction == "payable"
        assert receivable.direction == "receivable"


    def test_payable_plan_requires_valid_cost_transaction(self, financial_setup):
        tx = make_cost_transaction(financial_setup["task"], financial_setup["revision"], financial_setup["admin"])
        plan = make_plan(financial_setup["task"], financial_setup["admin"], cost_transaction=tx)

        assert plan.cost_transaction == tx
        assert plan.contract_amount == tx.amount

    def test_payable_plan_without_cost_transaction_is_rejected(self, financial_setup):
        assert_plan_payload_invalid(
            plan_payload(financial_setup["task"], cost_transaction=None),
            "cost_transaction",
        )

    def test_receivable_plan_without_cost_transaction_is_allowed(self, financial_setup):
        plan = make_plan(
            financial_setup["task"],
            financial_setup["admin"],
            direction=TaskFinancialPlan.DIRECTION_RECEIVABLE,
            cost_transaction=None,
        )

        assert plan.cost_transaction is None

    def test_receivable_plan_with_cost_transaction_is_rejected(self, financial_setup):
        tx = make_cost_transaction(financial_setup["task"], financial_setup["revision"], financial_setup["admin"])

        assert_plan_payload_invalid(
            plan_payload(
                financial_setup["task"],
                direction=TaskFinancialPlan.DIRECTION_RECEIVABLE,
                cost_transaction=tx,
            ),
            "cost_transaction",
        )

    def test_non_cost_transaction_type_is_rejected(self, financial_setup):
        tx = make_cost_transaction(
            financial_setup["task"],
            financial_setup["revision"],
            financial_setup["admin"],
            resource_type=Resource.LABOR,
            transaction_type="LABOR",
        )

        assert_plan_payload_invalid(plan_payload(financial_setup["task"], cost_transaction=tx), "cost_transaction")

    def test_cost_transaction_for_other_task_is_rejected(self, financial_setup):
        other_task, _ = make_task(financial_setup["project"], financial_setup["revision"], title="Other Task")
        tx = make_cost_transaction(other_task, financial_setup["revision"], financial_setup["admin"])

        assert_plan_payload_invalid(plan_payload(financial_setup["task"], cost_transaction=tx), "cost_transaction")

    def test_cost_transaction_for_other_project_is_rejected(self, financial_setup):
        other_project = make_project(creator=financial_setup["admin"], scope="intra_unit")
        other_revision = make_revision(other_project, creator=financial_setup["admin"], approved=True)
        tx = make_cost_transaction(financial_setup["task"], financial_setup["revision"], financial_setup["admin"])
        CostTransaction.objects.filter(pk=tx.pk).update(project=other_project, revision=other_revision)
        tx.refresh_from_db()

        assert_plan_payload_invalid(plan_payload(financial_setup["task"], cost_transaction=tx), "cost_transaction")

    def test_assignment_for_other_task_is_rejected(self, financial_setup):
        tx = make_cost_transaction(financial_setup["task"], financial_setup["revision"], financial_setup["admin"])
        other_task, _ = make_task(financial_setup["project"], financial_setup["revision"], title="Other Task")
        tx.assignment.task = other_task
        tx.assignment.save()

        assert_plan_payload_invalid(plan_payload(financial_setup["task"], cost_transaction=tx), "cost_transaction")

    def test_non_cost_resource_is_rejected_for_cost_transaction(self, financial_setup):
        tx = make_cost_transaction(financial_setup["task"], financial_setup["revision"], financial_setup["admin"])
        tx.resource.resource_type = Resource.LABOR
        tx.resource.save()

        assert_plan_payload_invalid(plan_payload(financial_setup["task"], cost_transaction=tx), "cost_transaction")

    def test_contract_amount_must_equal_cost_transaction_amount(self, financial_setup):
        tx = make_cost_transaction(financial_setup["task"], financial_setup["revision"], financial_setup["admin"])

        assert_plan_payload_invalid(
            plan_payload(financial_setup["task"], amount="99999999.99", cost_transaction=tx),
            "contract_amount",
        )
        assert_plan_payload_invalid(
            plan_payload(financial_setup["task"], amount="100000000.01", cost_transaction=tx),
            "contract_amount",
        )
        serializer = TaskFinancialPlanSerializer(data=plan_payload(financial_setup["task"], cost_transaction=tx))
        assert serializer.is_valid(), serializer.errors

    def test_cost_transaction_cannot_be_reused_by_active_plan(self, financial_setup):
        tx = make_cost_transaction(financial_setup["task"], financial_setup["revision"], financial_setup["admin"])
        first = make_plan(financial_setup["task"], financial_setup["admin"], cost_transaction=tx)
        second = TaskFinancialPlan.objects.create(
            task=financial_setup["task"],
            cost_transaction=tx,
            direction=TaskFinancialPlan.DIRECTION_PAYABLE,
            contract_amount=tx.amount,
            currency="IRR",
            created_by=financial_setup["admin"],
        )
        add_30_30_40(first)
        add_30_30_40(second)
        activate_plan(first)

        with pytest.raises(Exception):
            activate_plan(second)

    def test_legacy_null_cost_transaction_plan_can_be_read_serialized_and_not_activated(self, financial_setup):
        TaskFinancialPlan.objects.bulk_create([
            TaskFinancialPlan(
                task=financial_setup["task"],
                direction=TaskFinancialPlan.DIRECTION_PAYABLE,
                contract_amount=Decimal("100000000.00"),
                currency="IRR",
                created_by=financial_setup["admin"],
            )
        ])

        plan = TaskFinancialPlan.objects.get(cost_transaction__isnull=True)
        data = TaskFinancialPlanSerializer(plan).data

        assert plan.direction == TaskFinancialPlan.DIRECTION_PAYABLE
        assert data["cost_transaction_amount"] is None
        assert data["cost_transaction_type"] is None
        assert data["cost_transaction_date"] is None
        assert data["cost_transaction_description"] is None
        assert data["cost_transaction_resource_name"] is None
        with pytest.raises(Exception):
            activate_plan(plan)

    def test_linked_cost_transaction_delete_is_protected(self, financial_setup):
        tx = make_cost_transaction(financial_setup["task"], financial_setup["revision"], financial_setup["admin"])
        make_plan(financial_setup["task"], financial_setup["admin"], cost_transaction=tx)

        with pytest.raises(ProtectedError):
            tx.delete()

    def test_plan_with_payment_transaction_cannot_change_cost_transaction(self, financial_setup):
        plan = make_plan(financial_setup["task"], financial_setup["admin"])
        advance, _, _ = add_30_30_40(plan)
        activate_plan(plan)
        register_transaction(advance, PaymentTransaction.TYPE_PAYMENT, Decimal("30000000.00"), timezone.localdate(), financial_setup["admin"])
        other_tx = make_cost_transaction(financial_setup["task"], financial_setup["revision"], financial_setup["admin"], amount="100000000.00")

        serializer = TaskFinancialPlanSerializer(plan, data={"cost_transaction": other_tx.id}, partial=True)

        assert serializer.is_valid() is False
        assert "cost_transaction" in serializer.errors

    def test_serializer_returns_cost_transaction_read_only_fields(self, financial_setup):
        tx = make_cost_transaction(financial_setup["task"], financial_setup["revision"], financial_setup["admin"])
        plan = make_plan(financial_setup["task"], financial_setup["admin"], cost_transaction=tx)

        data = TaskFinancialPlanSerializer(plan).data

        assert data["cost_transaction"] == tx.id
        assert data["cost_transaction_amount"] == "100000000.00"
        assert data["cost_transaction_type"] == "COST"
        assert data["cost_transaction_date"] == timezone.localdate().isoformat()
        assert data["cost_transaction_description"] == ""
        assert data["cost_transaction_resource_name"] == tx.resource.name
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
        assert CostTransaction.objects.count() == 1


@pytest.mark.django_db
class TestTaskFinancialPlanApi:
    def test_api_create_activate_and_record_payment(self, financial_setup):
        client = api(financial_setup["admin"])
        cost_transaction = make_cost_transaction(financial_setup["task"], financial_setup["revision"], financial_setup["admin"])
        plan_resp = client.post(reverse("task-financial-plan-list"), {
            "task": str(financial_setup["task"].id),
            "cost_transaction": cost_transaction.id,
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
    def test_api_create_payable_with_valid_cost_transaction(self, financial_setup):
        tx = make_cost_transaction(financial_setup["task"], financial_setup["revision"], financial_setup["admin"])

        response = api(financial_setup["admin"]).post(reverse("task-financial-plan-list"), {
            "task": str(financial_setup["task"].id),
            "cost_transaction": tx.id,
            "direction": TaskFinancialPlan.DIRECTION_PAYABLE,
            "contract_amount": "100000000.00",
            "currency": "IRR",
        }, format="json")

        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert response.data["cost_transaction"] == tx.id

    def test_api_rejects_inaccessible_cost_transaction_injection(self, financial_setup):
        tx = make_cost_transaction(financial_setup["task"], financial_setup["revision"], financial_setup["admin"])

        response = api(make_member()).post(reverse("task-financial-plan-list"), {
            "task": str(financial_setup["task"].id),
            "cost_transaction": tx.id,
            "direction": TaskFinancialPlan.DIRECTION_PAYABLE,
            "contract_amount": "100000000.00",
            "currency": "IRR",
        }, format="json")

        assert response.status_code in {status.HTTP_400_BAD_REQUEST, status.HTTP_403_FORBIDDEN}

    def test_draft_without_payment_can_change_cost_transaction_with_matching_amount(self, financial_setup):
        first_tx = make_cost_transaction(financial_setup["task"], financial_setup["revision"], financial_setup["admin"], amount="100.00")
        second_tx = make_cost_transaction(financial_setup["task"], financial_setup["revision"], financial_setup["admin"], amount="125.00")
        plan = make_plan(financial_setup["task"], financial_setup["admin"], amount="100.00", cost_transaction=first_tx)

        response = api(financial_setup["admin"]).patch(reverse("task-financial-plan-detail", kwargs={"pk": plan.id}), {
            "cost_transaction": second_tx.id,
            "contract_amount": "125.00",
        }, format="json")

        assert response.status_code == status.HTTP_200_OK, response.data
        plan.refresh_from_db()
        assert plan.cost_transaction == second_tx
        assert plan.contract_amount == Decimal("125.00")

    def test_draft_cost_transaction_change_requires_matching_contract_amount(self, financial_setup):
        first_tx = make_cost_transaction(financial_setup["task"], financial_setup["revision"], financial_setup["admin"], amount="100.00")
        second_tx = make_cost_transaction(financial_setup["task"], financial_setup["revision"], financial_setup["admin"], amount="125.00")
        plan = make_plan(financial_setup["task"], financial_setup["admin"], amount="100.00", cost_transaction=first_tx)

        response = api(financial_setup["admin"]).patch(reverse("task-financial-plan-detail", kwargs={"pk": plan.id}), {
            "cost_transaction": second_tx.id,
        }, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "contract_amount" in response.data

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
        next_tx = make_cost_transaction(financial_setup["task"], financial_setup["revision"], financial_setup["admin"], amount="100.00")

        response = api(financial_setup["admin"]).patch(reverse("task-financial-plan-detail", kwargs={"pk": plan.id}), {
            "cost_transaction": next_tx.id,
        }, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "cost_transaction" in response.data

    def test_receivable_without_cost_transaction_still_works(self, financial_setup):
        response = api(financial_setup["admin"]).post(reverse("task-financial-plan-list"), {
            "task": str(financial_setup["task"].id),
            "direction": TaskFinancialPlan.DIRECTION_RECEIVABLE,
            "contract_amount": "100000000.00",
            "currency": "IRR",
        }, format="json")

        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert response.data["cost_transaction"] is None


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