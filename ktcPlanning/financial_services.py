from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, time

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import (
    CostTransaction, CostTransactionMilestoneAllocation, PaymentMilestone,
    PaymentTransaction, TaskDelivery, TaskFinancialPlan, TaskReportLog, TaskRole,
)
from .permissions import can_edit_project, is_company_level

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
        TaskReportLog.objects.filter(
            Q(approval_status__in=["reviewer_approved", "final_approved"]) | Q(is_approved=True),
            task=task,
        )
        .order_by("-timestamp", "-id")
        .first()
    )
    if report:
        return progress(report.progress_percent)

    version = task.versions.filter(revision_id=task.project.current_execution_revision_id, is_deleted=False).select_related("actual").first()
    actual = getattr(version, "actual", None) if version else None
    return progress(actual.progress if actual else 0)


def get_approved_progress_as_of(task, as_of_date=None):
    queryset = TaskReportLog.objects.filter(
        Q(approval_status__in=["reviewer_approved", "final_approved"]) | Q(is_approved=True),
        task=task,
    )
    if as_of_date:
        cutoff = as_of_date
        if not hasattr(cutoff, "hour"):
            cutoff = timezone.make_aware(
                datetime.combine(cutoff, time.max),
                timezone.get_current_timezone(),
            )
        elif timezone.is_naive(cutoff):
            cutoff = timezone.make_aware(cutoff, timezone.get_current_timezone())
        queryset = queryset.filter(timestamp__lte=cutoff)
    report = queryset.order_by("-timestamp", "-id").first()
    if report:
        return progress(report.progress_percent)
    if as_of_date is None:
        return get_approved_progress(task)
    return Decimal("0.00")


def _normalize_cutoff(as_of_date):
    if not as_of_date:
        return None
    cutoff = as_of_date
    if not hasattr(cutoff, "hour"):
        cutoff = timezone.make_aware(
            datetime.combine(cutoff, time.max),
            timezone.get_current_timezone(),
        )
    elif timezone.is_naive(cutoff):
        cutoff = timezone.make_aware(cutoff, timezone.get_current_timezone())
    return cutoff


def get_latest_task_delivery(task, *, as_of_date=None):
    queryset = TaskDelivery.objects.filter(task=task)
    cutoff = _normalize_cutoff(as_of_date)
    if cutoff:
        queryset = queryset.filter(created_at__lte=cutoff)
    return queryset.order_by("-created_at", "-id").first()


def get_effective_task_delivery(task, *, as_of_date=None):
    queryset = TaskDelivery.objects.filter(task=task, status=TaskDelivery.STATUS_APPROVED)
    cutoff = _normalize_cutoff(as_of_date)
    if cutoff:
        queryset = queryset.filter(approved_at__lte=cutoff)
    return queryset.order_by("-approved_at", "-id").first()


def _delivery_eligibility_payload(delivery, *, eligible, reason):
    return {
        "eligible": eligible,
        "reason": reason,
        "current_value": delivery.status if delivery else None,
        "required_value": TaskDelivery.STATUS_APPROVED,
        "delivery_id": str(delivery.id) if delivery else None,
        "delivery_status": delivery.status if delivery else None,
        "delivery_reference": delivery.delivery_reference if delivery else "",
        "submitted_at": delivery.submitted_at.isoformat() if delivery and delivery.submitted_at else None,
        "approved_at": delivery.approved_at.isoformat() if delivery and delivery.approved_at else None,
    }


def evaluate_before_delivery_eligibility(milestone, *, as_of_date=None):
    task = milestone.financial_plan.task
    approved = get_effective_task_delivery(task, as_of_date=as_of_date)
    if approved:
        return _delivery_eligibility_payload(approved, eligible=True, reason="delivery_approved")
    latest = get_latest_task_delivery(task, as_of_date=as_of_date)
    if not latest or latest.status == TaskDelivery.STATUS_DRAFT:
        return _delivery_eligibility_payload(latest, eligible=False, reason="delivery_not_submitted")
    if latest.status == TaskDelivery.STATUS_SUBMITTED:
        return _delivery_eligibility_payload(latest, eligible=False, reason="delivery_pending_approval")
    if latest.status == TaskDelivery.STATUS_REJECTED:
        return _delivery_eligibility_payload(latest, eligible=False, reason="delivery_rejected")
    if latest.status == TaskDelivery.STATUS_CANCELLED:
        return _delivery_eligibility_payload(latest, eligible=False, reason="delivery_cancelled")
    return _delivery_eligibility_payload(latest, eligible=False, reason="delivery_not_submitted")


