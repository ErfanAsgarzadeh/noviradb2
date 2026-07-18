from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from auditlog.services import log_event, model_to_dict_safe

from .exceptions import EngineeringLifecycleError, EngineeringPermissionError
from .models import BOM, BOMLine, BOMRevision, ItemRevision
from .permissions import can_manage_engineering, is_engineering_releaser

TRANSITIONS = {
    BOMRevision.STATUS_DRAFT: {BOMRevision.STATUS_UNDER_REVIEW},
    BOMRevision.STATUS_UNDER_REVIEW: {BOMRevision.STATUS_DRAFT, BOMRevision.STATUS_APPROVED},
    BOMRevision.STATUS_APPROVED: {BOMRevision.STATUS_UNDER_REVIEW, BOMRevision.STATUS_RELEASED},
    BOMRevision.STATUS_RELEASED: {BOMRevision.STATUS_SUPERSEDED, BOMRevision.STATUS_OBSOLETE},
    BOMRevision.STATUS_SUPERSEDED: {BOMRevision.STATUS_OBSOLETE},
    BOMRevision.STATUS_OBSOLETE: set(),
}


def _require_manager(actor):
    if not can_manage_engineering(actor):
        raise EngineeringPermissionError('You do not have permission to manage BOM revisions.')


def _require_releaser(actor):
    if not is_engineering_releaser(actor):
        raise EngineeringPermissionError('You do not have permission to release BOM revisions.')


def _validate_transition(revision: BOMRevision, target_status: str):
    if target_status not in TRANSITIONS.get(revision.status, set()):
        raise EngineeringLifecycleError({'status': f'Cannot transition BOM revision from {revision.status} to {target_status}.'})


def _periods_overlap(a_start: date, a_end: date | None, b_start: date, b_end: date | None) -> bool:
    return a_start <= (b_end or date.max) and b_start <= (a_end or date.max)


def ensure_bom_revision_editable(instance: BOMRevision | None, attrs: dict):
    if not instance or instance.status not in BOMRevision.LOCKED_STATUSES:
        return
    changed = [field for field in BOMRevision.TECHNICAL_FIELDS if field in attrs and attrs[field] != getattr(instance, field)]
    if changed:
        raise ValidationError({field: 'Released BOM revisions are immutable.' for field in changed})


def ensure_bom_line_editable(line: BOMLine | None):
    if line and line.bom_revision.status in BOMRevision.LOCKED_STATUSES:
        raise EngineeringLifecycleError({'bom_revision': 'Released BOM revisions are immutable.'})


def ensure_bom_revision_deletable(instance: BOMRevision):
    if instance.status != BOMRevision.STATUS_DRAFT:
        raise EngineeringLifecycleError({'status': 'Only draft BOM revisions can be deleted.'})


def _validate_no_overlap(revision: BOMRevision, *, exclude_ids: Iterable[str] = ()): 
    if not revision.effective_from:
        return
    released = BOMRevision.objects.filter(bom=revision.bom, status=BOMRevision.STATUS_RELEASED).exclude(pk=revision.pk)
    excluded = {str(value) for value in exclude_ids}
    if excluded:
        released = released.exclude(pk__in=excluded)
    for existing in released:
        if existing.effective_from and _periods_overlap(revision.effective_from, revision.effective_to, existing.effective_from, existing.effective_to):
            raise EngineeringLifecycleError({'effective_from': 'Released BOM revisions cannot have overlapping effective periods.'})


def _released_bom_for_parent(parent: ItemRevision, *, candidate: BOMRevision | None = None):
    if candidate and candidate.bom.parent_item_revision_id == parent.pk:
        return candidate
    return BOMRevision.objects.filter(
        bom__parent_item_revision=parent,
        status=BOMRevision.STATUS_RELEASED,
    ).prefetch_related('lines__component_item_revision__item').order_by('-released_at', '-created_at').first()


def _cycle_path(candidate: BOMRevision) -> list[str]:
    target = candidate.bom.parent_item_revision

    def visit(current: ItemRevision, path: list[ItemRevision], seen: set[str]) -> list[ItemRevision] | None:
        if current.pk == target.pk and path:
            return [*path, current]
        key = str(current.pk)
        if key in seen:
            return None
        seen.add(key)
        released = _released_bom_for_parent(current, candidate=candidate)
        if not released:
            return None
        for line in released.lines.all():
            result = visit(line.component_item_revision, [*path, current], set(seen))
            if result:
                return result
        return None

    for line in candidate.lines.select_related('component_item_revision__item'):
        result = visit(line.component_item_revision, [target], set())
        if result:
            return [f'{rev.item.item_code}/{rev.revision}' for rev in result]
    return []


