from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier, Event
import uuid

import pytest
from django.db import close_old_connections, connection
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from auditlog.models import AuditEvent
from enterprise_items.models import CodingOrganization, Item, ItemRevision, ItemType
from opc.graph_persistence import OPCGraphConflict, save_opc_graph
from opc.models import OPCDiagram, OPCEdge, OPCNode
from tests.factories import make_company_admin, make_member


pytestmark = pytest.mark.django_db


def api(user=None):
    client = APIClient()
    if user:
        client.force_authenticate(user=user)
    return client


def make_item_revision(code='P-GRAPH-001', revision='A', *, status_value=ItemRevision.STATUS_APPROVED):
    org = CodingOrganization.objects.create(name=f'Org {code}', code=f'O{code[-4:]}')
    item_type = ItemType.objects.create(organization=org, name='Part', code=f'T{code[-4:]}', group='PART')
    item = Item.objects.create(
        organization=org,
        item_code=code,
        name=f'Part {code}',
        item_type=item_type,
        base_unit='EA',
        status='ACTIVE',
        make_or_buy='MAKE',
    )
    return ItemRevision.objects.create(item=item, revision=revision, status=status_value, effective_from=date(2026, 8, 1), title=f'{code} {revision}')


def make_diagram(code='P-GRAPH-001'):
    item_revision = make_item_revision(code)
    diagram = OPCDiagram.objects.create(
        item_revision=item_revision,
        title=f'OPC {code}',
        part_code=code,
        part_name=f'Part {code}',
        revision='R0',
        effective_from=date(2026, 9, 1),
    )
    start = OPCNode.objects.create(diagram=diagram, node_type='MATERIAL', label='Material', sequence=1, x=100, y=120)
    op = OPCNode.objects.create(diagram=diagram, node_type='OPERATION', label='Cut', process_code='OP10', sequence=2, x=300, y=120)
    out = OPCNode.objects.create(diagram=diagram, node_type='OUTPUT', label='Output', sequence=3, x=500, y=120)
    first = OPCEdge.objects.create(diagram=diagram, source=start, target=op, sequence=1)
    second = OPCEdge.objects.create(diagram=diagram, source=op, target=out, sequence=2)
    return diagram, (start, op, out), (first, second)


def node_payload(node, **overrides):
    payload = {
        'id': str(node.pk),
        'type': node.node_type,
        'label': node.label,
        'part_code': node.part_code,
        'process_code': node.process_code,
        'station': node.station,
        'execution_type': node.execution_type,
        'setup_time_hours': str(node.setup_time_hours),
        'run_time_per_unit_hours': str(node.run_time_per_unit_hours),
        'queue_time_hours': str(node.queue_time_hours),
        'move_time_hours': str(node.move_time_hours),
        'inspection_time_hours': str(node.inspection_time_hours),
        'external_lead_time_days': str(node.external_lead_time_days),
        'buffer_time_hours': str(node.buffer_time_hours),
        'description': node.description,
        'sequence': node.sequence,
        'x': node.x,
        'y': node.y,
    }
    payload.update(overrides)
    return payload


def edge_payload(edge, **overrides):
    payload = {
        'id': str(edge.pk),
        'source': str(edge.source_id),
        'target': str(edge.target_id),
        'type': edge.edge_type,
        'label': edge.label,
        'sequence': edge.sequence,
    }
    payload.update(overrides)
    return payload


def graph_payload(diagram, nodes, edges, **diagram_overrides):
    return {
        'expected_version': diagram.graph_version,
        'diagram': {
            'item_revision': str(diagram.item_revision_id) if diagram.item_revision_id else None,
            'title': diagram.title,
            'part_code': diagram.part_code,
            'part_name': diagram.part_name,
            'revision': diagram.revision,
            'description': diagram.description,
            'effective_from': str(diagram.effective_from) if diagram.effective_from else None,
            'effective_to': str(diagram.effective_to) if diagram.effective_to else None,
            **diagram_overrides,
        },
        'nodes': [node_payload(node) for node in nodes],
        'edges': [edge_payload(edge) for edge in edges],
    }