def _refresh_payable_plan_allocations_for_task(task):
    summaries = []
    plans = TaskFinancialPlan.objects.filter(
        task=task,
        direction=TaskFinancialPlan.DIRECTION_PAYABLE,
    ).exclude(status=TaskFinancialPlan.STATUS_CANCELLED)
    for plan in plans:
        summaries.append(refresh_plan_cost_allocations(plan))
    return summaries


def can_review_task_delivery(delivery, user):
    if not user or not user.is_authenticated:
        return False
    if is_company_level(user):
        return True
    annotated = getattr(delivery, "_can_review_user", None)
    if annotated is not None:
        return bool(annotated)
    return TaskRole.objects.filter(
        task=delivery.task,
        user=user,
        role__in=["reviewer", "project manager"],
    ).exists()


def get_task_delivery_capabilities(delivery, user):
    empty = {
        "can_edit": False,
        "can_submit": False,
        "can_approve": False,
        "can_reject": False,
        "can_cancel": False,
    }
    if not user or not user.is_authenticated:
        return empty

    editable = can_edit_project(user, delivery.project)
    submitter = delivery.created_by_id == user.id or editable
    reviewer = can_review_task_delivery(delivery, user)
    delivery_status = delivery.status
    return {
        "can_edit": delivery_status == TaskDelivery.STATUS_DRAFT and editable,
        "can_submit": delivery_status in {TaskDelivery.STATUS_DRAFT, TaskDelivery.STATUS_REJECTED} and submitter,
        "can_approve": delivery_status == TaskDelivery.STATUS_SUBMITTED and reviewer,
        "can_reject": delivery_status == TaskDelivery.STATUS_SUBMITTED and reviewer,
        "can_cancel": delivery_status in {
            TaskDelivery.STATUS_DRAFT,
            TaskDelivery.STATUS_SUBMITTED,
            TaskDelivery.STATUS_REJECTED,
        } and submitter,
    }


def get_task_delivery_attachment_capabilities(delivery, user):
    empty = {
        "can_upload": False,
        "can_delete": False,
    }
    if not user or not user.is_authenticated:
        return empty
    editable_status = delivery.status in {TaskDelivery.STATUS_DRAFT, TaskDelivery.STATUS_REJECTED}
    delivery_capabilities = get_task_delivery_capabilities(delivery, user)
    can_edit_attachment = editable_status and can_edit_project(user, delivery.project)
    return {
        "can_upload": editable_status and (delivery_capabilities["can_edit"] or delivery_capabilities["can_submit"]),
        "can_delete": can_edit_attachment,
    }


def get_task_delivery_create_capabilities(task, user):
    return {
        "can_create": bool(user and user.is_authenticated and can_edit_project(user, task.project)),
    }


def submit_task_delivery(delivery, *, user):
    with transaction.atomic():
        locked = TaskDelivery.objects.select_for_update().select_related("task", "project").get(pk=delivery.pk)
        if locked.status not in {TaskDelivery.STATUS_DRAFT, TaskDelivery.STATUS_REJECTED}:
            raise ValidationError({"status": "Only draft or rejected deliveries can be submitted."})
        locked.status = TaskDelivery.STATUS_SUBMITTED
        locked.submitted_at = timezone.now()
        locked.submitted_by = user
        locked.approved_at = None
        locked.approved_by = None
        locked.rejected_at = None
        locked.rejected_by = None
        locked.rejection_reason = ""
        locked.full_clean()
        locked.save(update_fields=[
            "status", "submitted_at", "submitted_by", "approved_at", "approved_by",
            "rejected_at", "rejected_by", "rejection_reason", "updated_at",
        ])
        return locked


