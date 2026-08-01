from datetime import timedelta

from django.db.models import Count, F, Q
from django.utils import timezone

from .models import ActionCompletionSubmission, ActionProgressReport, ActionStatus, Resolution, ResolutionAction
from .permissions import filter_visible_actions


def individual_dashboard(user):
    today = timezone.localdate()
    actions = filter_visible_actions(ResolutionAction.objects.all(), user)
    own_actions = actions.filter(Q(responsible_user=user) | Q(accountable_user=user)).distinct()
    closed = own_actions.filter(status=ActionStatus.CLOSED)
    accepted = ActionCompletionSubmission.objects.filter(action__in=own_actions, status="accepted")
    report_count = ActionProgressReport.objects.filter(action__in=own_actions).count()
    return {
        "active_commitments": own_actions.exclude(status__in=[ActionStatus.CLOSED, ActionStatus.CANCELLED]).count(),
        "upcoming_deadlines": own_actions.filter(current_due_date__gte=today, current_due_date__lte=today + timedelta(days=7)).count(),
        "overdue_actions": own_actions.exclude(status__in=[ActionStatus.CLOSED, ActionStatus.CANCELLED]).filter(current_due_date__lt=today).count(),
        "waiting_for_review": own_actions.filter(status=ActionStatus.SUBMITTED_FOR_REVIEW).count(),
        "accepted_on_time_completion_rate": _rate(closed.filter(closed_at__date__lte=F("current_due_date")).count(), closed.count()),
        "average_delay": 0,
        "reporting_compliance": _rate(report_count, max(own_actions.count(), 1)),
        "rework_rate": _rate(accepted.filter(review_comment__icontains="revision").count(), max(accepted.count(), 1)),
        "blocked_action_ratio": _rate(own_actions.filter(status=ActionStatus.BLOCKED).count(), max(own_actions.count(), 1)),
    }


def unit_dashboard(user, unit_id=None):
    today = timezone.localdate()
    actions = filter_visible_actions(ResolutionAction.objects.all(), user)
    if unit_id:
        actions = actions.filter(accountable_unit_id=unit_id)
    return {
        "active_commitments": actions.exclude(status__in=[ActionStatus.CLOSED, ActionStatus.CANCELLED]).count(),
        "overdue_commitments": actions.exclude(status__in=[ActionStatus.CLOSED, ActionStatus.CANCELLED]).filter(current_due_date__lt=today).count(),
        "unassigned_actions": actions.filter(Q(accountable_user__isnull=True) | Q(responsible_user__isnull=True)).count(),
        "blocked_actions": actions.filter(status=ActionStatus.BLOCKED).count(),
        "member_workload": list(actions.values("responsible_user__username").annotate(count=Count("id")).order_by("responsible_user__username")),
        "incoming_dependencies": 0,
        "outgoing_dependencies": 0,
    }


def executive_dashboard(user):
    actions = filter_visible_actions(ResolutionAction.objects.select_related("resolution"), user)
    resolutions = Resolution.objects.filter(actions__in=actions).distinct()
    today = timezone.localdate()
    return {
        "active_resolutions": resolutions.exclude(status__in=["closed", "cancelled"]).count(),
        "critical_overdue_resolutions": resolutions.filter(actions__priority="critical", actions__current_due_date__lt=today).distinct().count(),
        "approval_backlog": actions.filter(status=ActionStatus.SUBMITTED_FOR_REVIEW).count(),
        "resolutions_without_actions": resolutions.annotate(action_count=Count("actions")).filter(action_count=0).count(),
        "meetings_with_no_executable_output": 0,
        "units_with_highest_overdue_exposure": list(
            actions.filter(current_due_date__lt=today)
            .values("accountable_unit__name")
            .annotate(count=Count("id"))
            .order_by("-count")[:10]
        ),
    }


def _rate(numerator, denominator):
    if not denominator:
        return 0
    return round((numerator / denominator) * 100, 2)
