from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import CostTransaction, PaymentMilestone, PaymentTransaction, TaskFinancialPlan, TaskReportLog

MONEY_QUANT = Decimal("0.01")
PROGRESS_QUANT = Decimal("0.01")


def money(value):
    return Decimal(value or 0).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def progress(value):
    return Decimal(value or 0).quantize(PROGRESS_QUANT, rounding=ROUND_HALF_UP)


def as_str(value):
    return str(money(value))


def get_active_plan(task):
    return (
        TaskFinancialPlan.objects.filter(task=task, status=TaskFinancialPlan.STATUS_ACTIVE)
        .prefetch_related("milestones__transactions")
        .first()
    )


def get_approved_progress(task):
    report = (
        TaskReportLog.objects.filter(task=task, approval_status="final_approved")
        .order_by("-timestamp", "-id")
        .first()
    )
    if report:
        return progress(report.progress_percent)

    version = task.versions.filter(revision_id=task.project.current_execution_revision_id, is_deleted=False).select_related("actual").first()
    actual = getattr(version, "actual", None) if version else None
    return progress(actual.progress if actual else 0)


def milestone_amount(milestone):
    if milestone.amount_type == PaymentMilestone.AMOUNT_FIXED:
        return money(milestone.fixed_amount)
    raw = Decimal(milestone.financial_plan.contract_amount) * Decimal(milestone.percentage or 0) / Decimal("100")
    return money(raw)


def transaction_effect(transaction):
    amount = money(transaction.amount)
    if transaction.transaction_type == PaymentTransaction.TYPE_PAYMENT:
        return amount
    if transaction.transaction_type == PaymentTransaction.TYPE_REFUND:
        return -amount
    return amount


def milestone_paid_amount(milestone):
    total = Decimal("0.00")
    for item in milestone.transactions.all():
        total += transaction_effect(item)
    return money(total)


def milestone_outstanding(milestone):
    return money(milestone_amount(milestone) - milestone_paid_amount(milestone))


def allocate_cost_transactions_to_milestones(plan, milestones=None):
    milestones = list(milestones if milestones is not None else plan.milestones.all().order_by("sequence", "id"))
    allocations = {milestone.id: Decimal("0.00") for milestone in milestones}
    remaining_by_milestone = {milestone.id: milestone_amount(milestone) for milestone in milestones}
    cost_transactions = (
        CostTransaction.objects.filter(financial_plan=plan)
        .order_by("transaction_date", "created_at", "id")
    )

    for cost_transaction in cost_transactions:
        remaining_cost = money(cost_transaction.amount)
        for milestone in milestones:
            if remaining_cost <= 0:
                break
            milestone_remaining = remaining_by_milestone[milestone.id]
            if milestone_remaining <= 0:
                continue
            applied = min(remaining_cost, milestone_remaining)
            allocations[milestone.id] = money(allocations[milestone.id] + applied)
            remaining_by_milestone[milestone.id] = money(milestone_remaining - applied)
            remaining_cost = money(remaining_cost - applied)
    return allocations


def milestone_incurred_amount(milestone):
    milestones = list(milestone.financial_plan.milestones.all().order_by("sequence", "id"))
    return money(allocate_cost_transactions_to_milestones(milestone.financial_plan, milestones).get(milestone.id, Decimal("0.00")))


def milestone_cost_outstanding(milestone):
    return money(milestone_amount(milestone) - milestone_incurred_amount(milestone))


def is_milestone_paid(milestone):
    return milestone_outstanding(milestone) <= Decimal("0.00")


def is_milestone_triggered(milestone, approved_progress=None, today=None):
    approved_progress = progress(approved_progress if approved_progress is not None else get_approved_progress(milestone.financial_plan.task))
    today = today or timezone.localdate()
    if milestone.trigger_type in {PaymentMilestone.TRIGGER_BEFORE_START, PaymentMilestone.TRIGGER_BEFORE_DELIVERY, PaymentMilestone.TRIGGER_MANUAL}:
        return True
    if milestone.trigger_type == PaymentMilestone.TRIGGER_APPROVED_PROGRESS:
        return approved_progress >= progress(milestone.progress_threshold)
    if milestone.trigger_type == PaymentMilestone.TRIGGER_TASK_COMPLETION:
        return approved_progress >= Decimal("100.00")
    if milestone.trigger_type == PaymentMilestone.TRIGGER_FIXED_DATE:
        return bool(milestone.due_date and milestone.due_date <= today)
    return False


