from __future__ import annotations

from django.db import transaction
from django.db.models import Count, Max, Q
from django.utils import timezone

from auditlog.models import AuditEvent
from auditlog.services import log_event
from opc.models import OPCDiagram, OPCNode

from .bom_services import validate_bom_revision_for_release
from .coding_services import governance_decision_for_attribute_change
from .exceptions import EngineeringLifecycleError, EngineeringPermissionError
from .models import BOMRevision, Item, ItemIdentifier, ItemRevision, ItemRevisionAttributeValue, get_effective_classification_attributes
from .permissions import can_manage_engineering
from .readiness import evaluate_item_revision_readiness

ACTIVE_STATUS_MAP = {
    'DRAFT': True,
    'ACTIVE': True,
    'PHASE_OUT': True,
    'BLOCKED': False,
    'OBSOLETE': False,
    'MERGED': False,
}


def current_revision_for_item(item: Item):
    revisions = list(getattr(item, '_prefetched_objects_cache', {}).get('revisions', []))
    if revisions:
        return next((rev for rev in revisions if rev.revision == item.default_revision), revisions[0])
    return item.revisions.order_by('-released_at', '-created_at').first()


def released_revision_for_item(item: Item):
    revisions = list(getattr(item, '_prefetched_objects_cache', {}).get('revisions', []))
    if revisions:
        return next((rev for rev in revisions if rev.status == ItemRevision.STATUS_RELEASED and rev.effective_to is None), None)
    return item.revisions.filter(status=ItemRevision.STATUS_RELEASED, effective_to__isnull=True).order_by('-released_at').first()


def apply_part_list_filters(queryset, params):
    search = (params.get('search') or '').strip()
    if search:
        queryset = queryset.filter(
            Q(item_code__icontains=search) |
            Q(name__icontains=search) |
            Q(specification__icontains=search) |
            Q(drawing_no__icontains=search) |
            Q(identifiers__value__icontains=search) |
            Q(identifiers__normalized_value__icontains=search)
        ).distinct()
    classification = params.get('classification')
    if classification:
        include_desc = params.get('include_descendants', 'true') != 'false'
        if include_desc:
            from .models import ItemClassification
            selected = ItemClassification.objects.filter(pk=classification).first()
            if selected:
                queryset = queryset.filter(classification__path__startswith=selected.path)
        else:
            queryset = queryset.filter(classification_id=classification)
    for key in ('status', 'make_or_buy', 'code_generation_strategy'):
        value = params.get(key)
        if value:
            queryset = queryset.filter(**{key: value})
    is_active = params.get('is_active')
    if is_active in {'true', 'false'}:
        queryset = queryset.filter(is_active=is_active == 'true')
    revision_status = params.get('revision_status')
    if revision_status:
        queryset = queryset.filter(revisions__status=revision_status).distinct()
    readiness = params.get('readiness')
    if readiness == 'ready':
        queryset = queryset.filter(revisions__status=ItemRevision.STATUS_RELEASED).distinct()
    elif readiness == 'issues':
        queryset = queryset.exclude(revisions__status=ItemRevision.STATUS_RELEASED).distinct()
    return queryset


def sort_part_list(queryset, ordering):
    allowed = {
        'part_number': 'item_code', '-part_number': '-item_code',
        'name': 'name', '-name': '-name', 'modified': 'updated_at', '-modified': '-updated_at',
        'status': 'status', '-status': '-status', 'make_or_buy': 'make_or_buy', '-make_or_buy': '-make_or_buy',
    }
    return queryset.order_by(allowed.get(ordering or '', 'item_code'))


def part_list_row(item: Item):
    current = current_revision_for_item(item)
    readiness = evaluate_item_revision_readiness(current) if current else {'release_ready': False, 'blockers': ['No revision exists.'], 'warnings': []}
    primary_mpn = item.identifiers.filter(identifier_type=ItemIdentifier.TYPE_MANUFACTURER_PART_NUMBER, is_primary=True).first()
    return {
        'id': str(item.pk),
        'part_number': item.item_code,
        'name': item.name,
        'classification': str(item.classification_id) if item.classification_id else None,
        'classification_path': item.classification.path if item.classification_id else '',
        'make_or_buy': item.make_or_buy,
        'current_revision': current.revision if current else '',
        'current_revision_id': str(current.pk) if current else None,
        'revision_lifecycle': current.status if current else '',
        'item_status': item.status,
        'is_active': item.is_active,
        'readiness': readiness,
        'coding_strategy': item.code_generation_strategy,
        'drawing_number': item.drawing_no,
        'manufacturer_part_number': primary_mpn.value if primary_mpn else '',
        'modified': item.updated_at,
    }


