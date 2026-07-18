from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from auditlog.services import log_event, model_to_dict_safe

from .exceptions import EngineeringLifecycleError, EngineeringPermissionError
from .models import Item, ItemRevision
from .permissions import can_manage_engineering, is_engineering_releaser


TRANSITIONS = {
    ItemRevision.STATUS_DRAFT: {ItemRevision.STATUS_UNDER_REVIEW},
    ItemRevision.STATUS_UNDER_REVIEW: {ItemRevision.STATUS_DRAFT, ItemRevision.STATUS_APPROVED},
    ItemRevision.STATUS_APPROVED: {ItemRevision.STATUS_UNDER_REVIEW, ItemRevision.STATUS_RELEASED},
    ItemRevision.STATUS_RELEASED: {ItemRevision.STATUS_SUPERSEDED, ItemRevision.STATUS_OBSOLETE},
    ItemRevision.STATUS_SUPERSEDED: {ItemRevision.STATUS_OBSOLETE},
    ItemRevision.STATUS_OBSOLETE: set(),
}


def normalize_code(value: str | None) -> str:
    return (value or '').strip()


def _require_manager(actor):
    if not can_manage_engineering(actor):
        raise EngineeringPermissionError('You do not have permission to manage engineering revisions.')


def _require_releaser(actor):
    if not is_engineering_releaser(actor):
        raise EngineeringPermissionError('You do not have permission to release engineering revisions.')


def _validate_transition(revision: ItemRevision, target_status: str):
    allowed = TRANSITIONS.get(revision.status, set())
    if target_status not in allowed:
        raise EngineeringLifecycleError({
            'status': f'Cannot transition item revision from {revision.status} to {target_status}.'
        })


def _validate_item_ready(item: Item):
    errors = {}
    if not item.is_active:
        errors['item'] = 'Parent item must be active.'
    if item.status in {'OBSOLETE', 'BLOCKED'}:
        errors['status'] = 'Parent item status does not allow release.'
    for field in ('item_code', 'name', 'base_unit', 'make_or_buy', 'tracking_mode'):
        if not getattr(item, field, None):
            errors[field] = 'This item field is required before release.'
    if errors:
        raise EngineeringLifecycleError(errors)


def _validate_revision_ready(revision: ItemRevision):
    errors = {}
    if not normalize_code(revision.revision):
        errors['revision'] = 'Revision code is required.'
    if not revision.effective_from:
        errors['effective_from'] = 'Effective start date is required before release.'
    if revision.effective_from and revision.effective_to and revision.effective_to < revision.effective_from:
        errors['effective_to'] = 'Effective end date cannot precede effective start date.'
    if errors:
        raise EngineeringLifecycleError(errors)


def _periods_overlap(a_start: date, a_end: date | None, b_start: date, b_end: date | None) -> bool:
    a_stop = a_end or date.max
    b_stop = b_end or date.max
    return a_start <= b_stop and b_start <= a_stop


def _validate_no_released_overlap(revision: ItemRevision, *, exclude_ids: Iterable[str] = ()): 
    if not revision.effective_from:
        return
    excluded = {str(value) for value in exclude_ids}
    released = ItemRevision.objects.filter(
        item=revision.item,
        status=ItemRevision.STATUS_RELEASED,
    ).exclude(pk=revision.pk)
    if excluded:
        released = released.exclude(pk__in=excluded)
    for existing in released:
        if existing.effective_from and _periods_overlap(
            revision.effective_from, revision.effective_to, existing.effective_from, existing.effective_to
        ):
            raise EngineeringLifecycleError({
                'effective_from': 'Released item revisions cannot have overlapping effective periods.'
            })


def _save_status(revision: ItemRevision, *, new_status: str, actor, update_fields: list[str] | None = None):
    old_status = revision.status
    revision.status = new_status
    if update_fields is None:
        update_fields = ['status', 'updated_at']
    else:
        update_fields = list(dict.fromkeys([*update_fields, 'status', 'updated_at']))
    revision.save(update_fields=update_fields)
    log_event(
        f'item_revision_{new_status.lower()}',
        target=revision,
        category='business',
        changes={'status': {'old': old_status, 'new': new_status}},
        extra={
            'item_id': str(revision.item_id),
            'item_code': revision.item.item_code,
            'revision': revision.revision,
            'actor_id': getattr(actor, 'id', None),
            'effective_from': str(revision.effective_from) if revision.effective_from else None,
            'effective_to': str(revision.effective_to) if revision.effective_to else None,
        },
    )
    return revision


@transaction.atomic
def submit_revision_for_review(revision: ItemRevision, *, actor):
    _require_manager(actor)
    revision = ItemRevision.objects.select_for_update().select_related('item').get(pk=revision.pk)
    _validate_transition(revision, ItemRevision.STATUS_UNDER_REVIEW)
    revision.submitted_by = actor
    revision.submitted_at = timezone.now()
    return _save_status(revision, new_status=ItemRevision.STATUS_UNDER_REVIEW, actor=actor, update_fields=['submitted_by', 'submitted_at'])


