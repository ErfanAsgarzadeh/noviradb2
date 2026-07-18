from __future__ import annotations

from .models import BOMRevision, ItemRevision


def evaluate_item_revision_readiness(item_revision: ItemRevision) -> dict:
    item = item_revision.item
    has_released_bom = BOMRevision.objects.filter(
        bom__parent_item_revision=item_revision,
        status=BOMRevision.STATUS_RELEASED,
    ).exists()
    has_released_opc = item_revision.opc_diagrams.filter(status='RELEASED').exists()
    has_external_opc = item_revision.opc_diagrams.filter(status='RELEASED', nodes__execution_type='EXTERNAL').exists()
    blockers: list[str] = []
    warnings: list[str] = []

    make_or_buy = item.make_or_buy
    if make_or_buy == 'MAKE':
        if not has_released_bom:
            blockers.append('MAKE item revisions normally require a released BOM.')
        if not has_released_opc:
            blockers.append('MAKE item revisions normally require a released OPC.')
    elif make_or_buy == 'BUY':
        if not has_released_bom:
            warnings.append('BUY item revision has no released BOM; this is allowed in Phase 1B.')
        if not has_released_opc:
            warnings.append('BUY item revision has no released OPC; this is allowed in Phase 1B.')
    elif make_or_buy == 'OUTSOURCE':
        if not has_released_bom:
            warnings.append('OUTSOURCE item revision may require a released BOM by policy.')
        if not has_released_opc:
            blockers.append('OUTSOURCE item revision should have a released OPC with an external operation.')
        elif not has_external_opc:
            blockers.append('OUTSOURCE item revision OPC should include at least one EXTERNAL operation.')
    elif make_or_buy == 'PHANTOM':
        if not has_released_bom:
            blockers.append('PHANTOM item revisions require a released BOM.')
        if not has_released_opc:
            warnings.append('PHANTOM item revision standalone OPC is optional in Phase 1B.')
    elif make_or_buy == 'MAKE_OR_BUY':
        if not has_released_bom:
            warnings.append('MAKE_OR_BUY item revision has no released BOM.')
        if not has_released_opc:
            warnings.append('MAKE_OR_BUY item revision has no released OPC.')

    return {
        'item_revision_id': str(item_revision.pk),
        'item_code': item.item_code,
        'revision_code': item_revision.revision,
        'make_or_buy': make_or_buy,
        'item_revision_status': item_revision.status,
        'has_released_bom': has_released_bom,
        'has_released_opc': has_released_opc,
        'bom_ready': has_released_bom or make_or_buy == 'BUY',
        'opc_ready': has_released_opc or make_or_buy in {'BUY', 'PHANTOM'},
        'release_ready': not blockers,
        'blockers': blockers,
        'warnings': warnings,
    }
