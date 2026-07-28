from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Case, CharField, DecimalField, F, Q, Sum, Value, When
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework.exceptions import PermissionDenied, ValidationError

from .models import (
    BudgetAllocation,
    CostTransaction,
    CostTransactionMilestoneAllocation,
    Currency,
    ExchangeRate,
    PaymentTransaction,
    Task,
    TaskDelivery,
    TaskFinancialPlan,
    VarianceReport,
    WBSNodeVersion,
)

MONEY_QUANT = Decimal("0.01")
RATIO_QUANT = Decimal("0.0001")
ZERO = Decimal("0.00")

WARNING_CODES = {
    "recognized_cost_exceeds_budget",
    "recognized_cost_exceeds_contract",
    "unallocated_recognized_cost",
    "payment_exceeds_recognized_cost",
    "delivery_pending_approval",
    "cost_transaction_without_financial_plan",
    "missing_approved_budget",
    "missing_cost_evm_snapshot",
    "stale_cost_evm_snapshot",
    "allocated_cost_exceeds_recognized_cost",
    "paid_amount_exceeds_contract",
    "negative_outstanding_payment",
    "cpi_below_one",
    "payment_currency_differs_from_plan",
    "missing_exchange_rate",
    "partially_converted",
}

ORDERING_FIELDS = {
    "task_code",
    "task_title",
    "approved_budget",
    "recognized_cost",
    "allocated_recognized_cost",
    "unallocated_recognized_cost",
    "paid_amount",
    "outstanding_payment",
    "cpi",
    "spi",
    "health",
}
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
BASE_CURRENCY_CODE = "IRR"
AMOUNT_FIELDS = [
    "approved_budget",
    "recognized_cost",
    "allocated_recognized_cost",
    "unallocated_recognized_cost",
    "contract_value",
    "paid_amount",
    "outstanding_payment",
    "bac",
    "pv",
    "ev",
    "ac",
    "cv",
    "sv",
]


def money(value):
    return Decimal(value or 0).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def money_str(value):
    return str(money(value))


def _currency_code(value):
    if value in (None, ""):
        return None
    return str(value).upper()


def _currency_meta_map(codes):
    normalized = sorted({code for code in (_currency_code(item) for item in codes) if code})
    rows = Currency.objects.filter(code__in=normalized)
    found = {
        row.code: {"code": row.code, "name": row.name, "symbol": row.symbol}
        for row in rows
    }
    return {
        code: found.get(code, {"code": code, "name": code, "symbol": code})
        for code in normalized
    }


def ratio(value):
    if value is None:
        return None
    return Decimal(value).quantize(RATIO_QUANT, rounding=ROUND_HALF_UP)


def ratio_str(value):
    rounded = ratio(value)
    return str(rounded) if rounded is not None else None


def _sum_by_task_currency(queryset, task_field, currency_field, amount_field="amount"):
    rows = queryset.values(task_field, currency_field).annotate(total=Sum(amount_field))
    totals = {}
    for row in rows:
        task_id = row.get(task_field)
        code = _currency_code(row.get(currency_field))
        if not task_id or not code:
            continue
        totals[(task_id, code)] = money(row["total"])
    return totals


def _parse_status_date(value):
    if value in (None, ""):
        return None
    parsed = parse_date(str(value))
    if parsed is None:
        raise ValidationError({"status_date": "Invalid status_date. Use YYYY-MM-DD."})
    return parsed


def _task_version(task):
    versions = list(task.versions.all())
    current_revision_id = getattr(task.project, "current_execution_revision_id", None)
    for version in versions:
        if current_revision_id and version.revision_id == current_revision_id:
            return version
    if versions:
        return versions[0]
    return None


def _task_code(version):
    if version and version.wbs_node:
        return version.wbs_node.wbs_code
    return ""