def test_graph_endpoint_atomically_updates_creates_deletes_maps_ids_and_audits():
    admin = make_company_admin()
    diagram, (start, op, out), (first, second) = make_diagram()
    payload = graph_payload(diagram, [start, op], [first], title='Changed OPC')
    payload['nodes'][1]['label'] = 'Cut revised'
    payload['nodes'][1]['x'] = 360
    payload['nodes'].append({
        'client_id': 'tmp-output',
        'type': 'OUTPUT',
        'label': 'New output',
        'part_code': diagram.part_code,
        'process_code': 'OUT-NEW',
        'station': '',
        'execution_type': 'INSPECTION',
        'setup_time_hours': None,
        'run_time_per_unit_hours': '0.000',
        'queue_time_hours': '0.000',
        'move_time_hours': '0.000',
        'inspection_time_hours': '0.000',
        'external_lead_time_days': '0.000',
        'buffer_time_hours': '0.000',
        'description': '',
        'sequence': 4,
        'x': 700,
        'y': 160,
    })
    payload['edges'].append({
        'client_id': 'tmp-edge',
        'source': str(op.pk),
        'target': 'tmp-output',
        'type': 'FLOW',
        'label': 'created',
        'sequence': 3,
    })

    response = api(admin).put(reverse('opc-diagram-graph', kwargs={'pk': diagram.pk}), payload, format='json')

    assert response.status_code == status.HTTP_200_OK, response.data
    diagram.refresh_from_db()
    assert diagram.title == 'Changed OPC'
    assert diagram.graph_version == 1
    assert response.data['graph_version'] == 1
    assert response.data['node_id_map']['tmp-output']
    assert response.data['edge_id_map']['tmp-edge']
    assert not OPCNode.objects.filter(pk=out.pk).exists()
    assert not OPCEdge.objects.filter(pk=second.pk).exists()
    assert OPCNode.objects.get(pk=op.pk).label == 'Cut revised'
    created_edge = OPCEdge.objects.get(pk=response.data['edge_id_map']['tmp-edge'])
    assert created_edge.target_id == OPCNode.objects.get(pk=response.data['node_id_map']['tmp-output']).pk
    audit = AuditEvent.objects.get(action='opc_graph_saved', target_id=str(diagram.pk))
    assert audit.extra['nodes_created'] == 1
    assert audit.extra['nodes_deleted'] == 1
    assert audit.extra['edges_created'] == 1
    assert audit.extra['edges_deleted'] == 1


def test_graph_endpoint_creates_new_diagram_with_client_supplied_uuid_in_one_request():
    admin = make_company_admin()
    diagram_id = 'aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa'
    item_revision = make_item_revision('P-GRAPH-NEW')
    payload = {
        'expected_version': 0,
        'diagram': {
            'item_revision': str(item_revision.pk),
            'title': 'New graph',
            'part_code': 'P-GRAPH-NEW',
            'part_name': 'New Part',
            'revision': 'R0',
            'description': '',
            'effective_from': None,
            'effective_to': None,
        },
        'nodes': [
            {'client_id': 'tmp-a', 'type': 'MATERIAL', 'label': 'Raw', 'execution_type': 'INTERNAL', 'sequence': 1, 'x': 100, 'y': 120},
            {'client_id': 'tmp-b', 'type': 'OUTPUT', 'label': 'Out', 'execution_type': 'INSPECTION', 'sequence': 2, 'x': 300, 'y': 120},
        ],
        'edges': [{'client_id': 'tmp-e', 'source': 'tmp-a', 'target': 'tmp-b', 'type': 'FLOW', 'label': '', 'sequence': 1}],
    }

    response = api(admin).put(reverse('opc-diagram-graph', kwargs={'pk': diagram_id}), payload, format='json')

    assert response.status_code == status.HTTP_200_OK, response.data
    assert response.data['diagram']['id'] == diagram_id
    assert response.data['graph_version'] == 1
    assert OPCDiagram.objects.get(pk=diagram_id).nodes.count() == 2


@pytest.mark.parametrize(
    ('mutator', 'expected_key'),
    [
        (lambda p: p.pop('expected_version'), 'expected_version'),
        (lambda p: p['nodes'].append({'client_id': p['nodes'][0]['id'], 'type': 'OPERATION', 'label': 'x'}), 'nodes'),
        (lambda p: p['nodes'].__setitem__(1, {**p['nodes'][1], 'id': p['nodes'][0]['id']}), 'nodes'),
        (lambda p: p['nodes'].__setitem__(0, {**p['nodes'][0], 'type': 'INVALID'}), 'nodes'),
        (lambda p: p['edges'].__setitem__(0, {**p['edges'][0], 'source': 'missing'}), 'edges'),
        (lambda p: p['edges'].__setitem__(0, {**p['edges'][0], 'target': p['edges'][0]['source']}), 'edges'),
        (lambda p: p['diagram'].__setitem__('item_revision', 'aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa'), 'diagram'),
    ],
)
def test_graph_endpoint_validates_before_mutation(mutator, expected_key):
    admin = make_company_admin()
    diagram, nodes, edges = make_diagram('P-GRAPH-VAL')
    payload = graph_payload(diagram, nodes, edges)
    mutator(payload)

    response = api(admin).put(reverse('opc-diagram-graph', kwargs={'pk': diagram.pk}), payload, format='json')

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert expected_key in response.data
    diagram.refresh_from_db()
    assert diagram.graph_version == 0
    assert OPCNode.objects.filter(diagram=diagram).count() == 3
    assert OPCEdge.objects.filter(diagram=diagram).count() == 2