def approve_task_delivery(delivery, *, user):
    with transaction.atomic():
        locked = TaskDelivery.objects.select_for_update().select_related("task", "project").get(pk=delivery.pk)
        if locked.status == TaskDelivery.STATUS_APPROVED:
            summaries = _refresh_payable_plan_allocations_for_task(locked.task)
            return locked, summaries
        if locked.status != TaskDelivery.STATUS_SUBMITTED:
            raise ValidationError({"status": "Only submitted deliveries can be approved."})
        locked.status = TaskDelivery.STATUS_APPROVED
        locked.approved_at = timezone.now()
        locked.approved_by = user
        locked.rejected_at = None
        locked.rejected_by = None
        locked.rejection_reason = ""
        locked.full_clean()
        locked.save(update_fields=[
            "status", "approved_at", "approved_by", "rejected_at", "rejected_by",
            "rejection_reason", "updated_at",
        ])
        summaries = _refresh_payable_plan_allocations_for_task(locked.task)
        return locked, summaries


def reject_task_delivery(delivery, *, user, reason):
    clean_reason = (reason or "").strip()
    if not clean_reason:
        raise ValidationError({"rejection_reason": "Rejection reason is required."})
    with transaction.atomic():
        locked = TaskDelivery.objects.select_for_update().select_related("task", "project").get(pk=delivery.pk)
        if locked.status != TaskDelivery.STATUS_SUBMITTED:
            raise ValidationError({"status": "Only submitted deliveries can be rejected."})
        locked.status = TaskDelivery.STATUS_REJECTED
        locked.rejected_at = timezone.now()
        locked.rejected_by = user
        locked.rejection_reason = clean_reason
        locked.full_clean()
        locked.save(update_fields=["status", "rejected_at", "rejected_by", "rejection_reason", "updated_at"])
        return locked


def cancel_task_delivery(delivery, *, user):
    with transaction.atomic():
        locked = TaskDelivery.objects.select_for_update().select_related("task", "project").get(pk=delivery.pk)
        if locked.status in {TaskDelivery.STATUS_APPROVED, TaskDelivery.STATUS_CANCELLED}:
            raise ValidationError({"status": "Approved or cancelled deliveries cannot be cancelled."})
        locked.status = TaskDelivery.STATUS_CANCELLED
        locked.cancelled_at = timezone.now()
        locked.cancelled_by = user
        locked.full_clean()
        locked.save(update_fields=["status", "cancelled_at", "cancelled_by", "updated_at"])
        return locked


def milestone_amount(milestone):
    if milestone.amount_type == PaymentMilestone.AMOUNT_FIXED:
        return money(milestone.fixed_amount)
    raw = Decimal(milestone.financial_plan.contract_amount) * Decimal(milestone.percentage or 0) / Decimal("100")
    return money(raw)


def milestone_cost_allocated_amount(milestone):
    if hasattr(milestone, "_cost_allocated_amount"):
        return money(milestone._cost_allocated_amount)
    if hasattr(milestone, "_prefetched_objects_cache") and "cost_allocations" in milestone._prefetched_objects_cache:
        return money(sum((item.allocated_amount for item in milestone.cost_allocations.all()), Decimal("0.00")))
    total = milestone.cost_allocations.aggregate(total=Sum("allocated_amount"))["total"] or Decimal("0.00")
    return money(total)


def milestone_cost_remaining_capacity(milestone):
    return money(milestone_amount(milestone) - milestone_cost_allocated_amount(milestone))


def cost_transaction_allocated_amount(cost_transaction):
    if hasattr(cost_transaction, "_allocated_amount"):
        return money(cost_transaction._allocated_amount)
    if hasattr(cost_transaction, "_prefetched_objects_cache") and "milestone_allocations" in cost_transaction._prefetched_objects_cache:
        return money(sum((item.allocated_amount for item in cost_transaction.milestone_allocations.all()), Decimal("0.00")))
    total = cost_transaction.milestone_allocations.aggregate(total=Sum("allocated_amount"))["total"] or Decimal("0.00")
    return money(total)