def evaluate_milestone_status(milestone, approved_progress=None, today=None):
    if milestone.status == PaymentMilestone.STATUS_CANCELLED:
        return PaymentMilestone.STATUS_CANCELLED
    paid = milestone_paid_amount(milestone)
    outstanding = milestone_outstanding(milestone)
    if outstanding <= 0:
        return PaymentMilestone.STATUS_PAID
    if paid > 0:
        return PaymentMilestone.STATUS_PARTIALLY_PAID
    today = today or timezone.localdate()
    if milestone.due_date and milestone.due_date < today:
        return PaymentMilestone.STATUS_OVERDUE
    if is_milestone_triggered(milestone, approved_progress=approved_progress, today=today):
        return PaymentMilestone.STATUS_ELIGIBLE
    return PaymentMilestone.STATUS_LOCKED


def refresh_milestone_status(milestone, approved_progress=None):
    new_status = evaluate_milestone_status(milestone, approved_progress=approved_progress)
    if milestone.status != new_status:
        milestone.status = new_status
        milestone.save(update_fields=["status", "updated_at"])
    return new_status


def validate_plan_milestones(plan):
    milestones = list(plan.milestones.all())
    if not milestones:
        raise ValidationError({"milestones": "At least one milestone is required before activation."})

    percentage_total = sum((Decimal(m.percentage or 0) for m in milestones if m.amount_type == PaymentMilestone.AMOUNT_PERCENTAGE), Decimal("0"))
    if percentage_total > Decimal("100"):
        raise ValidationError({"percentage": "Milestone percentages cannot exceed 100%."})

    total = sum((milestone_amount(m) for m in milestones), Decimal("0.00"))
    if money(total) != money(plan.contract_amount):
        raise ValidationError({"contract_amount": "Milestone total must equal the contract amount before activation."})


def activate_plan(plan):
    with transaction.atomic():
        locked_plan = TaskFinancialPlan.objects.select_for_update(of=("self",)).select_related("task", "task__project").get(pk=plan.pk)
        if TaskFinancialPlan.objects.select_for_update().filter(
            task=locked_plan.task,
            status=TaskFinancialPlan.STATUS_ACTIVE,
        ).exclude(pk=locked_plan.pk).exists():
            raise ValidationError({"task": "Only one active financial plan is allowed for each task."})
        validate_plan_milestones(locked_plan)
        locked_plan.status = TaskFinancialPlan.STATUS_ACTIVE
        locked_plan.save(update_fields=["status", "updated_at"])
        evaluate_financial_plan(locked_plan.task)
        return locked_plan


def evaluate_financial_plan(task):
    plan = get_active_plan(task)
    if not plan:
        return None
    approved = get_approved_progress(task)
    for milestone in plan.milestones.all():
        refresh_milestone_status(milestone, approved_progress=approved)
    return plan


def _blocking_payload(milestone, reason):
    return {
        "id": milestone.id,
        "title": milestone.title,
        "sequence": milestone.sequence,
        "reason": reason,
        "amount": as_str(milestone_amount(milestone)),
        "paid_amount": as_str(milestone_paid_amount(milestone)),
        "outstanding": as_str(milestone_outstanding(milestone)),
        "trigger_type": milestone.trigger_type,
        "progress_threshold": str(progress(milestone.progress_threshold)) if milestone.progress_threshold is not None else None,
    }


def get_max_allowed_progress(task, plan=None):
    plan = plan or get_active_plan(task)
    if not plan:
        return Decimal("100.00")
    max_allowed = Decimal("100.00")
    for milestone in plan.milestones.all():
        if (
            milestone.trigger_type == PaymentMilestone.TRIGGER_APPROVED_PROGRESS
            and milestone.blocks_progress_after_threshold
            and not is_milestone_paid(milestone)
            and milestone.progress_threshold is not None
        ):
            max_allowed = min(max_allowed, progress(milestone.progress_threshold))
    return progress(max_allowed)