def test_graph_endpoint_rejects_stale_version_with_409_and_preserves_database():
    admin = make_company_admin()
    diagram, nodes, edges = make_diagram('P-GRAPH-CON')
    diagram.graph_version = 2
    diagram.save(update_fields=['graph_version'])
    payload = graph_payload(diagram, nodes, edges)
    payload['expected_version'] = 1
    payload['diagram']['title'] = 'stale overwrite'

    response = api(admin).put(reverse('opc-diagram-graph', kwargs={'pk': diagram.pk}), payload, format='json')

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.data['code'] == 'graph_version_conflict'
    assert response.data['expected_version'] == 1
    assert response.data['current_version'] == 2
    diagram.refresh_from_db()
    assert diagram.title != 'stale overwrite'
    assert diagram.graph_version == 2


def test_graph_endpoint_blocks_unauthorized_and_locked_writes():
    diagram, nodes, edges = make_diagram('P-GRAPH-AUTH')
    payload = graph_payload(diagram, nodes, edges)
    assert api().put(reverse('opc-diagram-graph', kwargs={'pk': diagram.pk}), payload, format='json').status_code in {status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN}
    assert api(make_member()).put(reverse('opc-diagram-graph', kwargs={'pk': diagram.pk}), payload, format='json').status_code == status.HTTP_403_FORBIDDEN

    diagram.status = OPCDiagram.STATUS_RELEASED
    diagram.save(update_fields=['status'])
    response = api(make_company_admin()).put(reverse('opc-diagram-graph', kwargs={'pk': diagram.pk}), payload, format='json')
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.parametrize('fault_at', ['after_diagram_save', 'after_node_upsert', 'after_edge_delete', 'after_edge_upsert', 'after_node_delete', 'after_audit'])
def test_graph_service_rolls_back_every_mutation_stage(fault_at):
    admin = make_company_admin()
    diagram, nodes, edges = make_diagram(f'P-ROLL-{uuid.uuid4().hex[:8]}')
    payload = graph_payload(diagram, nodes, edges, title='Rollback title')
    payload['nodes'][1]['label'] = 'Rollback node'
    payload['edges'] = payload['edges'][:1]

    with pytest.raises(RuntimeError):
        save_opc_graph(diagram_id=str(diagram.pk), payload=payload, actor=admin, fault_at=fault_at)

    diagram.refresh_from_db()
    assert diagram.title != 'Rollback title'
    assert diagram.graph_version == 0
    assert OPCNode.objects.get(pk=nodes[1].pk).label != 'Rollback node'
    assert OPCEdge.objects.filter(diagram=diagram).count() == 2
    assert not AuditEvent.objects.filter(action='opc_graph_saved', target_id=str(diagram.pk)).exists()


def test_legacy_mutation_endpoints_increment_graph_version_but_validation_and_lifecycle_do_not():
    admin = make_company_admin()
    diagram, nodes, edges = make_diagram('P-GRAPH-LEG')
    client = api(admin)

    assert client.post(reverse('opc-diagram-validate', kwargs={'pk': diagram.pk})).status_code == status.HTTP_200_OK
    diagram.refresh_from_db()
    assert diagram.graph_version == 0

    assert client.patch(reverse('opc-node-detail', kwargs={'pk': nodes[0].pk}), {'label': 'Legacy node'}, format='json').status_code == status.HTTP_200_OK
    diagram.refresh_from_db()
    assert diagram.graph_version == 1

    assert client.patch(reverse('opc-edge-detail', kwargs={'pk': edges[0].pk}), {'label': 'Legacy edge'}, format='json').status_code == status.HTTP_200_OK
    diagram.refresh_from_db()
    assert diagram.graph_version == 2

    assert client.patch(reverse('opc-diagram-detail', kwargs={'pk': diagram.pk}), {'title': 'Legacy header'}, format='json').status_code == status.HTTP_200_OK
    diagram.refresh_from_db()
    assert diagram.graph_version == 3

    assert client.post(reverse('opc-diagram-submit', kwargs={'pk': diagram.pk})).status_code == status.HTTP_200_OK
    diagram.refresh_from_db()
    assert diagram.graph_version == 3


