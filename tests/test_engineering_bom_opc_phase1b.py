from datetime import date

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from auditlog.models import AuditEvent
from enterprise_items.bom_services import approve_bom_revision, release_bom_revision, submit_bom_revision
from enterprise_items.exceptions import EngineeringLifecycleError, EngineeringPermissionError
from enterprise_items.models import BOM, BOMLine, BOMRevision, CodingOrganization, Item, ItemRevision, ItemType
from enterprise_items.readiness import evaluate_item_revision_readiness
from opc.models import OPCDiagram, OPCEdge, OPCNode
from opc.services import approve_opc_revision, clone_opc_revision, release_opc_revision, submit_opc_revision, validate_opc_graph
from tests.factories import make_company_admin, make_member, make_project_manager

pytestmark = pytest.mark.django_db


def api(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def make_item(code, *, make_or_buy='MAKE'):
    org = CodingOrganization.objects.create(name=f'Org {code}', code=f'O{code[-4:]}')
    item_type = ItemType.objects.create(organization=org, name='Part', code=f'T{code[-4:]}', group='PART')
    return Item.objects.create(
        organization=org,
        item_code=code,
        name=f'Part {code}',
        item_type=item_type,
        base_unit='EA',
        status='ACTIVE',
        make_or_buy=make_or_buy,
    )


def make_item_revision(code, revision='A', *, status_value=ItemRevision.STATUS_RELEASED, make_or_buy='MAKE'):
    return ItemRevision.objects.create(
        item=make_item(code, make_or_buy=make_or_buy),
        revision=revision,
        status=status_value,
        effective_from=date(2026, 8, 1),
        title=f'{code} {revision}',
    )


def make_bom_revision(parent, revision='A', *, status_value=BOMRevision.STATUS_DRAFT, effective_from=date(2026, 9, 1)):
    bom = BOM.objects.create(parent_item_revision=parent, bom_type=BOM.TYPE_ENGINEERING)
    return BOMRevision.objects.create(bom=bom, revision=revision, status=status_value, effective_from=effective_from)


def add_line(bom_revision, component, sequence=10, quantity='1.000000'):
    return BOMLine.objects.create(
        bom_revision=bom_revision,
        sequence=sequence,
        component_item_revision=component,
        quantity=quantity,
        unit='EA',
    )


def make_valid_opc(item_revision, revision='A', *, status_value=OPCDiagram.STATUS_DRAFT, effective_from=date(2026, 9, 1)):
    diagram = OPCDiagram.objects.create(
        item_revision=item_revision,
        title=f'OPC {item_revision.item.item_code}',
        part_code=item_revision.item.item_code,
        part_name=item_revision.item.name,
        revision=revision,
        status=status_value,
        effective_from=effective_from,
    )
    start = OPCNode.objects.create(diagram=diagram, node_type='MATERIAL', label='Material', sequence=1)
    op = OPCNode.objects.create(diagram=diagram, node_type='OPERATION', label='Cut', process_code='OP10', sequence=2)
    out = OPCNode.objects.create(diagram=diagram, node_type='OUTPUT', label='Output', sequence=3)
    OPCEdge.objects.create(diagram=diagram, source=start, target=op, sequence=1)
    OPCEdge.objects.create(diagram=diagram, source=op, target=out, sequence=2)
    return diagram


class TestBOMLifecycle:
    def test_bom_release_and_audit(self):
        admin = make_company_admin()
        parent = make_item_revision('P-BOM-001', status_value=ItemRevision.STATUS_APPROVED)
        component = make_item_revision('P-BOM-002')
        revision = make_bom_revision(parent)
        add_line(revision, component)

        revision = submit_bom_revision(revision, actor=admin)
        assert revision.status == BOMRevision.STATUS_UNDER_REVIEW
        revision = approve_bom_revision(revision, actor=admin)
        assert revision.status == BOMRevision.STATUS_APPROVED
        revision = release_bom_revision(revision, actor=admin)

        assert revision.status == BOMRevision.STATUS_RELEASED
        assert revision.released_by == admin
        assert AuditEvent.objects.filter(action='bom_revision_released', target_id=str(revision.pk)).exists()

    def test_bom_rejects_invalid_component_and_quantities(self):
        parent = make_item_revision('P-BOM-010', status_value=ItemRevision.STATUS_APPROVED)
        draft_component = make_item_revision('P-BOM-011', status_value=ItemRevision.STATUS_DRAFT)
        revision = make_bom_revision(parent, status_value=BOMRevision.STATUS_APPROVED)
        add_line(revision, draft_component)

        with pytest.raises(EngineeringLifecycleError):
            release_bom_revision(revision, actor=make_company_admin())

        with pytest.raises(Exception):
            add_line(revision, parent, sequence=20)
        with pytest.raises(Exception):
            add_line(revision, make_item_revision('P-BOM-012'), sequence=30, quantity='0')

    def test_bom_supersession_closes_prior_revision(self):
        admin = make_company_admin()
        parent = make_item_revision('P-BOM-020', status_value=ItemRevision.STATUS_APPROVED)
        c1 = make_item_revision('P-BOM-021')
        c2 = make_item_revision('P-BOM-022')
        first = make_bom_revision(parent, 'A', status_value=BOMRevision.STATUS_APPROVED, effective_from=date(2026, 9, 1))
        add_line(first, c1)
        first = release_bom_revision(first, actor=admin)
        second = BOMRevision.objects.create(bom=first.bom, revision='B', status=BOMRevision.STATUS_APPROVED, effective_from=date(2026, 10, 1))
        add_line(second, c2)

        second = release_bom_revision(second, actor=admin, supersede_current=True)
        first.refresh_from_db()

        assert first.status == BOMRevision.STATUS_SUPERSEDED
        assert first.effective_to == date(2026, 9, 30)
        assert first.superseded_by == second
        assert second.status == BOMRevision.STATUS_RELEASED

    def test_bom_cycle_detection_multilevel(self):
        admin = make_company_admin()
        a = make_item_revision('P-BOM-030', status_value=ItemRevision.STATUS_RELEASED)
        b = make_item_revision('P-BOM-031')
        b_bom = make_bom_revision(b, status_value=BOMRevision.STATUS_APPROVED)
        add_line(b_bom, a)
        release_bom_revision(b_bom, actor=admin)

        a_bom = make_bom_revision(a, status_value=BOMRevision.STATUS_APPROVED)
        add_line(a_bom, b)

        with pytest.raises(EngineeringLifecycleError) as exc:
            release_bom_revision(a_bom, actor=admin)
        assert 'cycle' in exc.value.message_dict

    def test_bom_api_blocks_generic_status_patch_and_released_line_edit(self):
        admin = make_company_admin()
        parent = make_item_revision('P-BOM-040', status_value=ItemRevision.STATUS_APPROVED)
        component = make_item_revision('P-BOM-041')
        revision = make_bom_revision(parent)
        line = add_line(revision, component)
        response = api(admin).patch(reverse('bom-revision-detail', kwargs={'pk': revision.pk}), {'status': BOMRevision.STATUS_RELEASED}, format='json')
        revision.refresh_from_db()
        assert response.status_code == status.HTTP_200_OK
        assert revision.status == BOMRevision.STATUS_DRAFT

        revision.status = BOMRevision.STATUS_APPROVED
        revision.save(update_fields=['status'])
        release_bom_revision(revision, actor=admin)
        response = api(admin).patch(reverse('bom-line-detail', kwargs={'pk': line.pk}), {'quantity': '2.000000'}, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_bom_permissions(self):
        manager = make_project_manager()
        revision = make_bom_revision(make_item_revision('P-BOM-050', status_value=ItemRevision.STATUS_APPROVED))
        assert submit_bom_revision(revision, actor=manager).status == BOMRevision.STATUS_UNDER_REVIEW
        revision.status = BOMRevision.STATUS_APPROVED
        revision.save(update_fields=['status'])
        with pytest.raises(EngineeringPermissionError):
            release_bom_revision(revision, actor=manager)
        response = api(make_member()).post(reverse('bom-list'), {'parent_item_revision': str(revision.bom.parent_item_revision_id), 'bom_type': BOM.TYPE_ENGINEERING}, format='json')
        assert response.status_code == status.HTTP_403_FORBIDDEN


class TestOPCLifecycle:
    def test_opc_release_validate_clone_and_audit(self):
        admin = make_company_admin()
        item_revision = make_item_revision('P-OPC-001', status_value=ItemRevision.STATUS_APPROVED)
        diagram = make_valid_opc(item_revision)

        assert validate_opc_graph(diagram)['valid'] is True
        diagram = submit_opc_revision(diagram, actor=admin)
        diagram = approve_opc_revision(diagram, actor=admin)
        diagram = release_opc_revision(diagram, actor=admin)

        assert diagram.status == OPCDiagram.STATUS_RELEASED
        assert AuditEvent.objects.filter(action='opc_revision_released', target_id=str(diagram.pk)).exists()

        clone = clone_opc_revision(diagram, actor=admin, revision_code='B')
        assert clone.status == OPCDiagram.STATUS_DRAFT
        assert clone.released_at is None
        assert clone.nodes.count() == diagram.nodes.count()
        assert clone.edges.count() == diagram.edges.count()

    def test_opc_rejects_legacy_unassigned_release_and_cycles(self):
        admin = make_company_admin()
        legacy = OPCDiagram.objects.create(title='Legacy', part_code='LEG', revision='A', status=OPCDiagram.STATUS_APPROVED, effective_from=date(2026, 9, 1))
        with pytest.raises(EngineeringLifecycleError):
            release_opc_revision(legacy, actor=admin)

        item_revision = make_item_revision('P-OPC-010', status_value=ItemRevision.STATUS_APPROVED)
        diagram = make_valid_opc(item_revision, status_value=OPCDiagram.STATUS_APPROVED)
        nodes = list(diagram.nodes.order_by('sequence'))
        OPCEdge.objects.create(diagram=diagram, source=nodes[-1], target=nodes[0], sequence=99)
        result = validate_opc_graph(diagram)
        assert result['valid'] is False
        assert 'cycle' in result['errors']

    def test_opc_immutable_after_release_and_generic_status_patch_blocked(self):
        admin = make_company_admin()
        item_revision = make_item_revision('P-OPC-020', status_value=ItemRevision.STATUS_APPROVED)
        diagram = make_valid_opc(item_revision)
        response = api(admin).patch(reverse('opc-diagram-detail', kwargs={'pk': diagram.pk}), {'status': OPCDiagram.STATUS_RELEASED}, format='json')
        diagram.refresh_from_db()
        assert response.status_code == status.HTTP_200_OK
        assert diagram.status == OPCDiagram.STATUS_DRAFT

        diagram.status = OPCDiagram.STATUS_APPROVED
        diagram.save(update_fields=['status'])
        diagram = release_opc_revision(diagram, actor=admin)
        node = diagram.nodes.first()
        response = api(admin).patch(reverse('opc-node-detail', kwargs={'pk': node.pk}), {'label': 'Changed'}, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_opc_api_lifecycle_and_released_lookup(self):
        admin = make_company_admin()
        item_revision = make_item_revision('P-OPC-030', status_value=ItemRevision.STATUS_APPROVED)
        diagram = make_valid_opc(item_revision)
        client = api(admin)

        assert client.post(reverse('opc-diagram-submit', kwargs={'pk': diagram.pk})).status_code == status.HTTP_200_OK
        assert client.post(reverse('opc-diagram-approve', kwargs={'pk': diagram.pk})).status_code == status.HTTP_200_OK
        release = client.post(reverse('opc-diagram-release', kwargs={'pk': diagram.pk}))
        assert release.status_code == status.HTTP_200_OK
        released = client.get(reverse('opc-diagram-released'), {'item_revision': str(item_revision.pk)})
        assert released.status_code == status.HTTP_200_OK
        assert released.data['id'] == str(diagram.pk)


class TestEngineeringReadiness:
    def test_readiness_buy_and_make_rules(self):
        buy = make_item_revision('P-READY-001', make_or_buy='BUY')
        buy_result = evaluate_item_revision_readiness(buy)
        assert buy_result['release_ready'] is True
        assert buy_result['bom_ready'] is True

        make = make_item_revision('P-READY-002', make_or_buy='MAKE')
        make_result = evaluate_item_revision_readiness(make)
        assert make_result['release_ready'] is False
        assert make_result['blockers']

    def test_readiness_endpoint(self):
        revision = make_item_revision('P-READY-010', make_or_buy='BUY')
        response = api(make_company_admin()).get(reverse('engineering-readiness-detail', kwargs={'pk': revision.pk}))
        assert response.status_code == status.HTTP_200_OK
        assert response.data['item_revision_id'] == str(revision.pk)