def cost_transaction_unallocated_amount(cost_transaction):
    return money(cost_transaction.amount - cost_transaction_allocated_amount(cost_transaction))


def recognized_cost_summary(plan):
    if hasattr(plan, "_prefetched_objects_cache") and "cost_transactions" in plan._prefetched_objects_cache:
        transactions = list(plan.cost_transactions.all())
        recognized = sum((item.amount for item in transactions), Decimal("0.00"))
        allocated = sum((cost_transaction_allocated_amount(item) for item in transactions), Decimal("0.00"))
        count = len(transactions)
    else:
        recognized = plan.cost_transactions.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
        allocated = CostTransactionMilestoneAllocation.objects.filter(
            cost_transaction__financial_plan=plan
        ).aggregate(total=Sum("allocated_amount"))["total"] or Decimal("0.00")
        count = plan.cost_transactions.count()
    return {
        "linked_cost_transaction_count": count,
        "recognized_cost_amount": as_str(recognized),
        "allocated_recognized_cost_amount": as_str(allocated),
        "unallocated_recognized_cost_amount": as_str(money(recognized - allocated)),
    }


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


def cost_allocations_by_milestone(plan, milestones=None):
    milestones = list(milestones if milestones is not None else plan.milestones.all().order_by("sequence", "id"))
    milestone_ids = [milestone.id for milestone in milestones]
    totals = CostTransactionMilestoneAllocation.objects.filter(
        milestone_id__in=milestone_ids,
    ).values("milestone_id").annotate(total=Sum("allocated_amount"))
    allocations = {milestone.id: Decimal("0.00") for milestone in milestones}
    for item in totals:
        allocations[item["milestone_id"]] = money(item["total"])
    return allocations


def milestone_incurred_amount(milestone):
    return milestone_cost_allocated_amount(milestone)


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


def evaluate_milestone_eligibility(milestone, *, as_of_date=None):
    today = as_of_date or timezone.localdate()
    if hasattr(today, "date"):
        today = today.date()
    trigger_type = milestone.trigger_type
    if milestone.status == PaymentMilestone.STATUS_CANCELLED:
        return {
            "eligible": False,
            "reason": "milestone_cancelled",
            "current_value": milestone.status,
            "required_value": PaymentMilestone.STATUS_CANCELLED,
        }
    if trigger_type == PaymentMilestone.TRIGGER_BEFORE_START:
        has_allocation = milestone.cost_allocations.exists()
        version = milestone.financial_plan.task.versions.filter(
            revision_id=milestone.financial_plan.task.project.current_execution_revision_id,
            is_deleted=False,
        ).select_related("actual").first()
        actual = getattr(version, "actual", None) if version else None
        eligible = has_allocation or not (actual and actual.actual_start)
        return {
            "eligible": eligible,
            "reason": "before_start_available" if eligible else "task_already_started",
            "current_value": bool(actual and actual.actual_start),
            "required_value": False,
        }
    if trigger_type == PaymentMilestone.TRIGGER_BEFORE_DELIVERY:
        return evaluate_before_delivery_eligibility(milestone, as_of_date=as_of_date)
    if trigger_type == PaymentMilestone.TRIGGER_MANUAL:
        return {
            "eligible": bool(milestone.manual_approved),
            "reason": "manual_approved" if milestone.manual_approved else "manual_approval_required",
            "current_value": bool(milestone.manual_approved),
            "required_value": True,
        }
    if trigger_type == PaymentMilestone.TRIGGER_APPROVED_PROGRESS:
        approved = get_approved_progress_as_of(milestone.financial_plan.task, as_of_date=as_of_date)
        required = progress(milestone.progress_threshold)
        return {
            "eligible": approved >= required,
            "reason": "approved_progress_reached" if approved >= required else "approved_progress_below_threshold",
            "current_value": str(approved),
            "required_value": str(required),
        }
    if trigger_type == PaymentMilestone.TRIGGER_TASK_COMPLETION:
        approved = get_approved_progress_as_of(milestone.financial_plan.task, as_of_date=as_of_date)
        completed = approved >= Decimal("100.00")
        return {
            "eligible": completed,
            "reason": "task_completed" if completed else "task_not_completed",
            "current_value": str(approved),
            "required_value": "100.00",
        }
    if trigger_type == PaymentMilestone.TRIGGER_FIXED_DATE:
        eligible = bool(milestone.due_date and milestone.due_date <= today)
        return {
            "eligible": eligible,
            "reason": "fixed_date_reached" if eligible else "fixed_date_not_reached",
            "current_value": today.isoformat(),
            "required_value": milestone.due_date.isoformat() if milestone.due_date else None,
        }
    return {"eligible": False, "reason": "unsupported_trigger", "current_value": trigger_type, "required_value": None}


