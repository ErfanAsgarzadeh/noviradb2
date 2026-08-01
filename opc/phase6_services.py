from __future__ import annotations

import hashlib
from datetime import timedelta

from django.db import transaction
from django.http import FileResponse
from django.utils import timezone

from auditlog.services import log_event, model_to_dict_safe
from enterprise_items.exceptions import EngineeringLifecycleError, EngineeringPermissionError
from enterprise_items.models import BOMLine, BOMRevision, ItemRevision
from enterprise_items.permissions import can_manage_engineering, is_engineering_releaser

from .models import (
    ControlledDocumentRevision,
    EngineeringChangeObjectLink,
    EngineeringChangeOrder,
    EngineeringChangeRequest,
    OPCDiagram,
    OPCNodeDocumentRequirement,
    OPCOperationMaterialAllocation,
)


def _require_manager(actor):
    if not can_manage_engineering(actor):
        raise EngineeringPermissionError('You do not have permission to manage engineering change data.')


def _require_releaser(actor):
    if not is_engineering_releaser(actor):
        raise EngineeringPermissionError('You do not have permission to release controlled document revisions.')


def _periods_overlap(a_start, a_end, b_start, b_end):
    if not a_start or not b_start:
        return False
    return a_start <= (b_end or a_start.max) and b_start <= (a_end or b_start.max)


def calculate_document_file_metadata(revision: ControlledDocumentRevision):
    file_obj = revision.file
    if not file_obj:
        return
    position = file_obj.tell() if hasattr(file_obj, 'tell') else None
    hasher = hashlib.sha256()
    file_obj.seek(0)
    for chunk in file_obj.chunks():
        hasher.update(chunk)
    if position is not None:
        file_obj.seek(position)
    revision.checksum_sha256 = hasher.hexdigest()
    revision.file_size = getattr(file_obj, 'size', 0) or 0
    revision.original_filename = revision.original_filename or getattr(file_obj, 'name', '').split('/')[-1]
    revision.mime_type = revision.mime_type or getattr(file_obj, 'content_type', '')


def ensure_document_revision_editable(revision: ControlledDocumentRevision | None):
    if revision and revision.status in ControlledDocumentRevision.LOCKED_STATUSES:
        raise EngineeringLifecycleError({'status': 'Released document revisions are immutable.'})


@transaction.atomic
def release_document_revision(revision: ControlledDocumentRevision, *, actor, effective_from=None, supersede_current=False):
    _require_releaser(actor)
    revision = ControlledDocumentRevision.objects.select_for_update(of=('self',)).select_related('document').get(pk=revision.pk)
    if revision.status not in {ControlledDocumentRevision.STATUS_APPROVED, ControlledDocumentRevision.STATUS_DRAFT}:
        raise EngineeringLifecycleError({'status': 'Document revision must be draft or approved before release.'})
    if effective_from is not None:
        revision.effective_from = effective_from
    if not revision.effective_from:
        raise EngineeringLifecycleError({'effective_from': 'Effective start date is required before release.'})
    current = ControlledDocumentRevision.objects.select_for_update().filter(
        document=revision.document,
        status=ControlledDocumentRevision.STATUS_RELEASED,
        effective_to__isnull=True,
    ).exclude(pk=revision.pk).order_by('-released_at', '-created_at').first()
    if current and not supersede_current:
        raise EngineeringLifecycleError({'supersede': 'A currently released document revision exists. Use supersede.'})
    now = timezone.now()
    old_snapshot = model_to_dict_safe(revision)
    if current:
        if revision.effective_from <= current.effective_from:
            raise EngineeringLifecycleError({'effective_from': 'Superseding document revision must start after the current released revision starts.'})
        current.status = ControlledDocumentRevision.STATUS_SUPERSEDED
        current.effective_to = revision.effective_from - timedelta(days=1)
        current.superseded_by = revision
        current.save(update_fields=['status', 'effective_to', 'superseded_by', 'updated_at'])
    revision.status = ControlledDocumentRevision.STATUS_RELEASED
    revision.released_by = actor
    revision.released_at = now
    calculate_document_file_metadata(revision)
    revision.save(update_fields=['status', 'released_by', 'released_at', 'effective_from', 'checksum_sha256', 'mime_type', 'original_filename', 'file_size', 'updated_at'])
    log_event('controlled_document_revision_released', target=revision, category='business', changes={'status': {'old': old_snapshot.get('status'), 'new': revision.status}})
    return revision


