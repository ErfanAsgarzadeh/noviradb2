from __future__ import annotations

import pytest
from django.db.models import ProtectedError
from django.urls import reverse
from rest_framework import status

from auditlog.models import AuditEvent
from opc.graph_persistence import save_opc_graph
from opc.models import MachineAsset, OPCDiagram, OPCNode, OPCToolingRequirement, Plant, ProcessDefinition, ToolingDefinition, WorkCenter
from opc.services import validate_opc_graph
from tests.factories import make_company_admin
from tests.test_opc_graph_persistence_phase3 import api, graph_payload, make_diagram


pytestmark = pytest.mark.django_db


def make_resources(*, inactive=False):
    plant = Plant.objects.create(code='PLT-A', name='Plant A', active=not inactive)
    work_center = WorkCenter.objects.create(plant=plant, code='WC-A', name='Machining', active=not inactive)
    machine = MachineAsset.objects.create(plant=plant, work_center=work_center, asset_code='MC-A', name='Lathe A', active=not inactive)
    process = ProcessDefinition.objects.create(process_code='CUT', name='Cutting', execution_classification='INTERNAL', active=not inactive)
    external_process = ProcessDefinition.objects.create(process_code='OSP', name='Outside Process', execution_classification='EXTERNAL')
    tooling = ToolingDefinition.objects.create(tooling_code='FIX-A', name='Fixture A', category='FIXTURE', active=not inactive)
    return plant, work_center, machine, process, external_process, tooling


def add_assignment(payload, index, plant, work_center, machine, process, tooling, **overrides):
    payload['nodes'][index].update({
        'operation_number': 10,
        'process_definition': str(process.pk) if process else None,
        'plant': str(plant.pk) if plant else None,
        'work_center': str(work_center.pk) if work_center else None,
        'machine_asset': str(machine.pk) if machine else None,
        'tooling_requirements': [
            {
                'tooling_definition': str(tooling.pk),
                'quantity': '1.000',
                'mandatory': True,
                'notes': 'Clamp',
                'sequence': 1,
            }
        ] if tooling else [],
        **overrides,
    })
    return payload


def test_master_data_models_preserve_codes_and_protect_referenced_rows():
    plant, work_center, machine, process, _external, tooling = make_resources()
    diagram, (_start, op, _out), _edges = make_diagram('P-P4-MODEL')
    op.operation_number = 10
    op.process_definition = process
    op.plant = plant
    op.work_center = work_center
    op.machine_asset = machine
    op.save()
    OPCToolingRequirement.objects.create(node=op, tooling_definition=tooling, quantity='2.000')

    assert plant.code == 'PLT-A'
    assert work_center.plant == plant
    assert machine.work_center == work_center
    with pytest.raises(ProtectedError):
        process.delete()
    with pytest.raises(ProtectedError):
        tooling.delete()
    with pytest.raises(ProtectedError):
        plant.delete()


def test_reference_apis_require_authentication_and_filter_active_and_dependencies():
    admin = make_company_admin()
    plant, work_center, machine, process, _external, tooling = make_resources()
    inactive = Plant.objects.create(code='PLT-Z', name='Inactive', active=False)

    assert api().get(reverse('opc-plant-list')).status_code in {status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN}
    plant_response = api(admin).get(reverse('opc-plant-list'))
    assert plant_response.status_code == status.HTTP_200_OK
    assert [row['code'] for row in plant_response.data] == ['PLT-A']
    all_response = api(admin).get(reverse('opc-plant-list'), {'active': 'all'})
    assert {row['id'] for row in all_response.data} == {str(plant.pk), str(inactive.pk)}

    wc_response = api(admin).get(reverse('opc-work-center-list'), {'plant': str(plant.pk)})
    assert [row['id'] for row in wc_response.data] == [str(work_center.pk)]
    machine_response = api(admin).get(reverse('opc-machine-asset-list'), {'work_center': str(work_center.pk)})
    assert [row['id'] for row in machine_response.data] == [str(machine.pk)]
    process_response = api(admin).get(reverse('opc-process-definition-list'), {'execution': 'INTERNAL', 'search': 'CUT'})
    assert [row['id'] for row in process_response.data] == [str(process.pk)]
    tooling_response = api(admin).get(reverse('opc-tooling-definition-list'), {'category': 'FIXTURE'})
    assert [row['id'] for row in tooling_response.data] == [str(tooling.pk)]