def validate_bom_revision_for_release(revision: BOMRevision, *, exclude_overlap_ids=()):
    errors = {}
    parent = revision.bom.parent_item_revision
    if parent.status not in {ItemRevision.STATUS_APPROVED, ItemRevision.STATUS_RELEASED}:
        errors['parent_item_revision'] = 'Released BOM requires an approved or released parent item revision.'
    if not revision.effective_from:
        errors['effective_from'] = 'Effective start date is required before release.'
    lines = list(revision.lines.select_related('component_item_revision__item'))
    if not lines:
        errors['lines'] = 'BOM revision must contain at least one line before release.'
    seen_components = set()
    for line in lines:
        if line.component_item_revision_id == parent.pk:
            errors['component_item_revision'] = 'A BOM line cannot reference the parent item revision.'
        if line.quantity <= 0:
            errors['quantity'] = 'All BOM quantities must be greater than zero.'
        if line.scrap_percent < 0:
            errors['scrap_percent'] = 'Scrap percentage cannot be negative.'
        if line.component_item_revision.status != ItemRevision.STATUS_RELEASED:
            errors['component_item_revision'] = 'Released BOM lines must reference released component item revisions.'
        component_key = str(line.component_item_revision_id)
        if component_key in seen_components:
            errors['component_item_revision'] = 'Duplicate component item revisions are not allowed in one BOM revision.'
        seen_components.add(component_key)
    if errors:
        raise EngineeringLifecycleError(errors)
    path = _cycle_path(revision)
    if path:
        raise EngineeringLifecycleError({'cycle': 'Circular BOM structure detected: ' + ' -> '.join(path)})
    _validate_no_overlap(revision, exclude_ids=exclude_overlap_ids)


def _save_status(revision: BOMRevision, *, new_status: str, actor, update_fields: list[str] | None = None):
    old = revision.status
    revision.status = new_status
    fields = list(dict.fromkeys([*(update_fields or []), 'status', 'updated_at']))
    revision.save(update_fields=fields)
    log_event(
        f'bom_revision_{new_status.lower()}',
        target=revision,
        category='business',
        changes={'status': {'old': old, 'new': new_status}},
        extra={'bom_id': str(revision.bom_id), 'revision': revision.revision, 'parent_item_revision_id': str(revision.bom.parent_item_revision_id)},
    )
    return revision


@transaction.atomic
def submit_bom_revision(revision: BOMRevision, *, actor):
    _require_manager(actor)
    revision = BOMRevision.objects.select_for_update().select_related('bom__parent_item_revision__item').get(pk=revision.pk)
    _validate_transition(revision, BOMRevision.STATUS_UNDER_REVIEW)
    revision.submitted_by = actor
    revision.submitted_at = timezone.now()
    return _save_status(revision, new_status=BOMRevision.STATUS_UNDER_REVIEW, actor=actor, update_fields=['submitted_by', 'submitted_at'])


@transaction.atomic
def return_bom_revision_to_draft(revision: BOMRevision, *, actor):
    _require_manager(actor)
    revision = BOMRevision.objects.select_for_update().select_related('bom__parent_item_revision__item').get(pk=revision.pk)
    _validate_transition(revision, BOMRevision.STATUS_DRAFT)
    return _save_status(revision, new_status=BOMRevision.STATUS_DRAFT, actor=actor)


@transaction.atomic
def return_bom_revision_to_review(revision: BOMRevision, *, actor):
    _require_manager(actor)
    revision = BOMRevision.objects.select_for_update().select_related('bom__parent_item_revision__item').get(pk=revision.pk)
    _validate_transition(revision, BOMRevision.STATUS_UNDER_REVIEW)
    return _save_status(revision, new_status=BOMRevision.STATUS_UNDER_REVIEW, actor=actor)