def document_download_response(revision: ControlledDocumentRevision):
    if not revision.file:
        raise EngineeringLifecycleError({'file': 'Document revision has no file.'})
    return FileResponse(revision.file.open('rb'), as_attachment=True, filename=revision.original_filename or revision.file.name)


def _change_status(instance, *, actor, status):
    _require_manager(actor)
    old = instance.status
    instance.status = status
    now = timezone.now()
    fields = ['status', 'updated_at']
    if isinstance(instance, EngineeringChangeRequest):
        if status == EngineeringChangeRequest.STATUS_SUBMITTED:
            instance.submitted_at = now
            fields.append('submitted_at')
        if status in {EngineeringChangeRequest.STATUS_ACCEPTED, EngineeringChangeRequest.STATUS_REJECTED}:
            instance.decided_at = now
            fields.append('decided_at')
    if isinstance(instance, EngineeringChangeOrder):
        if status == EngineeringChangeOrder.STATUS_APPROVED:
            instance.approved_at = now
            fields.append('approved_at')
        if status == EngineeringChangeOrder.STATUS_IMPLEMENTED:
            instance.implemented_at = now
            fields.append('implemented_at')
    instance.save(update_fields=fields)
    log_event('engineering_change_status_changed', target=instance, category='business', changes={'status': {'old': old, 'new': status}})
    return instance


def change_request_transition(request: EngineeringChangeRequest, *, actor, status):
    allowed = {
        EngineeringChangeRequest.STATUS_DRAFT: {EngineeringChangeRequest.STATUS_SUBMITTED},
        EngineeringChangeRequest.STATUS_SUBMITTED: {EngineeringChangeRequest.STATUS_ACCEPTED, EngineeringChangeRequest.STATUS_REJECTED},
        EngineeringChangeRequest.STATUS_ACCEPTED: set(),
        EngineeringChangeRequest.STATUS_REJECTED: set(),
    }
    if status not in allowed.get(request.status, set()):
        raise EngineeringLifecycleError({'status': f'Cannot transition ECR from {request.status} to {status}.'})
    return _change_status(request, actor=actor, status=status)


def change_order_transition(order: EngineeringChangeOrder, *, actor, status):
    allowed = {
        EngineeringChangeOrder.STATUS_DRAFT: {EngineeringChangeOrder.STATUS_UNDER_REVIEW},
        EngineeringChangeOrder.STATUS_UNDER_REVIEW: {EngineeringChangeOrder.STATUS_APPROVED, EngineeringChangeOrder.STATUS_CANCELLED},
        EngineeringChangeOrder.STATUS_APPROVED: {EngineeringChangeOrder.STATUS_IMPLEMENTED, EngineeringChangeOrder.STATUS_CANCELLED},
        EngineeringChangeOrder.STATUS_IMPLEMENTED: set(),
        EngineeringChangeOrder.STATUS_CANCELLED: set(),
    }
    if status not in allowed.get(order.status, set()):
        raise EngineeringLifecycleError({'status': f'Cannot transition ECO from {order.status} to {status}.'})
    return _change_status(order, actor=actor, status=status)