def _wbs_code_map(versions):
    targets = {}
    for version in versions:
        if not version or not version.wbs_node_id:
            continue
        wbs_node = getattr(version, "wbs_node", None)
        if not wbs_node:
            continue
        targets[wbs_node.id] = wbs_node
    if not targets:
        return {}
    root_codes = {
        wbs_id: str(wbs_node.sequence)
        for wbs_id, wbs_node in targets.items()
        if getattr(wbs_node, "level", None) == 0
    }
    nested_targets = {
        wbs_id: wbs_node for wbs_id, wbs_node in targets.items()
        if wbs_id not in root_codes
    }
    if not nested_targets:
        return root_codes
    revision_ids = {wbs_node.revision_id for wbs_node in nested_targets.values()}
    tree_ids = {wbs_node.tree_id for wbs_node in nested_targets.values()}
    rows_by_tree = {}
    for row in WBSNodeVersion.objects.filter(revision_id__in=revision_ids, tree_id__in=tree_ids).values("id", "tree_id", "lft", "rght", "sequence"):
        rows_by_tree.setdefault(row["tree_id"], []).append(row)
    for rows in rows_by_tree.values():
        rows.sort(key=lambda item: item["lft"])
    codes = dict(root_codes)
    for wbs_id, target in nested_targets.items():
        ancestors = [
            row for row in rows_by_tree.get(target.tree_id, [])
            if row["lft"] <= target.lft and row["rght"] >= target.rght
        ]
        codes[wbs_id] = ".".join(str(row["sequence"]) for row in ancestors)
    return codes


def _warning(code, severity, task_id, message):
    return {
        "code": code,
        "severity": severity,
        "task_id": str(task_id) if task_id else None,
        "message": message,
    }


def _date_str(value):
    return value.isoformat() if value else None


def _status_date_str(status_date):
    return _date_str(status_date or timezone.localdate())


def _health(task):
    warnings = {item["code"] for item in task["warnings"]}
    cpi = Decimal(task["cpi"]) if task["cpi"] is not None else None
    has_data = (
        Decimal(task["approved_budget"]) > ZERO
        or Decimal(task["bac"]) > ZERO
        or Decimal(task["recognized_cost"]) > ZERO
        or Decimal(task["contract_value"]) > ZERO
    )
    if (
        "recognized_cost_exceeds_budget" in warnings
        or "payment_exceeds_recognized_cost" in warnings
        or "paid_amount_exceeds_contract" in warnings
        or "negative_outstanding_payment" in warnings
        or "allocated_cost_exceeds_recognized_cost" in warnings
        or (cpi is not None and cpi < Decimal("0.8000"))
    ):
        return "critical"
    if (
        "unallocated_recognized_cost" in warnings
        or "delivery_pending_approval" in warnings
        or "missing_cost_evm_snapshot" in warnings
        or "stale_cost_evm_snapshot" in warnings
        or (cpi is not None and Decimal("0.8000") <= cpi < Decimal("1.0000"))
    ):
        return "warning"
    if not has_data:
        return "unknown"
    return "good"


def _amount_totals(items):
    totals = {field: ZERO for field in AMOUNT_FIELDS}
    for task in items:
        for field in AMOUNT_FIELDS:
            totals[field] += Decimal(task[field])
    ev = totals["ev"]
    ac = totals["ac"]
    pv = totals["pv"]
    bac = totals["bac"]
    cpi = ev / ac if ac else None
    spi = ev / pv if pv else None
    eac = bac / cpi if cpi and cpi > ZERO else None
    vac = bac - eac if eac is not None else None
    return {
        **{field: money_str(value) for field, value in totals.items()},
        "cpi": ratio_str(cpi),
        "spi": ratio_str(spi),
        "eac": money_str(eac) if eac is not None else None,
        "vac": money_str(vac) if vac is not None else None,
    }


def _totals(tasks):
    return _amount_totals(tasks)


def _empty_amount_bucket(code):
    return {
        "currency_code": code,
        **{field: ZERO for field in AMOUNT_FIELDS},
        "cpi": None,
        "spi": None,
        "eac": None,
        "vac": None,
    }


def _finalize_amount_bucket(bucket, currency_map):
    ev = bucket["ev"]
    ac = bucket["ac"]
    pv = bucket["pv"]
    bac = bucket["bac"]
    cpi = ev / ac if ac else None
    spi = ev / pv if pv else None
    eac = bac / cpi if cpi and cpi > ZERO else None
    vac = bac - eac if eac is not None else None
    return {
        "currency": currency_map.get(bucket["currency_code"], {
            "code": bucket["currency_code"],
            "name": bucket["currency_code"],
            "symbol": bucket["currency_code"],
        }),
        **{field: money_str(bucket[field]) for field in AMOUNT_FIELDS},
        "cpi": ratio_str(cpi),
        "spi": ratio_str(spi),
        "eac": money_str(eac) if eac is not None else None,
        "vac": money_str(vac) if vac is not None else None,
    }


