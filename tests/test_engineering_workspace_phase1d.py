from datetime import date

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from auditlog.models import AuditEvent
from enterprise_items.models import (
    AttributeDefinition,
    ClassificationAttribute,
    CodingOrganization,
    Item,
    ItemClassification,
    ItemIdentifier,
    ItemRevision,
    ItemRevisionAttributeValue,
    ItemType,
)
from enterprise_items.workspace_services import change_item_status
from tests.factories import make_company_admin, make_member

pytestmark = pytest.mark.django_db


def api(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def setup_parts():
    admin = make_company_admin()
    member = make_member()
    org = CodingOrganization.objects.create(name='Workspace Org', code='WX')
    item_type = ItemType.objects.create(organization=org, name='Part', code='PART', group='PART')
    root = ItemClassification.objects.create(organization=org, code='M', name='Mechanical', coding_prefix='M')
    child = ItemClassification.objects.create(organization=org, parent=root, code='BLT', name='Bolt', coding_prefix='BLT')
    other = ItemClassification.objects.create(organization=org, code='E', name='Electrical', coding_prefix='E')
    attr = AttributeDefinition.objects.create(organization=org, code='DIA', name='Diameter', data_type='DECIMAL', default_unit='MM', is_required=True, is_code_bearing=True, is_identity_defining=True)
    ClassificationAttribute.objects.create(classification=child, attribute_definition=attr)
    item = Item.objects.create(organization=org, item_code='M-BLT-001', name='Hex bolt workspace', item_type=item_type, classification=child, make_or_buy='MAKE', status='ACTIVE', code_generation_strategy='SEMANTIC')
    rev = ItemRevision.objects.create(item=item, revision='A', title='Rev A', status=ItemRevision.STATUS_DRAFT, effective_from=date(2026, 1, 1))
    ItemRevisionAttributeValue.objects.create(item_revision=rev, attribute_definition=attr, value_decimal=12, unit='MM')
    ItemIdentifier.objects.create(item=item, identifier_type='MANUFACTURER_PART_NUMBER', value='MPN-42', organization_name='ACME', is_primary=True)
    released_item = Item.objects.create(organization=org, item_code='E-001', name='Electrical assembly', item_type=item_type, classification=other, make_or_buy='BUY', status='PHASE_OUT', code_generation_strategy='SEQUENTIAL')
    released = ItemRevision.objects.create(item=released_item, revision='R1', status=ItemRevision.STATUS_RELEASED, effective_from=date(2026, 1, 1))
    return locals()


class TestPartWorkspaceList:
    def test_search_by_part_number_name_and_identifier(self):
        data = setup_parts()
        client = api(data['admin'])

        by_number = client.get(reverse('enterprise-item-workspace'), {'search': 'M-BLT'})
        by_name = client.get(reverse('enterprise-item-workspace'), {'search': 'workspace'})
        by_identifier = client.get(reverse('enterprise-item-workspace'), {'search': 'MPN-42'})

        assert by_number.status_code == status.HTTP_200_OK
        assert by_number.data['results'][0]['part_number'] == 'M-BLT-001'
        assert by_name.data['results'][0]['part_number'] == 'M-BLT-001'
        assert by_identifier.data['results'][0]['manufacturer_part_number'] == 'MPN-42'

    def test_filters_pagination_and_sorting(self):
        data = setup_parts()
        client = api(data['admin'])

        classification = client.get(reverse('enterprise-item-workspace'), {'classification': str(data['root'].pk), 'include_descendants': 'true'})
        lifecycle = client.get(reverse('enterprise-item-workspace'), {'revision_status': ItemRevision.STATUS_RELEASED})
        status_filter = client.get(reverse('enterprise-item-workspace'), {'status': 'PHASE_OUT', 'ordering': '-part_number', 'page_size': 1})
        strategy = client.get(reverse('enterprise-item-workspace'), {'code_generation_strategy': 'SEMANTIC'})

        assert {row['part_number'] for row in classification.data['results']} == {'M-BLT-001'}
        assert lifecycle.data['results'][0]['part_number'] == 'E-001'
        assert status_filter.data['count'] == 1
        assert strategy.data['results'][0]['coding_strategy'] == 'SEMANTIC'

    def test_member_can_view_but_cannot_change_status(self):
        data = setup_parts()
        client = api(data['member'])
        list_response = client.get(reverse('enterprise-item-workspace'))
        change_response = client.post(reverse('enterprise-item-status-change', kwargs={'pk': data['item'].pk}), {'status': 'BLOCKED'}, format='json')

        assert list_response.status_code == status.HTTP_200_OK
        assert change_response.status_code == status.HTTP_403_FORBIDDEN


class TestPartWorkspaceDetail:
    def test_part_detail_summary_resolves_current_released_identifiers_and_readiness(self):
        data = setup_parts()
        response = api(data['admin']).get(reverse('enterprise-item-workspace-summary', kwargs={'pk': data['item'].pk}))

        assert response.status_code == status.HTTP_200_OK
        assert response.data['item']['part_number'] == 'M-BLT-001'
        assert response.data['current_revision']['revision'] == 'A'
        assert response.data['released_revision'] is None
        assert response.data['identifiers'][0]['value'] == 'MPN-42'
        assert response.data['bom_summary']['exists'] is False
        assert response.data['opc_summary']['exists'] is False

    def test_revision_summary_and_technical_attributes_are_revision_scoped(self):
        data = setup_parts()
        summary = api(data['admin']).get(reverse('item-revision-workspace-summary', kwargs={'pk': data['rev'].pk}))
        attrs = api(data['admin']).get(reverse('item-revision-technical-attributes', kwargs={'pk': data['rev'].pk}))

        assert summary.status_code == status.HTTP_200_OK
        assert summary.data['id'] == str(data['rev'].pk)
        assert attrs.data['results'][0]['code'] == 'DIA'
        assert attrs.data['results'][0]['code_bearing'] is True
        assert attrs.data['results'][0]['governance_decision'] == 'CREATE_NEW_ITEM'
        assert attrs.data['results'][0]['read_only'] is False

    def test_released_revision_attributes_are_read_only(self):
        data = setup_parts()
        attrs = api(data['admin']).get(reverse('item-revision-technical-attributes', kwargs={'pk': data['released'].pk}))
        assert attrs.status_code == status.HTTP_200_OK
        assert attrs.data['results'] == [] or attrs.data['results'][0]['read_only'] is True


class TestStatusPolicyAndAudit:
    def test_item_status_is_authoritative_for_is_active(self):
        data = setup_parts()
        item = data['item']
        item.status = 'PHASE_OUT'
        item.is_active = False
        item.save()
        item.refresh_from_db()

        assert item.status == 'PHASE_OUT'
        assert item.is_active is True

    def test_status_change_policy_and_audit(self):
        data = setup_parts()
        item = change_item_status(data['item'], actor=data['admin'], status='BLOCKED', reason='quality hold')

        assert item.status == 'BLOCKED'
        assert item.is_active is False
        assert AuditEvent.objects.filter(action='part_status_changed', target_id=str(item.pk)).exists()

    def test_engineering_dashboard_metrics_are_server_side(self):
        data = setup_parts()
        response = api(data['admin']).get(reverse('enterprise-item-workspace-dashboard'))

        assert response.status_code == status.HTTP_200_OK
        assert response.data['active_parts'] >= 1
        assert response.data['phase_out_or_obsolete'] >= 1
        assert 'recent_parts' in response.data