def test_graph_save_persists_assignments_tooling_response_and_audit_counts():
    admin = make_company_admin()
    plant, work_center, machine, process, _external, tooling = make_resources()
    diagram, nodes, edges = make_diagram('P-P4-SAVE')
    payload = graph_payload(diagram, nodes, edges)
    add_assignment(payload, 1, plant, work_center, machine, process, tooling)

    response = api(admin).put(reverse('opc-diagram-graph', kwargs={'pk': diagram.pk}), payload, format='json')

    assert response.status_code == status.HTTP_200_OK, response.data
    op = OPCNode.objects.get(pk=nodes[1].pk)
    assert op.operation_number == 10
    assert op.process_definition == process
    assert op.plant == plant
    assert op.work_center == work_center
    assert op.machine_asset == machine
    requirement = op.tooling_requirements.get()
    assert requirement.tooling_definition == tooling
    assert requirement.notes == 'Clamp'
    response_node = next(row for row in response.data['diagram']['nodes'] if row['id'] == str(op.pk))
    assert response_node['process_code_structured'] == 'CUT'
    assert response_node['plant_code'] == 'PLT-A'
    assert response_node['work_center_code'] == 'WC-A'
    assert response_node['machine_asset_code'] == 'MC-A'
    assert response_node['tooling_requirements'][0]['tooling_code'] == 'FIX-A'
    diagram.refresh_from_db()
    assert diagram.graph_version == 1
    audit = AuditEvent.objects.get(action='opc_graph_saved', target_id=str(diagram.pk))
    assert audit.extra['process_assignments_changed'] == 1
    assert audit.extra['tooling_requirements_created'] == 1


@pytest.mark.parametrize(
    'mutator, expected',
    [
        (lambda p, r: p['nodes'][1].update({'machine_asset': str(r['other_machine'].pk)}), 'machine_asset'),
        (lambda p, r: p['nodes'][1].update({'process_definition': str(r['external_process'].pk)}), 'process_definition'),
        (lambda p, r: p['nodes'][1].update({'execution_type': 'EXTERNAL'}), 'process_definition'),
        (lambda p, r: p['nodes'][2].update({'operation_number': 10}), 'operation_number'),
        (lambda p, r: p['nodes'][1]['tooling_requirements'].append(dict(p['nodes'][1]['tooling_requirements'][0])), 'tooling_requirements'),
        (lambda p, r: p['nodes'][1]['tooling_requirements'][0].update({'quantity': '0'}), 'tooling_requirements'),
        (lambda p, r: p['nodes'][1].update({'plant': str(r['inactive_plant'].pk)}), 'plant'),
    ],
)
def test_graph_save_rejects_invalid_assignments_before_mutation(mutator, expected):
    admin = make_company_admin()
    plant, work_center, machine, process, external_process, tooling = make_resources()
    other_plant = Plant.objects.create(code='PLT-B', name='Plant B')
    other_wc = WorkCenter.objects.create(plant=other_plant, code='WC-B', name='Assembly')
    other_machine = MachineAsset.objects.create(plant=other_plant, work_center=other_wc, asset_code='MC-B', name='Press B')
    inactive_plant = Plant.objects.create(code='PLT-X', name='Inactive Plant', active=False)
    diagram, nodes, edges = make_diagram('P-P4-VAL')
    payload = graph_payload(diagram, nodes, edges)
    add_assignment(payload, 1, plant, work_center, machine, process, tooling)
    mutator(payload, {'other_machine': other_machine, 'external_process': external_process, 'inactive_plant': inactive_plant})

    response = api(admin).put(reverse('opc-diagram-graph', kwargs={'pk': diagram.pk}), payload, format='json')

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert expected in str(response.data)
    diagram.refresh_from_db()
    assert diagram.graph_version == 0
    assert not OPCToolingRequirement.objects.filter(node=nodes[1]).exists()


