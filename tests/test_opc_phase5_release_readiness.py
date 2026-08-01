from __future__ import annotations

from datetime import date

import pytest
from django.core.files.base import ContentFile
from django.urls import reverse
from rest_framework import status

from enterprise_items.models import BOM, BOMLine, BOMRevision, ItemRevision
from opc.models import ControlledDocument, ControlledDocumentRevision, MachineAsset, OPCDiagram, OPCEdge, OPCNode, OPCNodeDocumentRequirement, OPCOperationMaterialAllocation, OPCValidationEvidence, Plant, ProcessDefinition, ToolingDefinition, WorkCenter
from opc.release_validation import POLICY_VERSION, validate_opc_release_readiness
from tests.factories import make_company_admin
from tests.test_opc_graph_persistence_phase3 import api, make_diagram, make_item_revision


pytestmark = pytest.mark.django_db


def make_structured_diagram(code='P-P5-READY', *, status_value=OPCDiagram.STATUS_DRAFT):
    suffix = code[-8:].replace('-', '')
    plant = Plant.objects.create(code=f'P{suffix}', name=f'Plant {suffix}')
    work_center = WorkCenter.objects.create(plant=plant, code=f'W{suffix}', name=f'Work center {suffix}')
    machine = MachineAsset.objects.create(plant=plant, work_center=work_center, asset_code=f'M{suffix}', name=f'Machine {suffix}')
    process = ProcessDefinition.objects.create(process_code=f'C{suffix}', name=f'Cut {suffix}', execution_classification='INTERNAL')
    external_process = ProcessDefinition.objects.create(process_code=f'O{suffix}', name=f'Outsource {suffix}', execution_classification='EXTERNAL')
    tooling = ToolingDefinition.objects.create(tooling_code=f'T{suffix}', name=f'Tooling {suffix}')
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
    mbom = BOM.objects.create(parent_item_revision=diagram.item_revision, bom_type=BOM.TYPE_MANUFACTURING)
    mbom_revision = BOMRevision.objects.create(bom=mbom, revision='M0', status=BOMRevision.STATUS_RELEASED, effective_from=diagram.effective_from)
    line = BOMLine.objects.create(bom_revision=mbom_revision, sequence=10, component_item_revision=component, quantity='1.000000', unit='EA')
    document = ControlledDocument.objects.create(document_number=f'WI-{suffix}', title=f'Work instruction {suffix}', document_type=ControlledDocument.TYPE_WORK_INSTRUCTION)
    document_revision = ControlledDocumentRevision.objects.create(document=document, revision='A', status=ControlledDocumentRevision.STATUS_RELEASED, effective_from=diagram.effective_from, file=ContentFile(b'work instruction', name='wi.pdf'))
    OPCOperationMaterialAllocation.objects.create(node=op, bom_line=line, component_item_revision=component, quantity='1.000000', unit='EA')
    OPCNodeDocumentRequirement.objects.create(node=op, document_revision=document_revision, purpose=OPCNodeDocumentRequirement.PURPOSE_WORK_INSTRUCTION, mandatory=True)
    diagram.manufacturing_bom_revision = mbom_revision
    diagram.save(update_fields=['manufacturing_bom_revision'])
    return diagram, nodes, edges, {
        'plant': plant,
        'work_center': work_center,
        'machine': machine,
        'process': process,
        'external_process': external_process,
        'tooling': tooling,
        'mbom_revision': mbom_revision,
        'bom_line': line,
        'document_revision': document_revision,
    }


def issue_codes(result):
    return {issue['code'] for issue in result['issues']}


def test_release_validation_contract_is_structured_and_deterministic():
    diagram, _nodes, _edges, _resources = make_structured_diagram()

    first = validate_opc_release_readiness(diagram, mode='release')
    second = validate_opc_release_readiness(diagram, mode='release')

    assert first['diagram_id'] == str(diagram.pk)
    assert first['graph_version'] == 0
    assert first['policy_version'] == POLICY_VERSION
    assert first['valid'] is True
    assert first['release_ready'] is True
    assert first['counts'] == second['counts']
    assert first['issues'] == second['issues']
    assert first['errors'] == {}
    assert first['warnings'] == []
    assert first['compatibility']['errors'] == {}


def test_release_validation_reports_required_structured_assignments():
    diagram, nodes, _edges = make_diagram('P-P5-MISSING')
    op = nodes[1]
    op.process_code = ''
    op.station = ''
    op.save(update_fields=['process_code', 'station'])

    result = validate_opc_release_readiness(diagram, mode='release')

    assert result['valid'] is False
    assert result['release_ready'] is False
    assert {'missing_operation_number', 'missing_process_definition', 'missing_plant', 'missing_work_center'} <= issue_codes(result)
    assert result['counts']['blocking_error_count'] >= 4