def get_task_financial_status(task):
    plan = get_active_plan(task)
    approved = get_approved_progress(task)
    if not plan:
        return {
            "has_plan": False,
            "approved_progress": str(approved),
            "can_start": True,
            "can_deliver": True,
            "max_allowed_progress": "100.00",
            "blocking_milestones": [],
            "milestones": [],
        }

    milestones = list(plan.milestones.all())
    cost_allocations = allocate_cost_transactions_to_milestones(plan, milestones)
    for milestone in milestones:
        refresh_milestone_status(milestone, approved_progress=approved)

    milestone_rows = []
    total_amount = total_eligible = total_paid = total_incurred = Decimal("0.00")
    blocking = []
    today = timezone.localdate()

    for milestone in milestones:
        amount = milestone_amount(milestone)
        paid = milestone_paid_amount(milestone)
        outstanding = milestone_outstanding(milestone)
        incurred = cost_allocations.get(milestone.id, Decimal("0.00"))
        cost_outstanding = money(amount - incurred)
        triggered = is_milestone_triggered(milestone, approved_progress=approved, today=today)
        row = {
            "id": milestone.id,
            "title": milestone.title,
            "sequence": milestone.sequence,
            "trigger_type": milestone.trigger_type,
            "amount_type": milestone.amount_type,
            "percentage": str(progress(milestone.percentage)) if milestone.percentage is not None else None,
            "fixed_amount": as_str(milestone.fixed_amount) if milestone.fixed_amount is not None else None,
            "calculated_amount": as_str(amount),
            "progress_threshold": str(progress(milestone.progress_threshold)) if milestone.progress_threshold is not None else None,
            "due_date": milestone.due_date.isoformat() if milestone.due_date else None,
            "status": milestone.status,
            "paid_amount": as_str(paid),
            "outstanding": as_str(outstanding),
            "incurred_amount": as_str(incurred),
            "cost_outstanding": as_str(cost_outstanding),
            "is_triggered": triggered,
            "blocks_task_start": milestone.blocks_task_start,
            "blocks_task_delivery": milestone.blocks_task_delivery,
            "blocks_progress_after_threshold": milestone.blocks_progress_after_threshold,
            "description": milestone.description,
        }
        milestone_rows.append(row)
        total_amount += amount
        total_paid += paid
        total_incurred += incurred
        if triggered:
            total_eligible += amount
        if milestone.blocks_task_start and outstanding > 0:
            blocking.append(_blocking_payload(milestone, "task_start"))
        if milestone.blocks_task_delivery and outstanding > 0:
            blocking.append(_blocking_payload(milestone, "task_delivery"))
        if milestone.blocks_progress_after_threshold and outstanding > 0 and milestone.progress_threshold is not None:
            blocking.append(_blocking_payload(milestone, "progress_threshold"))

    can_start = not any(item["reason"] == "task_start" for item in blocking)
    can_deliver = not any(item["reason"] == "task_delivery" for item in blocking)
    max_allowed = get_max_allowed_progress(task, plan=plan)
    next_milestone = next((row for row in milestone_rows if row["status"] in {PaymentMilestone.STATUS_ELIGIBLE, PaymentMilestone.STATUS_OVERDUE, PaymentMilestone.STATUS_PARTIALLY_PAID}), None)

    return {
        "has_plan": True,
        "plan_id": plan.id,
        "task": str(task.id),
        "contract_amount": as_str(plan.contract_amount),
        "currency": plan.currency,
        "direction": plan.direction,
        "plan_status": plan.status,
        "total_amount": as_str(total_amount),
        "total_eligible": as_str(total_eligible),
        "total_due": as_str(total_eligible),
        "total_paid": as_str(total_paid),
        "total_incurred": as_str(total_incurred),
        "outstanding": as_str(total_amount - total_paid),
        "cost_outstanding": as_str(total_amount - total_incurred),
        "approved_progress": str(approved),
        "can_start": can_start,
        "can_deliver": can_deliver,
        "max_allowed_progress": str(max_allowed),
        "next_milestone": next_milestone,
        "blocking_milestones": blocking,
        "milestones": milestone_rows,
    }


