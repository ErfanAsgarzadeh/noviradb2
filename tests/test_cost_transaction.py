from decimal import Decimal

import pytest
from django.db.models import Sum
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from ktcPlanning.models import (
    Assignment,
    BudgetAllocation,
    BudgetConsumption,
    CostTransaction,
    ExpenseType,
    FundingSource,
    Resource,
    ResourceRate,
    TaskFinancialPlan,
    UnitOfMeasure,
)
from ktcPlanning.serializers import CostTransactionSerializer
from tests.factories import make_company_admin, make_member, make_project, make_revision, make_task


@pytest.fixture
def cost_setup():
    admin = make_company_admin()
    project = make_project(creator=admin, scope="intra_unit")
    revision = make_revision(project, creator=admin, approved=True)
    task, task_version = make_task(project, revision, title="Cost Task")
    return {"admin": admin, "project": project, "revision": revision, "task": task, "task_version": task_version}


def api(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def make_resource(resource_type=Resource.COST, name=None):
    index = Resource.objects.count() + 1
    return Resource.objects.create(
        code=f"RES-COST-{index}",
        name=name or f"{resource_type} Resource {index}",
        resource_type=resource_type,
    )


def make_assignment(task, revision, resource):
    return Assignment.objects.create(
        revision=revision,
        task=task,
        resource=resource,
        units_percent=Decimal("100.00"),
    )


def cost_payload(setup, amount="100000000.00", **overrides):
    payload = {
        "project": setup["project"].id,
        "revision": setup["revision"].id,
        "task": str(setup["task"].id),
        "transaction_type": "COST",
        "transaction_date": str(timezone.localdate()),
        "amount": amount,
        "description": "Contracted service cost",
    }
    payload.update(overrides)
    return payload


def make_budget(setup, amount="200000000.00"):
    source = FundingSource.objects.create(
        title="Approved source",
        source_type="CONTRACT",
        received_date=timezone.localdate(),
        total_amount=Decimal(amount),
        status="APPROVED",
        created_by=setup["admin"],
    )
    return BudgetAllocation.objects.create(
        funding_source=source,
        project=setup["project"],
        revision=setup["revision"],
        scope_type="PROJECT",
        cost_type="COST",
        allocated_amount=Decimal(amount),
        status="APPROVED",
        created_by=setup["admin"],
    )


def make_expense_type():
    unit = UnitOfMeasure.objects.create(code=f"EA{UnitOfMeasure.objects.count() + 1}", name="Each")
    return ExpenseType.objects.create(name=f"Expense {ExpenseType.objects.count() + 1}", unit=unit)


@pytest.mark.django_db
class TestCostTransactionDirectAmount:
    def test_cost_create_uses_direct_amount_without_quantity_or_unit_rate(self, cost_setup):
        serializer = CostTransactionSerializer(data=cost_payload(cost_setup, quantity="9.00"))

        assert serializer.is_valid(), serializer.errors
        tx = serializer.save(created_by=cost_setup["admin"])

        assert tx.amount == Decimal("100000000.00")
        assert tx.quantity is None
        assert tx.unit_rate is None
        assert tx.resource_rate is None

    @pytest.mark.parametrize("amount", [None, "0.00", "-1.00"])
    def test_cost_requires_positive_direct_amount(self, cost_setup, amount):
        payload = cost_payload(cost_setup)
        if amount is None:
            payload.pop("amount")
        else:
            payload["amount"] = amount

        serializer = CostTransactionSerializer(data=payload)

        assert serializer.is_valid() is False
        assert "amount" in serializer.errors

    def test_cost_rejects_resource_rate_and_unit_rate_inputs(self, cost_setup):
        resource = make_resource(Resource.COST)
        rate = ResourceRate.objects.create(
            resource=resource,
            effective_from=timezone.localdate(),
            regular_rate=Decimal("25.00"),
        )

        serializer = CostTransactionSerializer(data=cost_payload(cost_setup, resource_rate=rate.id))
        assert serializer.is_valid() is False
        assert "resource_rate" in serializer.errors

        serializer = CostTransactionSerializer(data=cost_payload(cost_setup, unit_rate="25.00"))
        assert serializer.is_valid() is False
        assert "unit_rate" in serializer.errors

    def test_cost_accepts_cost_resource_and_rejects_non_cost_resource(self, cost_setup):
        cost_resource = make_resource(Resource.COST)
        serializer = CostTransactionSerializer(data=cost_payload(cost_setup, resource=cost_resource.id))
        assert serializer.is_valid(), serializer.errors

        labor_resource = make_resource(Resource.LABOR)
        serializer = CostTransactionSerializer(data=cost_payload(cost_setup, resource=labor_resource.id))
        assert serializer.is_valid() is False
        assert "resource" in serializer.errors

    def test_cost_accepts_no_resource(self, cost_setup):
        serializer = CostTransactionSerializer(data=cost_payload(cost_setup))

        assert serializer.is_valid(), serializer.errors

    def test_cost_rejects_non_cost_assignment_resource(self, cost_setup):
        resource = make_resource(Resource.LABOR)
        assignment = make_assignment(cost_setup["task"], cost_setup["revision"], resource)

        serializer = CostTransactionSerializer(data=cost_payload(cost_setup, assignment=assignment.id))

        assert serializer.is_valid() is False
        assert "resource" in serializer.errors

    def test_rate_based_labor_calculates_amount_and_rejects_client_amount(self, cost_setup):
        resource = make_resource(Resource.LABOR)
        assignment = make_assignment(cost_setup["task"], cost_setup["revision"], resource)
        rate = ResourceRate.objects.create(
            resource=resource,
            effective_from=timezone.localdate(),
            regular_rate=Decimal("12.50"),
        )
        payload = {
            "project": cost_setup["project"].id,
            "revision": cost_setup["revision"].id,
            "task": str(cost_setup["task"].id),
            "assignment": assignment.id,
            "resource_rate": rate.id,
            "transaction_type": "LABOR",
            "transaction_date": str(timezone.localdate()),
            "quantity": "3.00",
        }

        serializer = CostTransactionSerializer(data={**payload, "amount": "999.00"})
        assert serializer.is_valid() is False
        assert "amount" in serializer.errors

        serializer = CostTransactionSerializer(data=payload)
        assert serializer.is_valid(), serializer.errors
        tx = serializer.save(created_by=cost_setup["admin"])
        assert tx.amount == Decimal("37.50")

    def test_material_equipment_and_expense_regressions_still_calculate_amount(self, cost_setup):
        for transaction_type, resource_type in [("MATERIAL", Resource.MATERIAL), ("EQUIPMENT", Resource.EQUIPMENT)]:
            resource = make_resource(resource_type)
            assignment = make_assignment(cost_setup["task"], cost_setup["revision"], resource)
            rate = ResourceRate.objects.create(
                resource=resource,
                effective_from=timezone.localdate(),
                regular_rate=Decimal("20.00"),
            )
            tx = CostTransaction.objects.create(
                project=cost_setup["project"],
                revision=cost_setup["revision"],
                task=cost_setup["task"],
                assignment=assignment,
                resource_rate=rate,
                transaction_type=transaction_type,
                transaction_date=timezone.localdate(),
                quantity=Decimal("2.00"),
                created_by=cost_setup["admin"],
            )
            assert tx.amount == Decimal("40.00")

        tx = CostTransaction.objects.create(
            project=cost_setup["project"],
            revision=cost_setup["revision"],
            task=cost_setup["task"],
            transaction_type="EXPENSE",
            transaction_date=timezone.localdate(),
            quantity=Decimal("3.00"),
            expense_rate=Decimal("10.00"),
            expense_type=make_expense_type(),
            created_by=cost_setup["admin"],
        )
        assert tx.amount == Decimal("30.00")

    def test_budget_consumes_direct_cost_amount_and_rejects_overrun(self, cost_setup):
        make_budget(cost_setup, amount="150.00")

        response = api(cost_setup["admin"]).post(
            reverse("cost-transaction-list"),
            cost_payload(cost_setup, amount="125.00"),
            format="json",
        )

        assert response.status_code == 201, response.data
        tx = CostTransaction.objects.get(pk=response.data["id"])
        assert BudgetConsumption.objects.get(transaction=tx).amount == Decimal("125.00")

        response = api(cost_setup["admin"]).post(
            reverse("cost-transaction-list"),
            cost_payload(cost_setup, amount="26.00"),
            format="json",
        )
        assert response.status_code == 400
        assert "budget" in response.data

    def test_update_adjusts_budget_difference_when_allowed(self, cost_setup):
        make_budget(cost_setup, amount="200.00")
        client = api(cost_setup["admin"])
        response = client.post(reverse("cost-transaction-list"), cost_payload(cost_setup, amount="75.00"), format="json")
        assert response.status_code == 201, response.data

        response = client.patch(reverse("cost-transaction-detail", kwargs={"pk": response.data["id"]}), {"amount": "120.00"}, format="json")

        assert response.status_code == 200, response.data
        tx = CostTransaction.objects.get(pk=response.data["id"])
        assert tx.amount == Decimal("120.00")
        assert tx.budget_consumptions.aggregate(total=Sum("amount"))["total"] == Decimal("120.00")

    def make_linked_cost_transaction(self, cost_setup):
        plan = TaskFinancialPlan.objects.create(
            task=cost_setup["task"],
            direction=TaskFinancialPlan.DIRECTION_PAYABLE,
            contract_amount=Decimal("100.00"),
            currency="IRR",
            created_by=cost_setup["admin"],
        )
        return CostTransaction.objects.create(
            project=cost_setup["project"],
            revision=cost_setup["revision"],
            task=cost_setup["task"],
            transaction_type="COST",
            transaction_date=timezone.localdate(),
            amount=Decimal("100.00"),
            financial_plan=plan,
            description="Original description",
            created_by=cost_setup["admin"],
        )

    @pytest.mark.parametrize(
        ("field", "value", "expected_error"),
        [
            ("task", None, "task"),
            ("project", None, "project"),
            ("transaction_type", "LABOR", "transaction_type"),
        ],
    )
    def test_linked_financial_plan_blocks_invalid_relationship_updates(self, cost_setup, field, value, expected_error):
        tx = self.make_linked_cost_transaction(cost_setup)
        if field in {"task", "project"}:
            other_project = make_project(creator=cost_setup["admin"], scope="intra_unit")
            other_revision = make_revision(other_project, cost_setup["admin"], approved=True)
            other_task, _ = make_task(other_project, other_revision, title="Other Cost Task")
            value = str(other_task.id) if field == "task" else other_project.id

        serializer = CostTransactionSerializer(instance=tx, data={field: value}, partial=True)

        assert serializer.is_valid() is False
        assert expected_error in serializer.errors
        if field == "transaction_type":
            assert "quantity" not in serializer.errors
            assert "resource_rate" not in serializer.errors

    def test_linked_financial_plan_allows_amount_update(self, cost_setup):
        tx = self.make_linked_cost_transaction(cost_setup)
        serializer = CostTransactionSerializer(instance=tx, data={"amount": "120.00"}, partial=True)

        assert serializer.is_valid(), serializer.errors
        updated = serializer.save()
        assert updated.amount == Decimal("120.00")

    def test_transaction_type_cannot_change_after_creation_without_financial_plan(self, cost_setup):
        tx = CostTransaction.objects.create(
            project=cost_setup["project"],
            revision=cost_setup["revision"],
            task=cost_setup["task"],
            transaction_type="COST",
            transaction_date=timezone.localdate(),
            amount=Decimal("100.00"),
            created_by=cost_setup["admin"],
        )

        serializer = CostTransactionSerializer(instance=tx, data={"transaction_type": "LABOR"}, partial=True)

        assert serializer.is_valid() is False
        assert "transaction_type" in serializer.errors
        assert "quantity" not in serializer.errors
        assert "resource_rate" not in serializer.errors

    def test_linked_financial_plan_allows_same_protected_values_and_description_update(self, cost_setup):
        tx = self.make_linked_cost_transaction(cost_setup)
        serializer = CostTransactionSerializer(
            instance=tx,
            data={
                "amount": "100.00",
                "task": str(cost_setup["task"].id),
                "project": cost_setup["project"].id,
                "transaction_type": "COST",
                "description": "Updated description",
            },
            partial=True,
        )

        assert serializer.is_valid(), serializer.errors
        updated = serializer.save()
        assert updated.amount == Decimal("100.00")
        assert updated.task == cost_setup["task"]
        assert updated.project == cost_setup["project"]
        assert updated.transaction_type == "COST"
        assert updated.description == "Updated description"

    def test_legacy_cost_quantity_unit_rate_row_still_reads_amount(self, cost_setup):
        tx = CostTransaction.objects.create(
            project=cost_setup["project"],
            revision=cost_setup["revision"],
            task=cost_setup["task"],
            transaction_type="COST",
            transaction_date=timezone.localdate(),
            amount=Decimal("90.00"),
            created_by=cost_setup["admin"],
        )
        CostTransaction.objects.filter(pk=tx.pk).update(quantity=Decimal("1.00"), unit_rate=Decimal("90.00"))
        tx.refresh_from_db()

        data = CostTransactionSerializer(tx).data

        assert data["amount"] == "90.00"
        assert tx.quantity == Decimal("1.00")
        assert tx.unit_rate == Decimal("90.00")

    def test_cost_transaction_validates_payable_plan_task_and_project(self, cost_setup):
        tx = CostTransaction.objects.create(
            project=cost_setup["project"],
            revision=cost_setup["revision"],
            task=cost_setup["task"],
            transaction_type="COST",
            transaction_date=timezone.localdate(),
            amount=Decimal("100.00"),
            created_by=cost_setup["admin"],
        )
        plan = TaskFinancialPlan.objects.create(
            task=cost_setup["task"],
            direction=TaskFinancialPlan.DIRECTION_PAYABLE,
            contract_amount=Decimal("101.00"),
            currency="IRR",
            created_by=cost_setup["admin"],
        )

        serializer = CostTransactionSerializer(instance=tx, data={"financial_plan": plan.id}, partial=True)
        assert serializer.is_valid(), serializer.errors

        receivable = TaskFinancialPlan.objects.create(
            task=cost_setup["task"],
            direction=TaskFinancialPlan.DIRECTION_RECEIVABLE,
            contract_amount=Decimal("100.00"),
            currency="IRR",
            created_by=cost_setup["admin"],
        )
        serializer = CostTransactionSerializer(instance=tx, data={"financial_plan": receivable.id}, partial=True)
        assert serializer.is_valid() is False
        assert "financial_plan" in serializer.errors


def response_rows(response):
    data = response.data
    return data.get("results", data) if isinstance(data, dict) else data


@pytest.mark.django_db
class TestCostTransactionSelectorApi:
    def test_selector_filters_task_type_available_and_financial_plan_state(self, cost_setup):
        client = api(cost_setup["admin"])
        available_tx = CostTransaction.objects.create(
            project=cost_setup["project"],
            revision=cost_setup["revision"],
            task=cost_setup["task"],
            transaction_type="COST",
            transaction_date=timezone.localdate(),
            amount=Decimal("100.00"),
            created_by=cost_setup["admin"],
        )
        planned_tx = CostTransaction.objects.create(
            project=cost_setup["project"],
            revision=cost_setup["revision"],
            task=cost_setup["task"],
            transaction_type="COST",
            transaction_date=timezone.localdate(),
            amount=Decimal("200.00"),
            created_by=cost_setup["admin"],
        )
        plan = TaskFinancialPlan.objects.create(
            task=cost_setup["task"],
            direction=TaskFinancialPlan.DIRECTION_PAYABLE,
            contract_amount=Decimal("200.00"),
            currency="IRR",
            created_by=cost_setup["admin"],
        )
        planned_tx.financial_plan = plan
        planned_tx.save(update_fields=["financial_plan"])

        response = client.get(reverse("cost-transaction-list"), {
            "task_id": str(cost_setup["task"].id),
            "transaction_type": "COST",
            "available_for_financial_plan": "true",
        })

        assert response.status_code == 200, response.data
        ids = {row["id"] for row in response_rows(response)}
        assert available_tx.id in ids
        assert planned_tx.id not in ids

    def test_selector_effective_resource_direct_assignment_and_null(self, cost_setup):
        direct_resource = make_resource(Resource.COST, name="Direct Cost")
        assignment_resource = make_resource(Resource.COST, name="Assigned Cost")
        assignment = make_assignment(cost_setup["task"], cost_setup["revision"], assignment_resource)
        direct_tx = CostTransaction.objects.create(
            project=cost_setup["project"], revision=cost_setup["revision"], task=cost_setup["task"],
            resource=direct_resource, transaction_type="COST", transaction_date=timezone.localdate(),
            amount=Decimal("10.00"), created_by=cost_setup["admin"],
        )
        assignment_tx = CostTransaction.objects.create(
            project=cost_setup["project"], revision=cost_setup["revision"], task=cost_setup["task"],
            assignment=assignment, transaction_type="COST", transaction_date=timezone.localdate(),
            amount=Decimal("20.00"), created_by=cost_setup["admin"],
        )
        null_tx = CostTransaction.objects.create(
            project=cost_setup["project"], revision=cost_setup["revision"], task=cost_setup["task"],
            transaction_type="COST", transaction_date=timezone.localdate(), amount=Decimal("30.00"),
            created_by=cost_setup["admin"],
        )

        response = api(cost_setup["admin"]).get(reverse("cost-transaction-list"), {"transaction_type": "COST"})

        assert response.status_code == 200, response.data
        by_id = {row["id"]: row for row in response_rows(response)}
        assert by_id[direct_tx.id]["effective_resource_id"] == direct_resource.id
        assert by_id[direct_tx.id]["effective_resource_name"] == "Direct Cost"
        assert by_id[assignment_tx.id]["effective_resource_id"] == assignment_resource.id
        assert by_id[assignment_tx.id]["effective_resource_type"] == Resource.COST
        assert by_id[null_tx.id]["effective_resource_id"] is None
        assert by_id[null_tx.id]["effective_resource_name"] is None
        assert by_id[null_tx.id]["has_financial_plan"] is False

    def test_selector_scopes_to_accessible_projects(self, cost_setup):
        CostTransaction.objects.create(
            project=cost_setup["project"], revision=cost_setup["revision"], task=cost_setup["task"],
            transaction_type="COST", transaction_date=timezone.localdate(), amount=Decimal("10.00"),
            created_by=cost_setup["admin"],
        )
        response = api(make_member()).get(reverse("cost-transaction-list"), {"project_id": cost_setup["project"].id})

        assert response.status_code == 200, response.data
        assert response_rows(response) == []

    def test_selector_invalid_available_boolean_returns_400(self, cost_setup):
        response = api(cost_setup["admin"]).get(reverse("cost-transaction-list"), {"available_for_financial_plan": "maybe"})

        assert response.status_code == 400
        assert "available_for_financial_plan" in response.data