@pytest.mark.postgresql
@pytest.mark.concurrency
@pytest.mark.django_db(transaction=True)
def test_postgresql_same_version_concurrent_graph_save_allows_one_winner():
    if connection.vendor != 'postgresql':
        pytest.skip('PostgreSQL row-locking semantics required.')
    admin = make_company_admin()
    diagram, nodes, edges = make_diagram('P-GRAPH-PG1')
    payload_a = graph_payload(diagram, nodes, edges, title='winner a')
    payload_b = graph_payload(diagram, nodes, edges, title='winner b')
    start = Barrier(2)

    def run(payload):
        close_old_connections()
        start.wait()
        try:
            result = save_opc_graph(diagram_id=str(diagram.pk), payload=payload, actor=admin)
            return ('ok', result.diagram.title)
        except OPCGraphConflict as exc:
            return ('conflict', exc.payload['current_version'])
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, [payload_a, payload_b]))

    assert sorted(kind for kind, _value in results) == ['conflict', 'ok']
    diagram.refresh_from_db()
    assert diagram.graph_version == 1
    assert diagram.title in {'winner a', 'winner b'}


@pytest.mark.postgresql
@pytest.mark.concurrency
@pytest.mark.django_db(transaction=True)
def test_postgresql_delayed_transaction_rechecks_version_after_lock_release():
    if connection.vendor != 'postgresql':
        pytest.skip('PostgreSQL row-locking semantics required.')
    admin = make_company_admin()
    diagram, nodes, edges = make_diagram('P-GRAPH-PG2')
    payload_a = graph_payload(diagram, nodes, edges, title='delayed winner')
    payload_b = graph_payload(diagram, nodes, edges, title='delayed loser')
    lock_seen = Event()
    release_lock = Event()

    def winner():
        close_old_connections()
        try:
            return save_opc_graph(
                diagram_id=str(diagram.pk),
                payload=payload_a,
                actor=admin,
                lock_acquired=lambda: (lock_seen.set(), release_lock.wait(timeout=5)),
            ).diagram.title
        finally:
            close_old_connections()

    def loser():
        close_old_connections()
        lock_seen.wait(timeout=5)
        try:
            save_opc_graph(diagram_id=str(diagram.pk), payload=payload_b, actor=admin)
            return 'ok'
        except OPCGraphConflict:
            return 'conflict'
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(winner)
        second = pool.submit(loser)
        lock_seen.wait(timeout=5)
        release_lock.set()
        assert first.result(timeout=10) == 'delayed winner'
        assert second.result(timeout=10) == 'conflict'
    diagram.refresh_from_db()
    assert diagram.title == 'delayed winner'
    assert diagram.graph_version == 1


@pytest.mark.postgresql
@pytest.mark.concurrency
@pytest.mark.django_db(transaction=True)
def test_postgresql_independent_diagrams_can_save_concurrently_and_failure_releases_lock():
    if connection.vendor != 'postgresql':
        pytest.skip('PostgreSQL row-locking semantics required.')
    admin = make_company_admin()
    first_diagram, first_nodes, first_edges = make_diagram('P-GRAPH-PG3')
    second_diagram, second_nodes, second_edges = make_diagram('P-GRAPH-PG4')
    payload_first = graph_payload(first_diagram, first_nodes, first_edges, title='first ok')
    payload_second = graph_payload(second_diagram, second_nodes, second_edges, title='second ok')
    start = Barrier(2)

    def run(diagram, payload, fail=False):
        close_old_connections()
        start.wait()
        try:
            save_opc_graph(diagram_id=str(diagram.pk), payload=payload, actor=admin, fault_at='after_diagram_save' if fail else None)
            return 'ok'
        except RuntimeError:
            return 'failed'
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(run, [first_diagram, second_diagram], [payload_first, payload_second])) == ['ok', 'ok']
    first_diagram.refresh_from_db()
    second_diagram.refresh_from_db()
    assert first_diagram.graph_version == 1
    assert second_diagram.graph_version == 1

    failed_payload = graph_payload(first_diagram, list(first_diagram.nodes.all()), list(first_diagram.edges.all()), title='failed title')
    with pytest.raises(RuntimeError):
        save_opc_graph(diagram_id=str(first_diagram.pk), payload=failed_payload, actor=admin, fault_at='after_diagram_save')
    first_diagram.refresh_from_db()
    assert first_diagram.title == 'first ok'
    assert first_diagram.graph_version == 1

    retry_payload = graph_payload(first_diagram, list(first_diagram.nodes.all()), list(first_diagram.edges.all()), title='retry ok')
    save_opc_graph(diagram_id=str(first_diagram.pk), payload=retry_payload, actor=admin)
    first_diagram.refresh_from_db()
    assert first_diagram.title == 'retry ok'
    assert first_diagram.graph_version == 2
