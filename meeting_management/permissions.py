from django.db.models import Q
from rest_framework.permissions import BasePermission

from .models import Meeting, ResolutionAction


def is_company_admin(user):
    return bool(user and user.is_authenticated and getattr(user, "org_role", "") == "company_admin")


def managed_unit_ids(user):
    if not user or not user.is_authenticated:
        return []
    return list(user.managed_units.values_list("id", flat=True))


def visible_meeting_q(user):
    if is_company_admin(user):
        return Q()
    if not user or not user.is_authenticated:
        return Q(pk__isnull=True)
    unit_ids = managed_unit_ids(user)
    q = (
        Q(organizer=user)
        | Q(chairperson=user)
        | Q(secretary=user)
        | Q(participants__user=user)
        | Q(unit_invitations__unit_id__in=unit_ids)
        | Q(owning_unit_id__in=unit_ids)
    )
    return q


def visible_action_q(user):
    if is_company_admin(user):
        return Q()
    if not user or not user.is_authenticated:
        return Q(pk__isnull=True)
    unit_ids = managed_unit_ids(user)
    return (
        Q(accountable_user=user)
        | Q(responsible_user=user)
        | Q(reviewer=user)
        | Q(approver=user)
        | Q(accountable_unit_id__in=unit_ids)
        | Q(resolution__meeting__organizer=user)
        | Q(resolution__meeting__chairperson=user)
        | Q(resolution__meeting__secretary=user)
        | Q(resolution__meeting__participants__user=user)
    )


def filter_visible_meetings(queryset, user):
    return queryset.filter(visible_meeting_q(user)).distinct()


def filter_visible_actions(queryset, user):
    return queryset.filter(visible_action_q(user)).distinct()


class MeetingObjectPermission(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        if is_company_admin(request.user):
            return True
        if isinstance(obj, Meeting):
            return filter_visible_meetings(Meeting.objects.filter(pk=obj.pk), request.user).exists()
        if isinstance(obj, ResolutionAction):
            return filter_visible_actions(ResolutionAction.objects.filter(pk=obj.pk), request.user).exists()
        meeting = getattr(obj, "meeting", None)
        if meeting is not None:
            return self.has_object_permission(request, view, meeting)
        action = getattr(obj, "action", None)
        if action is not None:
            return self.has_object_permission(request, view, action)
        resolution = getattr(obj, "resolution", None)
        if resolution is not None:
            return self.has_object_permission(request, view, resolution.meeting)
        return False