@transaction.atomic
def return_revision_to_draft(revision: ItemRevision, *, actor):
    _require_manager(actor)
    revision = ItemRevision.objects.select_for_update().select_related('item').get(pk=revision.pk)
    _validate_transition(revision, ItemRevision.STATUS_DRAFT)
    return _save_status(revision, new_status=ItemRevision.STATUS_DRAFT, actor=actor)


@transaction.atomic
def return_revision_to_review(revision: ItemRevision, *, actor):
    _require_manager(actor)
    revision = ItemRevision.objects.select_for_update().select_related('item').get(pk=revision.pk)
    _validate_transition(revision, ItemRevision.STATUS_UNDER_REVIEW)
    return _save_status(revision, new_status=ItemRevision.STATUS_UNDER_REVIEW, actor=actor)


@transaction.atomic
def approve_revision(revision: ItemRevision, *, actor):
    _require_releaser(actor)
    revision = ItemRevision.objects.select_for_update().select_related('item').get(pk=revision.pk)
    _validate_transition(revision, ItemRevision.STATUS_APPROVED)
    revision.approved_by = actor
    revision.approved_at = timezone.now()
    return _save_status(revision, new_status=ItemRevision.STATUS_APPROVED, actor=actor, update_fields=['approved_by', 'approved_at'])


@transaction.atomic
def release_revision(revision: ItemRevision, *, actor, effective_from=None, supersede_current=False):
    _require_releaser(actor)
    revision = ItemRevision.objects.select_for_update().select_related('item').get(pk=revision.pk)
    item = Item.objects.select_for_update().get(pk=revision.item_id)
    revision.item = item
    _validate_transition(revision, ItemRevision.STATUS_RELEASED)
    _validate_item_ready(item)
    if effective_from is not None:
        revision.effective_from = effective_from
    _validate_revision_ready(revision)

    current_released = ItemRevision.objects.select_for_update().filter(
        item=item,
        status=ItemRevision.STATUS_RELEASED,
        effective_to__isnull=True,
    ).exclude(pk=revision.pk).order_by('-released_at', '-created_at').first()

    if current_released and not supersede_current:
        raise EngineeringLifecycleError({
            'supersede': 'A currently released revision exists. Use the explicit supersede release action.'
        })

    exclude_overlap = [current_released.pk] if current_released else []
    _validate_no_released_overlap(revision, exclude_ids=exclude_overlap)

    old_snapshot = model_to_dict_safe(revision)
    now = timezone.now()
    if current_released:
        if revision.effective_from and (not current_released.effective_from or revision.effective_from <= current_released.effective_from):
            raise EngineeringLifecycleError({
                'effective_from': 'Superseding revision must start after the current released revision starts.'
            })
        current_released.status = ItemRevision.STATUS_SUPERSEDED
        current_released.effective_to = revision.effective_from - timedelta(days=1)
        current_released.superseded_at = now
        current_released.superseded_by = revision
        current_released.save(update_fields=['status', 'effective_to', 'superseded_at', 'superseded_by', 'updated_at'])
        log_event(
            'item_revision_superseded',
            target=current_released,
            category='business',
            changes={'status': {'old': ItemRevision.STATUS_RELEASED, 'new': ItemRevision.STATUS_SUPERSEDED}},
            extra={'superseded_by': str(revision.pk), 'effective_to': str(current_released.effective_to)},
        )

    revision.status = ItemRevision.STATUS_RELEASED
    revision.released_by = actor
    revision.released_at = now
    revision.is_active = True
    revision.save(update_fields=['status', 'released_by', 'released_at', 'effective_from', 'is_active', 'updated_at'])
    item.default_revision = revision.revision
    if item.status == 'DRAFT':
        item.status = 'ACTIVE'
    item.is_active = True
    item.save(update_fields=['default_revision', 'status', 'is_active', 'updated_at'])
    log_event(
        'item_revision_released',
        target=revision,
        category='business',
        changes={'status': {'old': old_snapshot.get('status'), 'new': ItemRevision.STATUS_RELEASED}},
        extra={
            'item_id': str(item.pk),
            'item_code': item.item_code,
            'revision': revision.revision,
            'superseded_revision_id': str(current_released.pk) if current_released else None,
            'effective_from': str(revision.effective_from),
        },
    )
    return revision


@transaction.atomic
def obsolete_revision(revision: ItemRevision, *, actor):
    _require_releaser(actor)
    revision = ItemRevision.objects.select_for_update().select_related('item').get(pk=revision.pk)
    _validate_transition(revision, ItemRevision.STATUS_OBSOLETE)
    revision.is_active = False
    return _save_status(revision, new_status=ItemRevision.STATUS_OBSOLETE, actor=actor, update_fields=['is_active'])


def ensure_revision_editable(instance: ItemRevision, attrs: dict):
    if not instance or instance.status not in ItemRevision.LOCKED_STATUSES:
        return
    changed = []
    for field in ItemRevision.TECHNICAL_FIELDS:
        if field in attrs:
            next_value = attrs[field]
            current = getattr(instance, field)
            if next_value != current:
                changed.append(field)
    if changed:
        raise ValidationError({field: 'Released engineering revisions are immutable.' for field in changed})


def ensure_revision_deletable(instance: ItemRevision):
    if instance.status != ItemRevision.STATUS_DRAFT:
        raise EngineeringLifecycleError({'status': 'Only draft item revisions can be deleted.'})