def validate_task_start(task):
    status = get_task_financial_status(task)
    if not status.get("can_start", True):
        blocker = next((item for item in status["blocking_milestones"] if item["reason"] == "task_start"), None)
        raise ValidationError({"financial": f"Task cannot start; milestone '{blocker['title']}' is not fully settled."})


def validate_task_delivery(task):
    status = get_task_financial_status(task)
    if not status.get("can_deliver", True):
        blocker = next((item for item in status["blocking_milestones"] if item["reason"] == "task_delivery"), None)
        raise ValidationError({"financial": f"Task cannot be delivered; milestone '{blocker['title']}' is not fully settled."})


def validate_progress_transition(task, target_progress):
    target = progress(target_progress)
    max_allowed = get_max_allowed_progress(task)
    if target > max_allowed:
        raise ValidationError({"financial": f"Approved progress cannot exceed {max_allowed}% until the blocking financial milestone is settled."})


def register_transaction(milestone, transaction_type, amount, transaction_date, user=None, reference_number="", description=""):
    with transaction.atomic():
        locked = PaymentMilestone.objects.select_for_update().select_related("financial_plan__task").get(pk=milestone.pk)
        PaymentTransaction.objects.select_for_update().filter(milestone=locked).exists()
        tx = PaymentTransaction(
            milestone=locked,
            transaction_type=transaction_type,
            amount=money(amount),
            transaction_date=transaction_date,
            reference_number=reference_number or "",
            description=description or "",
            created_by=user,
        )
        tx.full_clean()
        if transaction_type in {PaymentTransaction.TYPE_PAYMENT, PaymentTransaction.TYPE_ADJUSTMENT} and tx.amount > 0:
            current_outstanding = milestone_outstanding(locked)
            if tx.amount > current_outstanding:
                raise ValidationError({"amount": "Transaction amount exceeds outstanding milestone amount."})
        tx.save()
        refresh_milestone_status(locked)
        return tx

def allocate_plan_payment(plan, amount, transaction_date, user=None, reference_number="", description=""):
    amount = money(amount)
    if amount <= 0:
        raise ValidationError({"amount": "Payment amount must be greater than zero."})

    with transaction.atomic():
        locked_plan = (
            TaskFinancialPlan.objects.select_for_update()
            .select_related("task")
            .prefetch_related("milestones__transactions")
            .get(pk=plan.pk)
        )
        if locked_plan.status != TaskFinancialPlan.STATUS_ACTIVE:
            raise ValidationError({"financial_plan": "Only active financial plans can receive allocated payments."})

        milestones = list(
            PaymentMilestone.objects.select_for_update()
            .filter(financial_plan=locked_plan)
            .prefetch_related("transactions")
            .order_by("sequence", "id")
        )
        total_outstanding = money(sum((milestone_outstanding(item) for item in milestones), Decimal("0.00")))
        if amount > total_outstanding:
            raise ValidationError({"amount": "Payment amount exceeds outstanding financial plan amount."})

        remaining = amount
        created = []
        for milestone in milestones:
            if remaining <= 0:
                break
            outstanding = milestone_outstanding(milestone)
            if outstanding <= 0:
                continue
            line_amount = min(remaining, outstanding)
            tx = PaymentTransaction(
                milestone=milestone,
                transaction_type=PaymentTransaction.TYPE_PAYMENT,
                amount=money(line_amount),
                transaction_date=transaction_date,
                reference_number=reference_number or "",
                description=description or "",
                created_by=user,
            )
            tx.full_clean()
            tx.save()
            created.append(tx)
            remaining = money(remaining - line_amount)
            refresh_milestone_status(milestone)

        evaluate_financial_plan(locked_plan.task)
        return created