def _object_label(object_type: str, object_id) -> str:
    if object_type == EngineeringChangeObjectLink.OBJECT_ITEM_REVISION:
        obj = ItemRevision.objects.select_related('item').filter(pk=object_id).first()
        return f'{obj.item.item_code}/{obj.revision}' if obj else str(object_id)
    if object_type == EngineeringChangeObjectLink.OBJECT_BOM_REVISION:
        obj = BOMRevision.objects.select_related('bom__parent_item_revision__item').filter(pk=object_id).first()
        return f'{obj.bom.parent_item_revision.item.item_code}/{obj.bom.bom_type}/{obj.revision}' if obj else str(object_id)
    if object_type == EngineeringChangeObjectLink.OBJECT_OPC_DIAGRAM:
        obj = OPCDiagram.objects.filter(pk=object_id).first()
        return f'{obj.part_code}/{obj.revision}' if obj else str(object_id)
    if object_type == EngineeringChangeObjectLink.OBJECT_DOCUMENT_REVISION:
        obj = ControlledDocumentRevision.objects.select_related('document').filter(pk=object_id).first()
        return f'{obj.document.document_number}/{obj.revision}' if obj else str(object_id)
    return str(object_id)


def analyze_change_impact(*, object_type: str, object_id) -> dict:
    direct = []
    transitive = []
    links = EngineeringChangeObjectLink.objects.filter(object_type=object_type, object_id=object_id).select_related('request', 'order').order_by('role', 'created_at')
    for link in links:
        direct.append({
            'change_type': 'ECO' if link.order_id else 'ECR',
            'change_id': str(link.order_id or link.request_id),
            'change_number': link.order.change_number if link.order_id else link.request.change_number,
            'status': link.order.status if link.order_id else link.request.status,
            'role': link.role,
            'object_type': link.object_type,
            'object_id': str(link.object_id),
            'object_label': _object_label(link.object_type, link.object_id),
        })
    if object_type == EngineeringChangeObjectLink.OBJECT_ITEM_REVISION:
        for bom in BOMRevision.objects.filter(bom__parent_item_revision_id=object_id).select_related('bom').order_by('bom__bom_type', 'revision'):
            transitive.append({'object_type': 'BOM_REVISION', 'object_id': str(bom.pk), 'object_label': _object_label('BOM_REVISION', bom.pk), 'relationship': 'parent_bom'})
        for opc in OPCDiagram.objects.filter(item_revision_id=object_id).order_by('part_code', 'revision'):
            transitive.append({'object_type': 'OPC_DIAGRAM', 'object_id': str(opc.pk), 'object_label': _object_label('OPC_DIAGRAM', opc.pk), 'relationship': 'opc_for_item_revision'})
    if object_type == EngineeringChangeObjectLink.OBJECT_BOM_REVISION:
        for allocation in OPCOperationMaterialAllocation.objects.filter(bom_line__bom_revision_id=object_id).select_related('node__diagram').order_by('node__diagram__part_code', 'node__sequence'):
            transitive.append({'object_type': 'OPC_DIAGRAM', 'object_id': str(allocation.node.diagram_id), 'object_label': _object_label('OPC_DIAGRAM', allocation.node.diagram_id), 'relationship': 'allocated_bom_line'})
    if object_type == EngineeringChangeObjectLink.OBJECT_DOCUMENT_REVISION:
        for requirement in OPCNodeDocumentRequirement.objects.filter(document_revision_id=object_id).select_related('node__diagram').order_by('node__diagram__part_code', 'node__sequence'):
            transitive.append({'object_type': 'OPC_DIAGRAM', 'object_id': str(requirement.node.diagram_id), 'object_label': _object_label('OPC_DIAGRAM', requirement.node.diagram_id), 'relationship': 'assigned_document'})
    return {
        'object_type': object_type,
        'object_id': str(object_id),
        'direct': direct,
        'transitive': transitive,
    }


def released_mbom_for_item_revision(item_revision_id):
    return BOMRevision.objects.filter(
        bom__parent_item_revision_id=item_revision_id,
        bom__bom_type='MANUFACTURING',
        status=BOMRevision.STATUS_RELEASED,
    ).select_related('bom__parent_item_revision__item').prefetch_related('lines__component_item_revision__item').order_by('-released_at', '-created_at').first()