def test_graph_save_preserves_inactive_historical_assignment_and_can_clear_snapshot():
    admin = make_company_admin()
    plant, work_center, machine, process, _external, tooling = make_resources(inactive=True)
    diagram, nodes, edges = make_diagram('P-P4-HIST')
    op = nodes[1]
    op.operation_number = 10
    op.process_definition = process
    op.plant = plant
    op.work_center = work_center
    op.machine_asset = machine
    op.save()
    OPCToolingRequirement.objects.create(node=op, tooling_definition=tooling, quantity='1.000')
    payload = graph_payload(diagram, nodes, edges)
    add_assignment(payload, 1, plant, work_center, machine, process, tooling)

    response = api(admin).put(reverse('opc-diagram-graph', kwargs={'pk': diagram.pk}), payload, format='json')
    assert response.status_code == status.HTTP_200_OK, response.data

    diagram.refresh_from_db()
    payload = graph_payload(diagram, list(diagram.nodes.all()), list(diagram.edges.all()))
    payload['nodes'][1].update({
        'operation_number': None,
        'process_definition': None,
        'plant': None,
        'work_center': None,
        'machine_asset': None,
        'tooling_requirements': [],
    })
    response = api(admin).put(reverse('opc-diagram-graph', kwargs={'pk': diagram.pk}), payload, format='json')
    assert response.status_code == status.HTTP_200_OK, response.data
    op.refresh_from_db()
    assert op.process_definition_id is None
    assert not OPCToolingRequirement.objects.filter(node=op).exists()


def test_temporary_node_supports_assignments_and_tooling_in_one_graph_save():
    admin = make_company_admin()
    plant, work_center, machine, process, _external, tooling = make_resources()
    diagram, (start, _op, out), (first, _second) = make_diagram('P-P4-TEMP')
    payload = graph_payload(diagram, [start, out], [first])
    payload['edges'][0]['target'] = 'tmp-op'
    payload['nodes'].append({
        'client_id': 'tmp-op',
        'type': 'OPERATION',
        'label': 'Temporary structured op',
        'part_code': diagram.part_code,
        'process_code': 'LEG-CUT',
        'station': 'Legacy cell',
        'execution_type': 'INTERNAL',
        'operation_number': 20,
        'process_definition': str(process.pk),
        'plant': str(plant.pk),
        'work_center': str(work_center.pk),
        'machine_asset': str(machine.pk),
        'tooling_requirements': [{'tooling_definition': str(tooling.pk), 'quantity': '1', 'mandatory': True, 'notes': '', 'sequence': 1}],
        'sequence': 4,
        'x': 700,
        'y': 160,
    })
    payload['edges'].append({'client_id': 'tmp-edge-2', 'source': 'tmp-op', 'target': str(out.pk), 'type': 'FLOW', 'label': '', 'sequence': 2})

    result = api(admin).put(reverse('opc-diagram-graph', kwargs={'pk': diagram.pk}), payload, format='json')

    assert result.status_code == status.HTTP_200_OK, result.data
    created = OPCNode.objects.get(pk=result.data['node_id_map']['tmp-op'])
    assert created.operation_number == 20
    assert created.process_code == 'LEG-CUT'
    assert created.tooling_requirements.count() == 1


def test_validation_reports_structured_errors_and_legacy_warnings_without_requiring_mapping():
    _plant, _work_center, _machine, process, external_process, _tooling = make_resources()
    diagram, nodes, _edges = make_diagram('P-P4-CHECK')
    legacy_op = nodes[1]
    legacy_op.process_code = 'LEGACY-OP'
    legacy_op.station = 'Legacy Station'
    legacy_op.save()
    result = validate_opc_graph(diagram)
    assert any('legacy process/station' in warning for warning in result['warnings'])

    legacy_op.process_definition = external_process
    legacy_op.execution_type = 'INTERNAL'
    legacy_op.save()
    result = validate_opc_graph(diagram)
    assert any('process' in key for key in result['errors'])
