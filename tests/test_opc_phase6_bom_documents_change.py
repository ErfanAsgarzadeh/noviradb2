from __future__ import annotations

from datetime import date

import pytest
from django.core.files.base import ContentFile
from django.urls import reverse
from rest_framework import status

from enterprise_items.models import BOM, BOMLine, BOMRevision, ItemRevision
from opc.models import (
    ControlledDocument,
    ControlledDocumentRevision,
    EngineeringChangeObjectLink,
    EngineeringChangeOrder,
    EngineeringChangeRequest,
    OPCDiagram,
    OPCNodeDocumentRequirement,
    OPCOperationMaterialAllocation,
)
from opc.release_validation import validate_opc_release_readiness
from opc.services import release_opc_revision
from tests.factories import make_company_admin
from tests.test_opc_graph_persistence_phase3 import api, graph_payload, make_diagram, make_item_revision
from tests.test_opc_phase4_resource_assignments import add_assignment, make_resources


pytestmark = pytest.mark.django_db


def make_phase6_ready(code='P-P6-READY', *, status_value=OPCDiagram.STATUS_DRAFT):
    admin = make_company_admin()
    plant, work_center, machine, process, _external, tooling = make_resources()
    diagram, nodes, edges = make_diagram(code)
    diagram.status = status_value
    diagram.save(update_fields=['status'])
    op = nodes[1]
    op.operation_number = 10
    op.process_definition = process
    op.plant = plant
    op.work_center = work_center
    op.machine_asset = machine
    op.save()
    component = make_item_revision(f'{code}-C', status_value=ItemRevision.STATUS_RELEASED)
    mbom = BOM.objects.create(parent_item_revision=diagram.item_revision, bom_type=BOM.TYPE_MANUFACTURING, created_by=admin)
    mbom_revision = BOMRevision.objects.create(bom=mbom, revision='M0', status=BOMRevision.STATUS_RELEASED, effective_from=diagram.effective_from, created_by=admin)
    bom_line = BOMLine.objects.create(bom_revision=mbom_revision, sequence=10, component_item_revision=component, quantity='1.000000', unit='EA')
    document = ControlledDocument.objects.create(document_number=f'WI-{code}', title='Work instruction', document_type=ControlledDocument.TYPE_WORK_INSTRUCTION, created_by=admin)
    document_revision = ControlledDocumentRevision.objects.create(document=document, revision='A', status=ControlledDocumentRevision.STATUS_RELEASED, effective_from=diagram.effective_from, file=ContentFile(b'phase6 work instruction', name='wi.pdf'), created_by=admin)
    allocation = OPCOperationMaterialAllocation.objects.create(node=op, bom_line=bom_line, component_item_revision=component, quantity='1.000000', unit='EA')
    requirement = OPCNodeDocumentRequirement.objects.create(node=op, document_revision=document_revision, purpose=OPCNodeDocumentRequirement.PURPOSE_WORK_INSTRUCTION, mandatory=True)
    diagram.manufacturing_bom_revision = mbom_revision
    diagram.save(update_fields=['manufacturing_bom_revision'])
    return admin, diagram, nodes, edges, {
        'plant': plant,
        'work_center': work_center,
        'machine': machine,
        'process': process,
        'tooling': tooling,
        'component': component,
        'mbom_revision': mbom_revision,
        'bom_line': bom_line,
        'document_revision': document_revision,
        'allocation': allocation,
        'requirement': requirement,
    }


def test_phase6_release_validation_requires_mbom_allocation_and_work_instruction():
    _admin, diagram, nodes, _edges, refs = make_phase6_ready('P-P6-VAL')
    assert validate_opc_release_readiness(diagram, mode='release')['release_ready'] is True

    diagram.manufacturing_bom_revision = None
    diagram.save(update_fields=['manufacturing_bom_revision'])
    result = validate_opc_release_readiness(diagram, mode='release')
    assert 'missing_manufacturing_bom' in {issue['code'] for issue in result['issues']}

    diagram.manufacturing_bom_revision = refs['mbom_revision']
    diagram.save(update_fields=['manufacturing_bom_revision'])
    refs['allocation'].delete()
    result = validate_opc_release_readiness(diagram, mode='release')
    assert 'mbom_line_unallocated' in {issue['code'] for issue in result['issues']}

    OPCOperationMaterialAllocation.objects.create(node=nodes[1], bom_line=refs['bom_line'], component_item_revision=refs['component'], quantity='2.000000', unit='EA')
    result = validate_opc_release_readiness(diagram, mode='release')
    assert 'mbom_line_overallocated' in {issue['code'] for issue in result['issues']}

    OPCOperationMaterialAllocation.objects.all().delete()
    OPCOperationMaterialAllocation.objects.create(node=nodes[1], bom_line=refs['bom_line'], component_item_revision=refs['component'], quantity='1.000000', unit='EA')
    refs['requirement'].delete()
    result = validate_opc_release_readiness(diagram, mode='release')
    assert 'missing_mandatory_work_instruction' in {issue['code'] for issue in result['issues']}