def dashboard_metrics():
    return {
        'active_parts': Item.objects.filter(status='ACTIVE').count(),
        'phase_out_or_obsolete': Item.objects.filter(status__in=['PHASE_OUT', 'OBSOLETE']).count(),
        'draft_revisions': ItemRevision.objects.filter(status=ItemRevision.STATUS_DRAFT).count(),
        'under_review_revisions': ItemRevision.objects.filter(status=ItemRevision.STATUS_UNDER_REVIEW).count(),
        'approved_awaiting_release': ItemRevision.objects.filter(status=ItemRevision.STATUS_APPROVED).count(),
        'released_revisions': ItemRevision.objects.filter(status=ItemRevision.STATUS_RELEASED).count(),
        'parts_missing_released_revision': Item.objects.exclude(revisions__status=ItemRevision.STATUS_RELEASED).distinct().count(),
        'recent_parts': [part_list_row(item) for item in Item.objects.select_related('classification').prefetch_related('revisions', 'identifiers').order_by('-updated_at')[:8]],
    }


def bom_summary(revision: ItemRevision | None):
    if not revision:
        return None
    bom_rev = BOMRevision.objects.filter(bom__parent_item_revision=revision).select_related('bom').annotate(component_count=Count('lines'), last_modified=Max('updated_at')).order_by('-created_at').first()
    if not bom_rev:
        return {'exists': False, 'revision_id': str(revision.pk), 'component_count': 0, 'status': '', 'issues': ['No BOM exists for this revision.']}
    return {'exists': True, 'bom_id': str(bom_rev.bom_id), 'bom_revision_id': str(bom_rev.pk), 'status': bom_rev.status, 'component_count': bom_rev.component_count, 'last_modified': bom_rev.last_modified, 'issues': []}


def opc_summary(revision: ItemRevision | None):
    if not revision:
        return None
    diagram = OPCDiagram.objects.filter(item_revision=revision).annotate(node_count=Count('nodes'), last_modified=Max('updated_at')).order_by('-created_at').first()
    if not diagram:
        return {'exists': False, 'revision_id': str(revision.pk), 'node_count': 0, 'status': '', 'issues': ['No OPC exists for this revision.']}
    return {'exists': True, 'diagram_id': str(diagram.pk), 'status': diagram.status, 'node_count': diagram.node_count, 'last_modified': diagram.last_modified, 'issues': []}


def part_detail_summary(item: Item):
    current = current_revision_for_item(item)
    released = released_revision_for_item(item)
    identifiers = list(item.identifiers.order_by('identifier_type', '-is_primary').values('id', 'identifier_type', 'value', 'organization_name', 'is_primary', 'is_verified', 'effective_from', 'effective_to')[:20])
    return {
        'item': part_list_row(item),
        'current_revision': revision_summary(current) if current else None,
        'released_revision': revision_summary(released) if released else None,
        'identifiers': identifiers,
        'bom_summary': bom_summary(current),
        'opc_summary': opc_summary(current),
        'allowed_actions': item_allowed_actions(item),
        'coding': {'strategy': item.code_generation_strategy, 'scheme': str(item.coding_scheme_id) if item.coding_scheme_id else None, 'template': str(item.coding_template_id) if item.coding_template_id else None, 'generated_code_locked': item.generated_code_locked},
    }


