from __future__ import annotations

from decimal import Decimal

import pytest
from django.urls import reverse
from rest_framework import status

from opc.models import (
    ManufacturingOperation,
    OPCToolingRequirement,
    ProductionMaterialRequirement,
    ProductionOrder,
    ProductionOrderValidationEvidence,
)
from opc.production_services import generate_order_baseline, release_production_order
from opc.services import release_opc_revision
from tests.test_opc_graph_persistence_phase3 import api
from tests.test_opc_phase6_bom_documents_change import make_phase6_ready


pytestmark = pytest.mark.django_db


def released_source(code='P-P7'):
    admin, diagram, nodes, edges, refs = make_phase6_ready(code, status_value='APPROVED')
    OPCToolingRequirement.objects.create(node=nodes[1], tooling_definition=refs['tooling'], quantity='2.000', mandatory=True)
    released = release_opc_revision(diagram, actor=admin, expected_graph_version=diagram.graph_version)
    return admin, released, nodes, edges, refs


def create_order(admin, diagram, refs, *, quantity='5.000000') -> ProductionOrder:
    return ProductionOrder.objects.create(
        order_number='PO-TEST-000001',
        item_revision=diagram.item_revision,
        planned_quantity=quantity,
        unit='EA',
        source_opc_diagram=diagram,
        source_graph_version=diagram.graph_version,
        source_validation_evidence=diagram.validation_evidence.filter(release_ready=True).first(),
        source_manufacturing_bom_revision=refs['mbom_revision'],
        created_by=admin,
    )


def test_generation_creates_immutable_source_snapshots_operations_requirements_and_checksum():
    admin, diagram, _nodes, _edges, refs = released_source('P-P7-GEN')
    refs['allocation'].scrap_percent = Decimal('10.000')
    refs['allocation'].save(update_fields=['scrap_percent'])
    order = create_order(admin, diagram, refs, quantity='5.000000')

    generated = generate_order_baseline(order, actor=admin, expected_version=0)

    assert generated.status == ProductionOrder.STATUS_PLANNED
    assert generated.order_version == 1
    assert generated.source_snapshot_checksum
    assert generated.operations.count() == diagram.nodes.count()
    op = generated.operations.get(source_opc_node=refs['allocation'].node)
    assert op.process_code_snapshot
    assert op.eligible_machines.get().machine_asset == refs['machine']
    assert generated.precedences.count() == diagram.edges.count()
    material = generated.material_requirements.get()
    assert material.source_mbom_line == refs['bom_line']
    assert material.quantity_per_unit == Decimal('1.000000')
    assert material.total_net_quantity == Decimal('5.000000')
    assert material.total_planned_quantity == Decimal('5.500000')
    assert generated.document_requirements.get().controlled_document_revision == refs['document_revision']
    assert generated.tooling_requirements.get().tooling_definition == refs['tooling']


def test_repeat_generation_is_deterministic_and_replaces_draft_rows_atomically():
    admin, diagram, _nodes, _edges, refs = released_source('P-P7-REGEN')
    order = create_order(admin, diagram, refs)

    first = generate_order_baseline(order, actor=admin, expected_version=0)
    checksum = first.source_snapshot_checksum
    operation_ids = set(first.operations.values_list('id', flat=True))
    second = generate_order_baseline(first, actor=admin, expected_version=1)

    assert second.order_version == 2
    assert second.source_snapshot_checksum != ''
    assert second.source_snapshot['item_revision'] == first.source_snapshot['item_revision']
    assert set(second.operations.values_list('id', flat=True)) != operation_ids
    assert second.operations.count() == first.source_opc_diagram.nodes.count()
    assert checksum == second.source_snapshot_checksum


def test_release_validation_evidence_and_released_baseline_immutability():
    admin, diagram, _nodes, _edges, refs = released_source('P-P7-REL')
    order = generate_order_baseline(create_order(admin, diagram, refs), actor=admin, expected_version=0)

    released = release_production_order(order, actor=admin, expected_version=1)

    assert released.status == ProductionOrder.STATUS_RELEASED
    assert released.order_version == 2
    evidence = ProductionOrderValidationEvidence.objects.get(production_order=released, released_at__isnull=False)
    assert evidence.release_ready is True
    op = released.operations.first()
    op.label = 'Mutated source label'
    with pytest.raises(Exception):
        op.save()
    with pytest.raises(Exception):
        generate_order_baseline(released, actor=admin, expected_version=2)


def test_production_order_api_create_generate_conflict_validate_release_and_cancel():
    admin, diagram, _nodes, _edges, refs = released_source('P-P7-API')
    client = api(admin)
    create = client.post(reverse('opc-production-order-list'), {
        'item_revision': str(diagram.item_revision_id),
        'planned_quantity': '3.000000',
        'unit': 'EA',
        'priority': 'HIGH',
        'source_opc_diagram': str(diagram.pk),
        'source_manufacturing_bom_revision': str(refs['mbom_revision'].pk),
    }, format='json')
    assert create.status_code == status.HTTP_201_CREATED, create.data
    assert create.data['order_number'].startswith('PO-')
    assert create.data['order_version'] == 0

    stale = client.post(reverse('opc-production-order-generate', kwargs={'pk': create.data['id']}), {'expected_version': 99}, format='json')
    assert stale.status_code == status.HTTP_409_CONFLICT

    generated = client.post(reverse('opc-production-order-generate', kwargs={'pk': create.data['id']}), {'expected_version': 0}, format='json')
    assert generated.status_code == status.HTTP_200_OK, generated.data
    assert generated.data['operations']
    assert generated.data['material_requirements'][0]['total_net_quantity'] == '3.000000'

    validation = client.post(reverse('opc-production-order-validate', kwargs={'pk': create.data['id']}), {}, format='json')
    assert validation.status_code == status.HTTP_200_OK
    assert validation.data['release_ready'] is True

    released = client.post(reverse('opc-production-order-release', kwargs={'pk': create.data['id']}), {'expected_version': generated.data['order_version']}, format='json')
    assert released.status_code == status.HTTP_200_OK, released.data
    patch = client.patch(reverse('opc-production-order-detail', kwargs={'pk': create.data['id']}), {'expected_version': released.data['order_version'], 'planned_quantity': '4.000000'}, format='json')
    assert patch.status_code == status.HTTP_400_BAD_REQUEST

    cancelled = client.post(reverse('opc-production-order-cancel', kwargs={'pk': create.data['id']}), {'expected_version': released.data['order_version']}, format='json')
    assert cancelled.status_code == status.HTTP_200_OK, cancelled.data
    assert cancelled.data['status'] == ProductionOrder.STATUS_CANCELLED


def test_generation_rejects_draft_or_mismatched_sources():
    admin, diagram, _nodes, _edges, refs = make_phase6_ready('P-P7-BAD')
    order = ProductionOrder(
        order_number='PO-TEST-BAD',
        item_revision=diagram.item_revision,
        planned_quantity='1.000000',
        unit='EA',
        source_opc_diagram=diagram,
        source_manufacturing_bom_revision=refs['mbom_revision'],
        created_by=admin,
    )
    with pytest.raises(Exception):
        order.full_clean()