def _allocation_payload(allocation):
    milestone = allocation.milestone
    return {
        "id": allocation.id,
        "milestone_id": milestone.id,
        "milestone_sequence": milestone.sequence,
        "milestone_title": milestone.title,
        "allocated_amount": as_str(allocation.allocated_amount),
        "milestone_capacity": as_str(milestone_amount(milestone)),
        "milestone_total_allocated": as_str(milestone_cost_allocated_amount(milestone)),
    }


def allocate_cost_transaction_to_milestones(cost_transaction, *, as_of_date=None):
    with transaction.atomic():
        locked_transaction = (
            CostTransaction.objects.select_for_update()
            .select_related("project")
            .get(pk=cost_transaction.pk)
        )
        plan = (
            TaskFinancialPlan.objects.select_related("task").get(pk=locked_transaction.financial_plan_id)
            if locked_transaction.financial_plan_id
            else None
        )
        if not plan:
            return {
                "allocated_amount": "0.00",
                "unallocated_amount": as_str(locked_transaction.amount),
                "allocations": [],
                "stopped_at_milestone": None,
                "stop_reason": "no_financial_plan",
            }
        if plan.direction != TaskFinancialPlan.DIRECTION_PAYABLE:
            raise ValidationError({"financial_plan": "Only payable plans can receive cost recognition allocations."})
        if plan.status == TaskFinancialPlan.STATUS_CANCELLED:
            raise ValidationError({"financial_plan": "Cancelled plans cannot receive cost recognition allocations."})
        if not locked_transaction.task_id or plan.task_id != locked_transaction.task_id:
            raise ValidationError({"financial_plan": "Financial plan must belong to the cost transaction task."})
        if plan.task.project_id != locked_transaction.project_id:
            raise ValidationError({"financial_plan": "Financial plan must belong to the cost transaction project."})

        CostTransactionMilestoneAllocation.objects.select_for_update().filter(cost_transaction=locked_transaction).exists()
        existing_total = cost_transaction_allocated_amount(locked_transaction)
        remaining = money(locked_transaction.amount - existing_total)
        created_or_updated = []
        stopped_at = None
        stop_reason = None

        if remaining <= 0:
            return {
                "allocated_amount": as_str(existing_total),
                "unallocated_amount": "0.00",
                "allocations": [_allocation_payload(item) for item in locked_transaction.milestone_allocations.select_related("milestone").order_by("milestone__sequence", "milestone_id")],
                "stopped_at_milestone": None,
                "stop_reason": "fully_allocated",
            }

        milestones = list(
            PaymentMilestone.objects.select_for_update()
            .filter(financial_plan=plan)
            .order_by("sequence", "id")
        )
        CostTransactionMilestoneAllocation.objects.select_for_update().filter(milestone__in=milestones).exists()

        for milestone in milestones:
            eligibility = evaluate_milestone_eligibility(milestone, as_of_date=as_of_date)
            if not eligibility["eligible"]:
                stopped_at = milestone.id
                stop_reason = eligibility["reason"]
                break
            capacity_remaining = milestone_cost_remaining_capacity(milestone)
            if capacity_remaining <= 0:
                continue
            line_amount = money(min(remaining, capacity_remaining))
            if line_amount <= 0:
                continue
            allocation, created = CostTransactionMilestoneAllocation.objects.get_or_create(
                cost_transaction=locked_transaction,
                milestone=milestone,
                defaults={"allocated_amount": line_amount},
            )
            if not created:
                allocation.allocated_amount = money(allocation.allocated_amount + line_amount)
                allocation.full_clean()
                allocation.save(update_fields=["allocated_amount", "updated_at"])
            created_or_updated.append(allocation)
            remaining = money(remaining - line_amount)
            if remaining <= 0:
                stop_reason = "fully_allocated"
                break

        total_allocated = cost_transaction_allocated_amount(locked_transaction)
        return {
            "allocated_amount": as_str(total_allocated),
            "unallocated_amount": as_str(locked_transaction.amount - total_allocated),
            "allocations": [_allocation_payload(item) for item in locked_transaction.milestone_allocations.select_related("milestone").order_by("milestone__sequence", "milestone_id")],
            "stopped_at_milestone": stopped_at,
            "stop_reason": stop_reason,
        }