def _summary_by_currency(rows, currency_map):
    buckets = {}
    for row in rows:
        for amount in row.get("amounts_by_currency", []):
            code = amount["currency"]["code"]
            bucket = buckets.setdefault(code, _empty_amount_bucket(code))
            for field in AMOUNT_FIELDS:
                bucket[field] += Decimal(amount[field])
    return [_finalize_amount_bucket(bucket, currency_map) for _, bucket in sorted(buckets.items())]


def _with_summary_metadata(summary, tasks, status_date):
    snapshot_dates = [task["evm_snapshot_date"] for task in tasks if task["evm_snapshot_date"]]
    return {
        **summary,
        "status_date": _status_date_str(status_date),
        "evm_snapshot_date": max(snapshot_dates) if snapshot_dates else None,
        "evm_is_stale": any(task["evm_is_stale"] for task in tasks),
        "data_as_of": _status_date_str(status_date),
        "sources": {
            "budget_source": "BudgetAllocation",
            "recognized_cost_source": "CostTransaction",
            "payment_source": "PaymentTransaction",
            "evm_source": "VarianceReport",
        },
    }


def _parse_positive_int(params, key, default, max_value=None):
    value = params.get(key)
    if value in (None, ""):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValidationError({key: "Must be a positive integer."})
    if parsed < 1:
        raise ValidationError({key: "Must be a positive integer."})
    if max_value:
        return min(parsed, max_value)
    return parsed


def _warning_summary(rows):
    counts = {code: 0 for code in sorted(WARNING_CODES)}
    tasks_with_warnings = 0
    for row in rows:
        codes = {item["code"] for item in row["warnings"]}
        if codes:
            tasks_with_warnings += 1
        for code in codes:
            counts[code] = counts.get(code, 0) + 1
    return {
        "total_tasks_with_warnings": tasks_with_warnings,
        "by_code": counts,
    }


def _sort_rows(rows, ordering):
    if not ordering:
        return rows
    descending = ordering.startswith("-")
    field = ordering[1:] if descending else ordering
    if field not in ORDERING_FIELDS:
        raise ValidationError({"ordering": "Invalid ordering field."})

    field_name = "financial_health" if field == "health" else field
    health_rank = {"critical": 0, "warning": 1, "unknown": 2, "good": 3}

    def sort_key(row):
        value = row.get(field_name)
        if field == "health":
            return health_rank.get(value, 9)
        if field in {"task_code", "task_title"}:
            return str(value or "").lower()
        if value in (None, ""):
            return Decimal("-999999999999999999.99")
        return Decimal(str(value))

    return sorted(rows, key=sort_key, reverse=descending)


def _pagination(page, page_size, total_items):
    total_pages = (total_items + page_size - 1) // page_size if total_items else 0
    return {
        "page": page,
        "page_size": page_size,
        "total_items": total_items,
        "total_pages": total_pages,
        "has_next": page < total_pages,
        "has_previous": page > 1 and total_pages > 0,
    }


def _apply_status_date(queryset, status_date, field):
    if not status_date:
        return queryset
    return queryset.filter(**{f"{field}__lte": status_date})


def _apply_datetime_status_date(queryset, status_date, field):
    if not status_date:
        return queryset
    return queryset.filter(Q(**{f"{field}__date__lte": status_date}) | Q(**{f"{field}__isnull": True}))


def _delivery_as_of_filter(status_date):
    if not status_date:
        return Q()
    return (
        Q(status=TaskDelivery.STATUS_DRAFT, created_at__date__lte=status_date)
        | Q(status=TaskDelivery.STATUS_SUBMITTED, submitted_at__date__lte=status_date)
        | Q(status=TaskDelivery.STATUS_APPROVED, approved_at__date__lte=status_date)
        | Q(status=TaskDelivery.STATUS_REJECTED, rejected_at__date__lte=status_date)
        | Q(status=TaskDelivery.STATUS_CANCELLED, cancelled_at__date__lte=status_date)
    )


def _parse_manual_rates(value):
    if not value:
        return []
    if isinstance(value, list):
        rates = value
    else:
        rates = []
    parsed = []
    for item in rates:
        source = _currency_code(item.get("source_currency"))
        target = _currency_code(item.get("target_currency"))
        rate_value = item.get("rate")
        if not source or not target or rate_value in (None, ""):
            raise ValidationError({"manual_rates": "Each rate requires source_currency, target_currency, and rate."})
        try:
            rate_decimal = Decimal(str(rate_value))
        except Exception as exc:
            raise ValidationError({"manual_rates": "Rate must be numeric."}) from exc
        if rate_decimal <= ZERO:
            raise ValidationError({"manual_rates": "Rate must be greater than zero."})
        parsed.append((source, target, rate_decimal))
    return parsed


