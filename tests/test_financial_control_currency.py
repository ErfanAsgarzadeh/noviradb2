from datetime import timedelta
from decimal import Decimal
from time import perf_counter

from django.test import TestCase
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from ktcPlanning.models import (
    Assignment,
    BudgetAllocation,
    CostTransaction,
    Currency,
    ExchangeRate,
    FundingSource,
    PaymentMilestone,
    PaymentTransaction,
    Resource,
    Task,
    TaskFinancialPlan,
    VarianceReport,
)
from tests.factories import assign_role, make_company_admin, make_member, make_project, make_revision, make_task


def api(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


class FinancialControlCurrencyTests(TestCase):
    def setUp(self):
        self.admin = make_company_admin()
        self.viewer = make_member()
        self.project = make_project(creator=self.admin, scope="intra_unit", name="Currency Control")
        self.revision = make_revision(self.project, creator=self.admin, approved=True, is_baseline=True)
        self.today = timezone.localdate()
        self.url = reverse("financial-control")
        self.convert_url = reverse("financial-control-convert")
        for code in ["IRR", "USD", "EUR"]:
            Currency.objects.get_or_create(code=code, defaults={"name": code, "symbol": code})
        self.resource = Resource.objects.create(code="CUR-COST", name="Currency Cost", resource_type=Resource.COST)
        self.irr_task, self.irr_version = make_task(self.project, self.revision, title="IRR Cost Task")
        self.usd_task, self.usd_version = make_task(self.project, self.revision, title="USD Cost Task")
        assign_role(self.irr_task, self.revision, self.viewer, role="executor")
        assign_role(self.usd_task, self.revision, self.viewer, role="executor")
        self.irr_assignment = Assignment.objects.create(revision=self.revision, task=self.irr_task, resource=self.resource, units_percent=Decimal("100.00"))
        self.usd_assignment = Assignment.objects.create(revision=self.revision, task=self.usd_task, resource=self.resource, units_percent=Decimal("100.00"))
        self.irr_source = FundingSource.objects.create(title="IRR Source", source_type="CONTRACT", received_date=self.today, total_amount=Decimal("1000.00"), currency="IRR", status="APPROVED", created_by=self.admin)
        self.usd_source = FundingSource.objects.create(title="USD Source", source_type="CONTRACT", received_date=self.today, total_amount=Decimal("100.00"), currency="USD", status="APPROVED", created_by=self.admin)
        BudgetAllocation.objects.create(funding_source=self.irr_source, project=self.project, revision=self.revision, scope_type="TASK", wbs_node=self.irr_version.wbs_node, task=self.irr_task, cost_type="COST", allocated_amount=Decimal("1000.00"), status="APPROVED", created_by=self.admin)
        BudgetAllocation.objects.create(funding_source=self.usd_source, project=self.project, revision=self.revision, scope_type="TASK", wbs_node=self.usd_version.wbs_node, task=self.usd_task, cost_type="COST", allocated_amount=Decimal("100.00"), status="APPROVED", created_by=self.admin)
        self.irr_plan = TaskFinancialPlan.objects.create(task=self.irr_task, direction=TaskFinancialPlan.DIRECTION_PAYABLE, contract_amount=Decimal("1000.00"), currency="IRR", status=TaskFinancialPlan.STATUS_ACTIVE, created_by=self.admin)
        self.usd_plan = TaskFinancialPlan.objects.create(task=self.usd_task, direction=TaskFinancialPlan.DIRECTION_PAYABLE, contract_amount=Decimal("100.00"), currency="USD", status=TaskFinancialPlan.STATUS_ACTIVE, created_by=self.admin)
        self.irr_milestone = PaymentMilestone.objects.create(financial_plan=self.irr_plan, title="IRR Gate", sequence=1, trigger_type=PaymentMilestone.TRIGGER_BEFORE_START, amount_type=PaymentMilestone.AMOUNT_FIXED, fixed_amount=Decimal("1000.00"))
        self.usd_milestone = PaymentMilestone.objects.create(financial_plan=self.usd_plan, title="USD Gate", sequence=1, trigger_type=PaymentMilestone.TRIGGER_BEFORE_START, amount_type=PaymentMilestone.AMOUNT_FIXED, fixed_amount=Decimal("100.00"))
        CostTransaction.objects.create(project=self.project, revision=self.revision, task=self.irr_task, assignment=self.irr_assignment, financial_plan=self.irr_plan, transaction_type="COST", amount=Decimal("100.00"), currency="IRR", transaction_date=self.today, created_by=self.admin)
        CostTransaction.objects.create(project=self.project, revision=self.revision, task=self.usd_task, assignment=self.usd_assignment, financial_plan=self.usd_plan, transaction_type="COST", amount=Decimal("10.00"), currency="USD", transaction_date=self.today, created_by=self.admin)
        PaymentTransaction.objects.create(milestone=self.irr_milestone, transaction_type=PaymentTransaction.TYPE_PAYMENT, amount=Decimal("20.00"), currency="USD", transaction_date=self.today, created_by=self.admin)
        PaymentTransaction.objects.create(milestone=self.usd_milestone, transaction_type=PaymentTransaction.TYPE_PAYMENT, amount=Decimal("5.00"), transaction_date=self.today, created_by=self.admin)
        VarianceReport.objects.create(task=self.irr_task, revision=self.revision, report_date=self.today, dimension=VarianceReport.DIMENSION_COST, currency="IRR", budget_at_completion=Decimal("1000.00"), planned_value=Decimal("100.00"), earned_value=Decimal("100.00"), actual_cost=Decimal("100.00"))
        VarianceReport.objects.create(task=self.usd_task, revision=self.revision, report_date=self.today, dimension=VarianceReport.DIMENSION_COST, currency="USD", budget_at_completion=Decimal("100.00"), planned_value=Decimal("10.00"), earned_value=Decimal("10.00"), actual_cost=Decimal("10.00"))

    def payload(self, **params):
        response = api(self.admin).get(self.url, {"project_id": self.project.id, **params})
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return response.data

    def converted(self, **body):
        response = api(self.admin).post(self.convert_url, {"filters": {"project_id": self.project.id}, **body}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return response.data

    def summary_bucket(self, payload, code):
        return next(item for item in payload["summary_by_currency"] if item["currency"]["code"] == code)

    def test_native_mode_is_default_and_returns_currency_buckets(self):
        payload = self.payload()
        self.assertEqual(payload["currency_mode"], "native")
        self.assertEqual({item["currency"]["code"] for item in payload["summary_by_currency"]}, {"IRR", "USD"})
        self.assertTrue(all(row["amounts_by_currency"] for row in payload["tasks"]))

    def test_payment_currency_mismatch_keeps_plan_outstanding_in_plan_currency(self):
        payload = self.payload()
        irr = self.summary_bucket(payload, "IRR")
        usd = self.summary_bucket(payload, "USD")
        self.assertEqual(irr["contract_value"], "1000.00")
        self.assertEqual(irr["paid_amount"], "0.00")
        self.assertEqual(irr["outstanding_payment"], "1000.00")
        self.assertEqual(usd["paid_amount"], "25.00")
        self.assertIn("payment_currency_differs_from_plan", {item["code"] for item in payload["warnings"]})

    def test_currency_filter_limits_rows_and_buckets(self):
        payload = self.payload(currency="USD")
        self.assertEqual({item["currency"]["code"] for item in payload["summary_by_currency"]}, {"USD"})
        self.assertTrue(all({item["currency"]["code"] for item in row["amounts_by_currency"]} == {"USD"} for row in payload["tasks"]))

    def test_converted_mode_uses_manual_rates_for_this_report(self):
        payload = self.converted(reporting_currency="USD", manual_rates=[{"source_currency": "IRR", "target_currency": "USD", "rate": "0.01"}])
        self.assertEqual(payload["currency_mode"], "converted")
        self.assertTrue(payload["converted_summary"]["is_fully_converted"])
        self.assertEqual(payload["converted_summary"]["recognized_cost"], "11.00")

    def test_converted_mode_reports_missing_rates_without_zero_assumption(self):
        payload = self.converted(reporting_currency="EUR")
        self.assertFalse(payload["converted_summary"]["is_fully_converted"])
        self.assertIn("missing_exchange_rate", {item["code"] for item in payload["warnings"]})
        self.assertIn("partially_converted", {item["code"] for item in payload["warnings"]})

    def test_saved_inverse_rate_is_usable(self):
        ExchangeRate.objects.create(source_currency="USD", target_currency="IRR", rate=Decimal("100.00"), effective_date=self.today - timedelta(days=1), created_by=self.admin)
        payload = self.converted(reporting_currency="USD")
        self.assertTrue(payload["converted_summary"]["is_fully_converted"])
        self.assertIn({"source_currency": "IRR", "target_currency": "USD", "rate": "0.0100000000"}, payload["converted_summary"]["rates"])

    def test_financial_control_get_does_not_create_cost_snapshots(self):
        before = VarianceReport.objects.filter(dimension=VarianceReport.DIMENSION_COST).count()
        self.payload()
        self.assertEqual(VarianceReport.objects.filter(dimension=VarianceReport.DIMENSION_COST).count(), before)

    def test_5000_task_currency_queries_are_bounded_and_paginated(self):
        currencies = ["IRR", "USD", "EUR", "AED"]
        Currency.objects.get_or_create(code="AED", defaults={"name": "AED", "symbol": "AED"})
        sources = {
            "IRR": self.irr_source,
            "USD": self.usd_source,
            "EUR": FundingSource.objects.create(title="EUR Source", source_type="CONTRACT", received_date=self.today, total_amount=Decimal("100000.00"), currency="EUR", status="APPROVED", created_by=self.admin),
            "AED": FundingSource.objects.create(title="AED Source", source_type="CONTRACT", received_date=self.today, total_amount=Decimal("100000.00"), currency="AED", status="APPROVED", created_by=self.admin),
        }
        tasks = [
            Task(project=self.project, created_by=self.admin)
            for _ in range(5000)
        ]
        Task.objects.bulk_create(tasks, batch_size=500)
        created_tasks = list(Task.objects.filter(project=self.project, versions__isnull=True).order_by("created_at", "id")[:5000])
        budgets = []
        costs = []
        snapshots = []
        for index, task in enumerate(created_tasks):
            code = currencies[index % len(currencies)]
            budgets.append(BudgetAllocation(funding_source=sources[code], project=self.project, revision=self.revision, scope_type="TASK", task=task, cost_type="COST", allocated_amount=Decimal("100.00"), status="APPROVED", created_by=self.admin))
            costs.append(CostTransaction(project=self.project, revision=self.revision, task=task, transaction_type="COST", amount=Decimal("10.00"), currency=code, transaction_date=self.today, created_by=self.admin))
            snapshots.append(VarianceReport(task=task, revision=self.revision, report_date=self.today, dimension=VarianceReport.DIMENSION_COST, currency=code, budget_at_completion=Decimal("100.00"), planned_value=Decimal("50.00"), earned_value=Decimal("20.00"), actual_cost=Decimal("10.00")))
        BudgetAllocation.objects.bulk_create(budgets, batch_size=500)
        CostTransaction.objects.bulk_create(costs, batch_size=500)
        VarianceReport.objects.bulk_create(snapshots, batch_size=500)

        results = {}
        for label, params in {
            "page1": {"project_id": self.project.id, "page_size": 25},
            "page2": {"project_id": self.project.id, "page": 2, "page_size": 25},
            "currency": {"project_id": self.project.id, "currency": "USD", "page_size": 25},
            "search": {"project_id": self.project.id, "search": "USD Cost Task", "page_size": 25},
        }.items():
            start = perf_counter()
            with CaptureQueriesContext(connection) as captured:
                response = api(self.admin).get(self.url, params)
            duration = perf_counter() - start
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
            self.assertLessEqual(len(response.data["tasks"]), 25)
            self.assertLess(len(captured), 30)
            results[label] = (len(captured), duration)
        self.assertLessEqual(abs(results["page1"][0] - results["page2"][0]), 2)