def refresh_plan_cost_allocations(plan, *, as_of_date=None):
    with transaction.atomic():
        locked_plan = (
            TaskFinancialPlan.objects.select_for_update()
            .select_related("task", "task__project")
            .get(pk=plan.pk)
        )
        if locked_plan.direction != TaskFinancialPlan.DIRECTION_PAYABLE:
            raise ValidationError({"financial_plan": "Only payable plans can refresh cost recognition allocations."})
        if locked_plan.status == TaskFinancialPlan.STATUS_CANCELLED:
            raise ValidationError({"financial_plan": "Cancelled plans cannot refresh cost recognition allocations."})
        PaymentMilestone.objects.select_for_update().filter(financial_plan=locked_plan).order_by("sequence", "id")
        results = []
        for cost_transaction in CostTransaction.objects.select_for_update().filter(
            financial_plan=locked_plan
        ).order_by("transaction_date", "id"):
            results.append(allocate_cost_transaction_to_milestones(cost_transaction, as_of_date=as_of_date))
        summary = recognized_cost_summary(locked_plan)
        summary["transactions"] = results
        return summary


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


def get_task_financial_status(task, user=None):
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
            "delivery_capabilities": get_task_delivery_create_capabilities(task, user) if user is not None else {"can_create": False},
        }

    milestones = list(plan.milestones.all())
    cost_allocations = cost_allocations_by_milestone(plan, milestones)
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
            "cost_allocated_amount": as_str(incurred),
            "cost_remaining_capacity": as_str(cost_outstanding),
            "cost_allocation_status": "full" if cost_outstanding <= 0 else "partial" if incurred > 0 else "empty",
            "eligible_for_cost_allocation": evaluate_milestone_eligibility(milestone, as_of_date=today)["eligible"],
            "eligibility_reason": evaluate_milestone_eligibility(milestone, as_of_date=today)["reason"],
            "eligibility_detail": evaluate_milestone_eligibility(milestone, as_of_date=today),
            "manual_approved": milestone.manual_approved,
            "manual_approved_at": milestone.manual_approved_at.isoformat() if milestone.manual_approved_at else None,
            "manual_approved_by": milestone.manual_approved_by_id,
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

    payload = {
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
        "delivery_capabilities": get_task_delivery_create_capabilities(task, user) if user is not None else {"can_create": False},
    }
    payload.update(recognized_cost_summary(plan))
    return payload


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


def register_transaction(milestone, transaction_type, amount, transaction_date, user=None, reference_number="", description="", currency=None):
    with transaction.atomic():
        locked = PaymentMilestone.objects.select_for_update().select_related("financial_plan__task").get(pk=milestone.pk)
        PaymentTransaction.objects.select_for_update().filter(milestone=locked).exists()
        tx = PaymentTransaction(
            milestone=locked,
            transaction_type=transaction_type,
            amount=money(amount),
            currency=str(currency).upper() if currency else None,
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

def allocate_plan_payment(plan, amount, transaction_date, user=None, reference_number="", description="", currency=None):
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
                currency=str(currency).upper() if currency else None,
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