def test_atomic_graph_save_persists_material_and_document_snapshots_and_increments_once():
    admin, diagram, nodes, edges, refs = make_phase6_ready('P-P6-GRAPH')
    payload = graph_payload(diagram, nodes, edges)
    add_assignment(payload, 1, refs['plant'], refs['work_center'], refs['machine'], refs['process'], refs['tooling'])
    payload['diagram']['manufacturing_bom_revision'] = str(refs['mbom_revision'].pk)
    payload['nodes'][1]['material_allocations'] = [{
        'bom_line': str(refs['bom_line'].pk),
        'component_item_revision': str(refs['component'].pk),
        'quantity': '1.000000',
        'unit': 'EA',
        'scrap_percent': '0.000',
        'allocation_type': 'CONSUME',
        'issue_at_operation': True,
        'backflush': False,
        'sequence': 1,
        'notes': 'issue here',
    }]
    payload['nodes'][1]['document_requirements'] = [{
        'document_revision': str(refs['document_revision'].pk),
        'purpose': 'WORK_INSTRUCTION',
        'mandatory': True,
        'sequence': 1,
        'notes': 'use exact revision',
    }]

    response = api(admin).put(reverse('opc-diagram-graph', kwargs={'pk': diagram.pk}), payload, format='json')

    assert response.status_code == status.HTTP_200_OK, response.data
    diagram.refresh_from_db()
    assert diagram.graph_version == 1
    op = diagram.nodes.get(label='Cut')
    assert op.material_allocations.get().notes == 'issue here'
    assert op.document_requirements.get().document_revision == refs['document_revision']

    payload = graph_payload(diagram, list(diagram.nodes.all()), list(diagram.edges.all()))
    payload['diagram']['manufacturing_bom_revision'] = str(refs['mbom_revision'].pk)
    payload['nodes'][1]['material_allocations'] = []
    payload['nodes'][1]['document_requirements'] = []
    response = api(admin).put(reverse('opc-diagram-graph', kwargs={'pk': diagram.pk}), payload, format='json')
    assert response.status_code == status.HTTP_200_OK, response.data
    assert not op.material_allocations.exists()
    assert not op.document_requirements.exists()


def test_released_opc_evidence_contains_phase6_reference_issues_and_release_succeeds():
    admin, diagram, _nodes, _edges, refs = make_phase6_ready('P-P6-REL', status_value=OPCDiagram.STATUS_APPROVED)

    released = release_opc_revision(diagram, actor=admin, expected_graph_version=diagram.graph_version)

    assert released.status == OPCDiagram.STATUS_RELEASED
    evidence = released.validation_evidence.get()
    assert evidence.graph_version == diagram.graph_version
    assert refs['mbom_revision'].pk == released.manufacturing_bom_revision_id


def test_controlled_document_api_records_metadata_downloads_and_blocks_locked_mutation():
    admin = make_company_admin()
    client = api(admin)
    doc = ControlledDocument.objects.create(document_number='DOC-P6', title='Doc P6', document_type=ControlledDocument.TYPE_DRAWING)
    response = client.post(reverse('opc-document-revision-list'), {
        'document': str(doc.pk),
        'revision': 'A',
        'effective_from': '2026-09-01',
        'file': ContentFile(b'drawing bytes', name='drawing.pdf'),
    }, format='multipart')
    assert response.status_code == status.HTTP_201_CREATED, response.data
    revision_id = response.data['id']
    revision = ControlledDocumentRevision.objects.get(pk=revision_id)
    assert revision.checksum_sha256
    assert revision.file_size == len(b'drawing bytes')

    revision.status = ControlledDocumentRevision.STATUS_APPROVED
    revision.save(update_fields=['status'])
    release = client.post(reverse('opc-document-revision-release', kwargs={'pk': revision.pk}), {'effective_from': '2026-09-01'}, format='json')
    assert release.status_code == status.HTTP_200_OK, release.data
    assert client.get(reverse('opc-document-revision-download', kwargs={'pk': revision.pk})).status_code == status.HTTP_200_OK
    patch = client.patch(reverse('opc-document-revision-detail', kwargs={'pk': revision.pk}), {'revision': 'B'}, format='json')
    assert patch.status_code == status.HTTP_400_BAD_REQUEST


def test_change_control_lifecycle_links_and_deterministic_impact_analysis():
    admin, diagram, _nodes, _edges, refs = make_phase6_ready('P-P6-CHG')
    client = api(admin)
    ecr = client.post(reverse('opc-change-request-list'), {'change_number': 'ECR-P6', 'title': 'Change request'}, format='json')
    assert ecr.status_code == status.HTTP_201_CREATED, ecr.data
    assert client.post(reverse('opc-change-request-submit', kwargs={'pk': ecr.data['id']})).status_code == status.HTTP_200_OK
    assert client.post(reverse('opc-change-request-accept', kwargs={'pk': ecr.data['id']})).status_code == status.HTTP_200_OK
    eco = client.post(reverse('opc-change-order-list'), {'change_number': 'ECO-P6', 'request': ecr.data['id'], 'title': 'Change order'}, format='json')
    assert eco.status_code == status.HTTP_201_CREATED, eco.data
    assert client.post(reverse('opc-change-order-submit', kwargs={'pk': eco.data['id']})).status_code == status.HTTP_200_OK
    assert client.post(reverse('opc-change-order-approve', kwargs={'pk': eco.data['id']})).status_code == status.HTTP_200_OK
    link = client.post(reverse('opc-change-object-link-list'), {
        'order': eco.data['id'],
        'object_type': EngineeringChangeObjectLink.OBJECT_BOM_REVISION,
        'object_id': str(refs['mbom_revision'].pk),
        'role': EngineeringChangeObjectLink.ROLE_AFFECTED,
    }, format='json')
    assert link.status_code == status.HTTP_201_CREATED, link.data
    impact = client.get(reverse('opc-impact-analysis-list'), {'object_type': EngineeringChangeObjectLink.OBJECT_BOM_REVISION, 'object_id': str(refs['mbom_revision'].pk)})
    assert impact.status_code == status.HTTP_200_OK, impact.data
    assert impact.data['direct'][0]['change_number'] == 'ECO-P6'
    assert any(row['object_id'] == str(diagram.pk) for row in impact.data['transitive'])