def revision_summary(revision: ItemRevision | None):
    if not revision:
        return None
    readiness = evaluate_item_revision_readiness(revision)
    return {
        'id': str(revision.pk), 'revision': revision.revision, 'title': revision.title, 'status': revision.status,
        'created_at': revision.created_at, 'updated_at': revision.updated_at, 'approved_at': revision.approved_at,
        'released_at': revision.released_at, 'superseded_at': revision.superseded_at, 'readiness': readiness,
        'is_read_only': revision.status in ItemRevision.LOCKED_STATUSES,
        'allowed_actions': revision_allowed_actions(revision),
        'bom_summary': bom_summary(revision), 'opc_summary': opc_summary(revision),
    }


def revision_allowed_actions(revision):
    if revision.status == ItemRevision.STATUS_DRAFT:
        return ['submit']
    if revision.status == ItemRevision.STATUS_UNDER_REVIEW:
        return ['return_to_draft', 'approve']
    if revision.status == ItemRevision.STATUS_APPROVED:
        return ['return_to_review', 'release', 'supersede']
    if revision.status in {ItemRevision.STATUS_RELEASED, ItemRevision.STATUS_SUPERSEDED}:
        return ['obsolete']
    return []


def item_allowed_actions(item):
    if item.status == 'ACTIVE':
        return ['block', 'phase_out', 'obsolete']
    if item.status == 'PHASE_OUT':
        return ['activate', 'obsolete']
    if item.status == 'BLOCKED':
        return ['activate', 'obsolete']
    if item.status == 'DRAFT':
        return ['activate', 'block']
    return []


@transaction.atomic
def change_item_status(item: Item, *, actor, status, reason=''):
    if not can_manage_engineering(actor):
        raise EngineeringPermissionError('You do not have permission to change Part status.')
    if status not in ACTIVE_STATUS_MAP:
        raise EngineeringLifecycleError({'status': 'Unsupported Part status.'})
    item = Item.objects.select_for_update().get(pk=item.pk)
    old = {'status': item.status, 'is_active': item.is_active}
    item.status = status
    item.status_reason = reason or item.status_reason
    item.is_active = ACTIVE_STATUS_MAP[status]
    item.save(update_fields=['status', 'status_reason', 'is_active', 'updated_at'])
    log_event('part_status_changed', target=item, category='business', changes={'status': {'old': old['status'], 'new': item.status}, 'is_active': {'old': old['is_active'], 'new': item.is_active}}, extra={'reason': reason, 'item_code': item.item_code})
    return item


def revision_attribute_workspace(revision: ItemRevision):
    existing = {str(value.attribute_definition_id): value for value in revision.attribute_values.select_related('attribute_definition')}
    assignments = []
    if revision.item.classification_id:
        assignments = get_effective_classification_attributes(revision.item.classification).order_by('display_order')
    rows = []
    for assignment in assignments:
        attr = assignment.attribute_definition
        value = existing.get(str(attr.pk))
        rows.append({
            'assignment_id': str(assignment.pk), 'attribute_definition': str(attr.pk), 'code': attr.code, 'name': attr.name,
            'description': attr.description, 'data_type': attr.data_type, 'unit': value.unit if value else (assignment.default_unit or attr.default_unit),
            'value': _value_payload(value), 'required': assignment.effective_required, 'code_bearing': assignment.effective_code_bearing,
            'identity_defining': assignment.effective_identity_defining, 'revision_controlled': attr.is_revision_controlled,
            'governance_decision': governance_decision_for_attribute_change(attr), 'inherited': assignment.classification_id != revision.item.classification_id,
            'read_only': revision.status in ItemRevision.LOCKED_STATUSES,
        })
    return rows


def _value_payload(value):
    if not value:
        return None
    for field in ('value_text', 'value_decimal', 'value_integer', 'value_boolean', 'value_date', 'value_choice'):
        current = getattr(value, field)
        if current not in (None, ''):
            return current
    return None


def audit_history_for_item(item: Item, limit=50):
    target_ids = [str(item.pk), *[str(rev.pk) for rev in item.revisions.all()]]
    qs = AuditEvent.objects.filter(target_id__in=target_ids).select_related('actor').order_by('-timestamp')[:limit]
    return [{'timestamp': event.timestamp, 'actor': event.actor.username if event.actor else '', 'action': event.action, 'target_model': event.target_model, 'changes': event.changes, 'extra': event.extra} for event in qs]