def _saved_rate_map(status_date):
    queryset = ExchangeRate.objects.all()
    if status_date:
        queryset = queryset.filter(effective_date__lte=status_date)
    latest = {}
    for rate in queryset.order_by("source_currency", "target_currency", "-effective_date", "-created_at", "-id"):
        key = (_currency_code(rate.source_currency), _currency_code(rate.target_currency))
        latest.setdefault(key, Decimal(rate.rate))
    return latest


def _rate_lookup(source, target, manual_rates, saved_rates):
    source = _currency_code(source)
    target = _currency_code(target)
    if not source or not target:
        return None
    if source == target:
        return Decimal("1")
    rates = dict(saved_rates)
    for manual_source, manual_target, manual_rate in manual_rates:
        rates[(manual_source, manual_target)] = manual_rate

    direct = rates.get((source, target))
    if direct:
        return direct
    inverse = rates.get((target, source))
    if inverse:
        return Decimal("1") / inverse

    source_to_base = rates.get((source, BASE_CURRENCY_CODE))
    if not source_to_base:
        base_to_source = rates.get((BASE_CURRENCY_CODE, source))
        source_to_base = Decimal("1") / base_to_source if base_to_source else None
    target_to_base = rates.get((target, BASE_CURRENCY_CODE))
    if not target_to_base:
        base_to_target = rates.get((BASE_CURRENCY_CODE, target))
        target_to_base = Decimal("1") / base_to_target if base_to_target else None
    if source_to_base and target_to_base:
        return source_to_base / target_to_base
    return None


def _converted_summary(summary_by_currency, reporting_currency, status_date, manual_rates):
    manual = _parse_manual_rates(manual_rates)
    saved = _saved_rate_map(status_date)
    reporting = _currency_code(reporting_currency)
    if not reporting:
        raise ValidationError({"reporting_currency": "reporting_currency is required for converted mode."})
    currency_map = _currency_meta_map([reporting])
    totals = {field: ZERO for field in AMOUNT_FIELDS}
    warnings = []
    fully_converted = True
    rate_details = []

    for bucket in summary_by_currency:
        source = bucket["currency"]["code"]
        rate = _rate_lookup(source, reporting, manual, saved)
        if rate is None:
            fully_converted = False
            warnings.append(_warning("missing_exchange_rate", "warning", None, f"Missing exchange rate from {source} to {reporting}."))
            continue
        rate_details.append({
            "source_currency": source,
            "target_currency": reporting,
            "rate": str(rate.quantize(Decimal("0.0000000001"))),
        })
        for field in AMOUNT_FIELDS:
            totals[field] += Decimal(bucket[field]) * rate

    converted = _amount_totals([{field: money_str(totals[field]) for field in AMOUNT_FIELDS}])
    if not fully_converted:
        warnings.append(_warning("partially_converted", "warning", None, "Converted totals exclude currencies without an exchange rate."))
    return {
        "reporting_currency": currency_map.get(reporting, {"code": reporting, "name": reporting, "symbol": reporting}),
        "converted_summary": {
            **converted,
            "is_fully_converted": fully_converted,
            "rates": rate_details,
        },
        "conversion_warnings": warnings,
    }