def test_topology_validation_detects_isolated_nodes_cycles_and_valid_rework_exit():
    diagram, nodes, _edges, _resources = make_structured_diagram('P-P5-TOPO')
    isolated = OPCNode.objects.create(diagram=diagram, node_type='OPERATION', label='Unconnected polish', sequence=4)
    result = validate_opc_release_readiness(diagram, mode='release')
    assert 'isolated_node' in issue_codes(result)
    assert any(issue['target_id'] == str(isolated.pk) for issue in result['issues'])

    isolated.delete()
    OPCEdge.objects.create(diagram=diagram, source=nodes[2], target=nodes[0], sequence=99)
    result = validate_opc_release_readiness(diagram, mode='release')
    assert 'invalid_cycle' in issue_codes(result)
    assert 'cycle' in result['compatibility']['errors']

    diagram, nodes, edges, resources = make_structured_diagram('P-P5-REWORK')
    edges[1].delete()
    inspection = OPCNode.objects.create(
        diagram=diagram,
        node_type='INSPECTION',
        label='Check',
        sequence=3,
        operation_number=20,
        process_definition=resources['process'],
        plant=resources['plant'],
        work_center=resources['work_center'],
    )
    nodes[2].sequence = 4
    nodes[2].save(update_fields=['sequence'])
    OPCEdge.objects.create(diagram=diagram, source=nodes[1], target=inspection, sequence=2)
    OPCEdge.objects.create(diagram=diagram, source=inspection, target=nodes[2], sequence=3)
    OPCEdge.objects.create(diagram=diagram, source=inspection, target=nodes[1], edge_type='REWORK', sequence=4)
    result = validate_opc_release_readiness(diagram, mode='release')
    assert 'invalid_cycle' not in issue_codes(result)
    assert 'rework_has_no_exit' not in issue_codes(result)

    OPCEdge.objects.get(diagram=diagram, source=inspection, target=nodes[2]).delete()
    result = validate_opc_release_readiness(diagram, mode='release')
    assert 'rework_has_no_exit' in issue_codes(result)


def test_backend_authoritative_warning_acknowledgement_is_graph_and_policy_bound():
    diagram, nodes, _edges, resources = make_structured_diagram('P-P5-ACK')
    op = nodes[1]
    op.execution_type = 'EXTERNAL'
    op.process_definition = resources['external_process']
    op.plant = None
    op.work_center = None
    op.machine_asset = None
    op.external_lead_time_days = 0
    op.save()

    result = validate_opc_release_readiness(diagram, mode='release')
    warning = next(issue for issue in result['issues'] if issue['code'] == 'external_missing_lead_time')
    assert result['valid'] is True
    assert result['release_ready'] is False
    assert warning['acknowledgeable'] is True
    assert warning['acknowledgement_required'] is True

    acknowledged = validate_opc_release_readiness(diagram, mode='release', acknowledged_issue_keys=[warning['issue_key']])
    assert acknowledged['release_ready'] is True
    assert acknowledged['acknowledged_warning_codes'] == ['external_missing_lead_time']

    diagram.graph_version = 1
    diagram.save(update_fields=['graph_version'])
    stale_ack = validate_opc_release_readiness(diagram, mode='release', acknowledged_issue_keys=[warning['issue_key']])
    assert stale_ack['release_ready'] is False
    assert 'unknown_warning_acknowledgement' in issue_codes(stale_ack)


def test_release_revalidates_current_version_and_stores_validation_evidence():
    admin = make_company_admin()
    diagram, _nodes, _edges, _resources = make_structured_diagram('P-P5-EVID', status_value=OPCDiagram.STATUS_APPROVED)

    response = api(admin).post(
        reverse('opc-diagram-release', kwargs={'pk': diagram.pk}),
        {
            'effective_from': str(diagram.effective_from),
            'expected_graph_version': diagram.graph_version,
            'validation_policy_version': POLICY_VERSION,
            'acknowledged_issue_keys': [],
        },
        format='json',
    )

    assert response.status_code == status.HTTP_200_OK, response.data
    diagram.refresh_from_db()
    assert diagram.status == OPCDiagram.STATUS_RELEASED
    evidence = OPCValidationEvidence.objects.get(diagram=diagram)
    assert evidence.graph_version == 0
    assert evidence.policy_version == POLICY_VERSION
    assert evidence.release_ready is True
    assert response.data['latest_validation_evidence']['id'] == str(evidence.pk)


def test_release_rejects_stale_validation_version_with_409():
    admin = make_company_admin()
    diagram, _nodes, _edges, _resources = make_structured_diagram('P-P5-STALE', status_value=OPCDiagram.STATUS_APPROVED)
    diagram.graph_version = 2
    diagram.save(update_fields=['graph_version'])

    response = api(admin).post(
        reverse('opc-diagram-release', kwargs={'pk': diagram.pk}),
        {
            'effective_from': str(date(2026, 9, 1)),
            'expected_graph_version': 1,
            'validation_policy_version': POLICY_VERSION,
            'acknowledged_issue_keys': [],
        },
        format='json',
    )

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.data['code'] == 'validation_graph_version_conflict'
    assert not OPCValidationEvidence.objects.filter(diagram=diagram).exists()