@transaction.atomic
def approve_bom_revision(revision: BOMRevision, *, actor):
    _require_releaser(actor)
    revision = BOMRevision.objects.select_for_update().select_related('bom__parent_item_revision__item').get(pk=revision.pk)
    _validate_transition(revision, BOMRevision.STATUS_APPROVED)
    revision.approved_by = actor
    revision.approved_at = timezone.now()
    return _save_status(revision, new_status=BOMRevision.STATUS_APPROVED, actor=actor, update_fields=['approved_by', 'approved_at'])


@transaction.atomic
def release_bom_revision(revision: BOMRevision, *, actor, effective_from=None, supersede_current=False):
    _require_releaser(actor)
    revision = BOMRevision.objects.select_for_update().select_related('bom__parent_item_revision__item').prefetch_related('lines__component_item_revision__item').get(pk=revision.pk)
    bom = BOM.objects.select_for_update().get(pk=revision.bom_id)
    revision.bom = bom
    _validate_transition(revision, BOMRevision.STATUS_RELEASED)
    if effective_from is not None:
        revision.effective_from = effective_from
    current = BOMRevision.objects.select_for_update().filter(bom=bom, status=BOMRevision.STATUS_RELEASED, effective_to__isnull=True).exclude(pk=revision.pk).order_by('-released_at', '-created_at').first()
    validate_bom_revision_for_release(revision, exclude_overlap_ids=[current.pk] if current and supersede_current else [])
    if current and not supersede_current:
        raise EngineeringLifecycleError({'supersede': 'A currently released BOM revision exists. Use supersede.'})
    now = timezone.now()
    old_snapshot = model_to_dict_safe(revision)
    if current:
        if not revision.effective_from or not current.effective_from or revision.effective_from <= current.effective_from:
            raise EngineeringLifecycleError({'effective_from': 'Superseding BOM revision must start after the current released revision starts.'})
        current.status = BOMRevision.STATUS_SUPERSEDED
        current.effective_to = revision.effective_from - timedelta(days=1)
        current.superseded_at = now
        current.superseded_by = revision
        current.save(update_fields=['status', 'effective_to', 'superseded_at', 'superseded_by', 'updated_at'])
        log_event('bom_revision_superseded', target=current, category='business', changes={'status': {'old': BOMRevision.STATUS_RELEASED, 'new': BOMRevision.STATUS_SUPERSEDED}}, extra={'superseded_by': str(revision.pk)})
    revision.status = BOMRevision.STATUS_RELEASED
    revision.released_by = actor
    revision.released_at = now
    revision.is_active = True
    revision.save(update_fields=['status', 'released_by', 'released_at', 'effective_from', 'is_active', 'updated_at'])
    log_event('bom_revision_released', target=revision, category='business', changes={'status': {'old': old_snapshot.get('status'), 'new': BOMRevision.STATUS_RELEASED}}, extra={'bom_id': str(bom.pk), 'revision': revision.revision, 'parent_item_revision_id': str(bom.parent_item_revision_id)})
    return revision


@transaction.atomic
def obsolete_bom_revision(revision: BOMRevision, *, actor):
    _require_releaser(actor)
    revision = BOMRevision.objects.select_for_update().select_related('bom__parent_item_revision__item').get(pk=revision.pk)
    _validate_transition(revision, BOMRevision.STATUS_OBSOLETE)
    revision.is_active = False
    return _save_status(revision, new_status=BOMRevision.STATUS_OBSOLETE, actor=actor, update_fields=['is_active'])


@transaction.atomic
def clone_bom_revision(source: BOMRevision, *, actor, revision_code: str):
    _require_manager(actor)
    source = BOMRevision.objects.select_for_update().prefetch_related('lines').get(pk=source.pk)
    clone = BOMRevision.objects.create(
        bom=source.bom,
        revision=revision_code,
        description=source.description,
        effective_from=None,
        effective_to=None,
        status=BOMRevision.STATUS_DRAFT,
        created_by=actor,
    )
    for line in source.lines.all():
        BOMLine.objects.create(
            bom_revision=clone,
            sequence=line.sequence,
            component_item_revision=line.component_item_revision,
            quantity=line.quantity,
            unit=line.unit,
            scrap_percent=line.scrap_percent,
            is_phantom=line.is_phantom,
            is_optional=line.is_optional,
            reference_designator=line.reference_designator,
            notes=line.notes,
        )
    log_event('bom_revision_cloned', target=clone, category='business', extra={'source_revision_id': str(source.pk), 'revision': clone.revision})
    return clone