def build_financial_control_payload(user, params):
    status_date = _parse_status_date(params.get("status_date"))
    currency_mode = (params.get("currency_mode") or "native").strip().lower()
    if currency_mode not in {"native", "converted"}:
        raise ValidationError({"currency_mode": "currency_mode must be native or converted."})
    reporting_currency = _currency_code(params.get("reporting_currency"))
    if currency_mode == "converted" and not reporting_currency:
        raise ValidationError({"reporting_currency": "reporting_currency is required for converted mode."})
    currency_filter = _currency_code(params.get("currency"))
    accessible_ids = accessible_ids_list(params.pop("_accessible_project_ids"))
    project_id = params.get("project_id")
    if project_id and str(project_id) not in {str(item) for item in accessible_ids}:
        raise PermissionDenied("You do not have access to this project.")

    task_queryset = Task.objects.filter(project_id__in=accessible_ids).select_related("project")
    if project_id:
        task_queryset = task_queryset.filter(project_id=project_id)
    task_id = params.get("task_id")
    if task_id:
        task_queryset = task_queryset.filter(pk=task_id)
    delivery_status = params.get("delivery_status")
    if delivery_status:
        task_queryset = task_queryset.filter(deliveries__status=delivery_status).distinct()
    search = (params.get("search") or "").strip()
    if search:
        task_queryset = task_queryset.filter(
            Q(project__name__icontains=search)
            | Q(versions__title__icontains=search)
        ).distinct()

    task_queryset = task_queryset.prefetch_related("versions__wbs_node")
    tasks = list(task_queryset.order_by("project__name", "created_at", "id"))
    task_ids = [task.id for task in tasks]
    task_versions = {task.id: _task_version(task) for task in tasks}
    wbs_codes = _wbs_code_map(task_versions.values())
    page = _parse_positive_int(params, "page", 1)
    page_size = _parse_positive_int(params, "page_size", DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE)

    if not task_ids:
        currency_map = _currency_meta_map([reporting_currency] if reporting_currency else [])
        payload = {
            "currency_mode": currency_mode,
            "reporting_currency": currency_map.get(reporting_currency) if reporting_currency else None,
            "summary_by_currency": [],
            "converted_summary": None,
            "summary": _with_summary_metadata(_totals([]), [], status_date),
            "warnings_summary": _warning_summary([]),
            "pagination": _pagination(page, page_size, 0),
            "tasks": [],
            "warnings": [],
        }
        if currency_mode == "converted":
            converted = _converted_summary([], reporting_currency, status_date, params.get("_manual_rates"))
            payload.update(converted)
        return payload

    budget_queryset = (
        _apply_datetime_status_date(
            BudgetAllocation.objects.filter(
                task_id__in=task_ids,
                scope_type="TASK",
                status="APPROVED",
                funding_source__status="APPROVED",
                is_borrow_sink=False,
            ),
            status_date,
            "approved_at",
        )
        .annotate(currency_code=F("funding_source__currency"))
    )
    budget_by_task_currency = _sum_by_task_currency(budget_queryset, "task_id", "currency_code", "allocated_amount")
    cost_queryset = _apply_status_date(
        CostTransaction.objects.filter(task_id__in=task_ids),
        status_date,
        "transaction_date",
    ).annotate(
        currency_code=Coalesce(
            "currency",
            "financial_plan__currency",
            "budget_allocation__funding_source__currency",
            output_field=CharField(),
        )
    )
    recognized_by_task_currency = _sum_by_task_currency(cost_queryset, "task_id", "currency_code", "amount")
    allocated_rows = (
        _apply_datetime_status_date(
            CostTransactionMilestoneAllocation.objects.filter(
                cost_transaction__task_id__in=task_ids,
                cost_transaction__transaction_date__lte=status_date if status_date else None,
            )
            if status_date
            else CostTransactionMilestoneAllocation.objects.filter(cost_transaction__task_id__in=task_ids),
            status_date,
            "created_at",
        )
        .annotate(
            currency_code=Coalesce(
                "cost_transaction__currency",
                "cost_transaction__financial_plan__currency",
                "cost_transaction__budget_allocation__funding_source__currency",
                output_field=CharField(),
            )
        )
        .values("cost_transaction__task_id", "currency_code")
        .annotate(total=Sum("allocated_amount"))
    )
    allocated_by_task_currency = {
        (row["cost_transaction__task_id"], _currency_code(row["currency_code"])): money(row["total"])
        for row in allocated_rows
        if row["cost_transaction__task_id"] and _currency_code(row["currency_code"])
    }
    contract_queryset = TaskFinancialPlan.objects.filter(
            task_id__in=task_ids,
            direction=TaskFinancialPlan.DIRECTION_PAYABLE,
    )
    contract_queryset = _apply_datetime_status_date(contract_queryset, status_date, "created_at")
    contract_rows = (
        contract_queryset
        .exclude(status=TaskFinancialPlan.STATUS_CANCELLED)
        .values("task_id", "currency")
        .annotate(total=Sum("contract_amount"))
    )
    contract_by_task_currency = {
        (row["task_id"], _currency_code(row["currency"])): money(row["total"])
        for row in contract_rows
        if _currency_code(row["currency"])
    }
    payment_amount = Case(
        When(transaction_type=PaymentTransaction.TYPE_REFUND, then=-F("amount")),
        default=F("amount"),
        output_field=DecimalField(max_digits=18, decimal_places=2),
    )
    payment_queryset = (
        _apply_status_date(
            PaymentTransaction.objects.filter(
                milestone__financial_plan__task_id__in=task_ids,
                milestone__financial_plan__direction=TaskFinancialPlan.DIRECTION_PAYABLE,
            ),
            status_date,
            "transaction_date",
        )
        .annotate(currency_code=Coalesce("currency", "milestone__financial_plan__currency", output_field=CharField()))
    )
    payment_rows = (
        payment_queryset
        .values("milestone__financial_plan__task_id", "currency_code")
        .annotate(total=Sum(payment_amount))
    )
    paid_by_task_currency = {
        (row["milestone__financial_plan__task_id"], _currency_code(row["currency_code"])): money(row["total"])
        for row in payment_rows
        if row["milestone__financial_plan__task_id"] and _currency_code(row["currency_code"])
    }
    payment_currency_mismatch_tasks = set(
        payment_queryset
        .exclude(currency__isnull=True)
        .exclude(currency="")
        .exclude(currency=F("milestone__financial_plan__currency"))
        .values_list("milestone__financial_plan__task_id", flat=True)
        .distinct()
    )
    latest_deliveries = {}
    for delivery in (
        TaskDelivery.objects.filter(task_id__in=task_ids)
        .filter(_delivery_as_of_filter(status_date))
        .order_by("task_id", "-created_at", "-id")
        .only("id", "task_id", "status", "delivery_reference")
    ):
        latest_deliveries.setdefault(delivery.task_id, delivery)

    currency_codes = set()
    for mapping in (
        budget_by_task_currency,
        recognized_by_task_currency,
        allocated_by_task_currency,
        contract_by_task_currency,
        paid_by_task_currency,
    ):
        currency_codes.update(code for _, code in mapping.keys())
    if currency_filter:
        currency_codes.add(currency_filter)
    if reporting_currency:
        currency_codes.add(reporting_currency)
    currency_map = _currency_meta_map(currency_codes)

    evm_queryset = VarianceReport.objects.filter(
        task_id__in=task_ids,
        dimension=VarianceReport.DIMENSION_COST,
        currency__isnull=False,
    )
    if currency_filter:
        evm_queryset = evm_queryset.filter(currency=currency_filter)
    if status_date:
        evm_queryset = evm_queryset.filter(report_date__lte=status_date)
    evm_by_task_currency = {}
    for report in evm_queryset.order_by("task_id", "currency", "-report_date", "-id"):
        evm_by_task_currency.setdefault((report.task_id, _currency_code(report.currency)), report)
    currencies_by_task = {}
    for mapping in (
        budget_by_task_currency,
        recognized_by_task_currency,
        allocated_by_task_currency,
        contract_by_task_currency,
        paid_by_task_currency,
        evm_by_task_currency,
    ):
        for task_id_key, code in mapping.keys():
            currencies_by_task.setdefault(task_id_key, set()).add(code)

    unplanned_cost_tasks = set(
        cost_queryset.filter(financial_plan__isnull=True)
        .order_by()
        .values_list("task_id", flat=True)
        .distinct()
    )

    rows = []
    warnings = []
    for task in tasks:
        version = task_versions.get(task.id)
        delivery = latest_deliveries.get(task.id)
        task_currency_codes = set(currencies_by_task.get(task.id, set()))
        if currency_filter:
            task_currency_codes = {code for code in task_currency_codes if code == currency_filter}
        amounts_by_currency = []
        scalar_bucket = _empty_amount_bucket(currency_filter or "")
        evm_snapshot_dates = []
        evm_is_stale = False
        for code in sorted(task_currency_codes):
            approved_budget = budget_by_task_currency.get((task.id, code), ZERO)
            recognized_cost = recognized_by_task_currency.get((task.id, code), ZERO)
            allocated_cost = allocated_by_task_currency.get((task.id, code), ZERO)
            unallocated_cost = recognized_cost - allocated_cost
            contract_value = contract_by_task_currency.get((task.id, code), ZERO)
            paid_amount = paid_by_task_currency.get((task.id, code), ZERO)
            outstanding_payment = contract_value - paid_amount
            evm = evm_by_task_currency.get((task.id, code))
            evm_snapshot_date = evm.report_date if evm else None
            if evm_snapshot_date:
                evm_snapshot_dates.append(evm_snapshot_date)
            evm_is_stale = evm_is_stale or bool(status_date and evm_snapshot_date and evm_snapshot_date < status_date)
            bac = money(evm.budget_at_completion if evm else ZERO)
            pv = money(evm.planned_value if evm else ZERO)
            ev = money(evm.earned_value if evm else ZERO)
            ac = money(evm.actual_cost if evm else ZERO)
            bucket = _empty_amount_bucket(code)
            bucket.update({
                "approved_budget": approved_budget,
                "recognized_cost": recognized_cost,
                "allocated_recognized_cost": allocated_cost,
                "unallocated_recognized_cost": unallocated_cost,
                "contract_value": contract_value,
                "paid_amount": paid_amount,
                "outstanding_payment": outstanding_payment,
                "bac": bac,
                "pv": pv,
                "ev": ev,
                "ac": ac,
                "cv": ev - ac,
                "sv": ev - pv,
            })
            amounts_by_currency.append(_finalize_amount_bucket(bucket, currency_map))
            for field in AMOUNT_FIELDS:
                scalar_bucket[field] += bucket[field]
        if currency_filter and not amounts_by_currency:
            continue
        scalar = (
            _finalize_amount_bucket(scalar_bucket, currency_map)
            if len(amounts_by_currency) <= 1
            else _finalize_amount_bucket(_empty_amount_bucket(""), currency_map)
        )
        task_warnings = []
        for amount in amounts_by_currency:
            code = amount["currency"]["code"]
            approved_budget = Decimal(amount["approved_budget"])
            recognized_cost = Decimal(amount["recognized_cost"])
            allocated_cost = Decimal(amount["allocated_recognized_cost"])
            unallocated_cost = Decimal(amount["unallocated_recognized_cost"])
            contract_value = Decimal(amount["contract_value"])
            paid_amount = Decimal(amount["paid_amount"])
            outstanding_payment = Decimal(amount["outstanding_payment"])
            if approved_budget > ZERO and recognized_cost > approved_budget:
                task_warnings.append(_warning("recognized_cost_exceeds_budget", "critical", task.id, f"Recognized cost exceeds approved budget in {code}."))
            if contract_value > ZERO and recognized_cost > contract_value:
                task_warnings.append(_warning("recognized_cost_exceeds_contract", "critical", task.id, f"Recognized cost exceeds contract value in {code}."))
            if allocated_cost > recognized_cost:
                task_warnings.append(_warning("allocated_cost_exceeds_recognized_cost", "critical", task.id, f"Allocated recognized cost exceeds recognized cost in {code}."))
            if unallocated_cost > ZERO:
                task_warnings.append(_warning("unallocated_recognized_cost", "warning", task.id, f"Recognized cost is not fully allocated to milestones in {code}."))
            if paid_amount > recognized_cost:
                task_warnings.append(_warning("payment_exceeds_recognized_cost", "critical", task.id, f"Paid amount exceeds recognized cost in {code}."))
            if contract_value > ZERO and paid_amount > contract_value:
                task_warnings.append(_warning("paid_amount_exceeds_contract", "critical", task.id, f"Paid amount exceeds contract value in {code}."))
            if outstanding_payment < ZERO:
                task_warnings.append(_warning("negative_outstanding_payment", "critical", task.id, f"Outstanding payment is negative in {code}."))
            needs_cost_evm_snapshot = (
                approved_budget > ZERO
                or recognized_cost > ZERO
                or allocated_cost > ZERO
            )
            if needs_cost_evm_snapshot and (task.id, code) not in evm_by_task_currency:
                task_warnings.append(_warning("missing_cost_evm_snapshot", "warning", task.id, f"No Cost EVM snapshot is available for this task in {code}."))
        if delivery and delivery.status == TaskDelivery.STATUS_SUBMITTED:
            task_warnings.append(_warning("delivery_pending_approval", "warning", task.id, "Delivery is submitted and waiting for approval."))
        if task.id in unplanned_cost_tasks:
            task_warnings.append(_warning("cost_transaction_without_financial_plan", "warning", task.id, "Cost transaction is not linked to a financial plan."))
        if Decimal(scalar["approved_budget"]) == ZERO and Decimal(scalar["recognized_cost"]) > ZERO:
            task_warnings.append(_warning("missing_approved_budget", "warning", task.id, "Recognized cost exists without approved task budget."))
        if evm_is_stale:
            task_warnings.append(_warning("stale_cost_evm_snapshot", "warning", task.id, "Cost EVM snapshot is older than the requested status date."))
        if task.id in payment_currency_mismatch_tasks:
            task_warnings.append(_warning("payment_currency_differs_from_plan", "warning", task.id, "At least one payment was recorded in a different currency than its financial plan."))
        if scalar["cpi"] is not None and Decimal(scalar["cpi"]) < Decimal("1.0000"):
            task_warnings.append(_warning("cpi_below_one", "warning" if Decimal(scalar["cpi"]) >= Decimal("0.8000") else "critical", task.id, "CPI is below 1.0."))

        row = {
            "task_id": str(task.id),
            "task_code": wbs_codes.get(version.wbs_node_id, "") if version and version.wbs_node_id else "",
            "task_title": version.title if version else "Task without an official execution version",
            "project_id": str(task.project_id),
            "project_name": task.project.name,
            "amounts_by_currency": amounts_by_currency,
            "approved_budget": scalar["approved_budget"],
            "recognized_cost": scalar["recognized_cost"],
            "allocated_recognized_cost": scalar["allocated_recognized_cost"],
            "unallocated_recognized_cost": scalar["unallocated_recognized_cost"],
            "contract_value": scalar["contract_value"],
            "paid_amount": scalar["paid_amount"],
            "outstanding_payment": scalar["outstanding_payment"],
            "delivery_status": delivery.status if delivery else None,
            "status_date": _status_date_str(status_date),
            "evm_snapshot_date": _date_str(max(evm_snapshot_dates) if evm_snapshot_dates else None),
            "evm_is_stale": evm_is_stale,
            "data_as_of": _status_date_str(status_date),
            "sources": {
                "budget_source": "BudgetAllocation",
                "recognized_cost_source": "CostTransaction",
                "payment_source": "PaymentTransaction",
                "evm_source": "VarianceReport",
            },
            "bac": scalar["bac"],
            "pv": scalar["pv"],
            "ev": scalar["ev"],
            "ac": scalar["ac"],
            "cv": scalar["cv"],
            "sv": scalar["sv"],
            "cpi": scalar["cpi"],
            "spi": scalar["spi"],
            "eac": scalar["eac"],
            "vac": scalar["vac"],
            "financial_health": "unknown",
            "warnings": task_warnings,
        }
        row["financial_health"] = _health(row)
        warnings.extend(task_warnings)
        rows.append(row)

    warning_filter = params.get("warning")
    if warning_filter:
        if warning_filter not in WARNING_CODES:
            raise ValidationError({"warning": "Invalid warning code."})
        rows = [row for row in rows if any(item["code"] == warning_filter for item in row["warnings"])]
        warnings = [item for row in rows for item in row["warnings"]]
    health_filter = params.get("health")
    if health_filter:
        valid_health = {"good", "warning", "critical", "unknown"}
        if health_filter not in valid_health:
            raise ValidationError({"health": "Invalid health value."})
        rows = [row for row in rows if row["financial_health"] == health_filter]
        warnings = [item for row in rows for item in row["warnings"]]

    rows = _sort_rows(rows, (params.get("ordering") or "").strip())
    total_items = len(rows)
    start = (page - 1) * page_size
    page_rows = rows[start:start + page_size]
    summary_by_currency = _summary_by_currency(rows, currency_map)
    conversion_warnings = []
    converted_summary = None
    reporting_currency_meta = None
    if currency_mode == "converted":
        conversion = _converted_summary(summary_by_currency, reporting_currency, status_date, params.get("_manual_rates"))
        converted_summary = conversion["converted_summary"]
        reporting_currency_meta = conversion["reporting_currency"]
        conversion_warnings = conversion["conversion_warnings"]
        warnings.extend(conversion_warnings)

    legacy_summary = _totals(rows) if len(summary_by_currency) <= 1 else _totals([])
    return {
        "currency_mode": currency_mode,
        "reporting_currency": reporting_currency_meta,
        "summary_by_currency": summary_by_currency,
        "converted_summary": converted_summary,
        "summary": _with_summary_metadata(legacy_summary, rows, status_date),
        "warnings_summary": _warning_summary(rows),
        "pagination": _pagination(page, page_size, total_items),
        "tasks": page_rows,
        "warnings": [item for row in page_rows for item in row["warnings"]] + conversion_warnings,
    }


def accessible_ids_list(value):
    return list(value)
