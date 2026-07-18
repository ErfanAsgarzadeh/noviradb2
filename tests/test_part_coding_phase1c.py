from datetime import date
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from auditlog.models import AuditEvent
from enterprise_items.coding_services import (
    activate_template,
    create_item_from_code,
    governance_decision_for_attribute_change,
    preview_code,
    semantic_hash,
)
from enterprise_items.models import (
    AttributeDefinition,
    AttributeEncodingOption,
    AttributeEncodingRule,
    ClassificationAttribute,
    CodingOrganization,
    Item,
    ItemClassification,
    ItemCodingScheme,
    ItemCodingTemplate,
    ItemCodingTemplateSegment,
    ItemIdentifier,
    ItemRevision,
    ItemRevisionAttributeValue,
)
from tests.factories import make_company_admin, make_member

pytestmark = pytest.mark.django_db


def api(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def fixture():
    admin = make_company_admin()
    org = CodingOrganization.objects.create(name='Phase 1C Org', code='P1C')
    item_type = org.item_types.create(name='Part', code='PART', group='PART')
    mechanical = ItemClassification.objects.create(organization=org, code='M', name='Mechanical', coding_prefix='M')
    fastener = ItemClassification.objects.create(organization=org, parent=mechanical, code='FST', name='Fastener', coding_prefix='FST')
    bolt = ItemClassification.objects.create(organization=org, parent=fastener, code='BLT', name='Bolt', coding_prefix='BLT')
    diameter = AttributeDefinition.objects.create(organization=org, code='DIAMETER', name='Diameter', data_type='DECIMAL', default_unit='MM', is_required=True, is_code_bearing=True, is_identity_defining=True, is_duplicate_key=True)
    length = AttributeDefinition.objects.create(organization=org, code='LENGTH', name='Length', data_type='DECIMAL', default_unit='MM', is_required=True, is_code_bearing=True, is_identity_defining=True, is_duplicate_key=True)
    coating = AttributeDefinition.objects.create(organization=org, code='COATING', name='Coating', data_type='CHOICE', validation_metadata={'choices': ['ZN', 'NC']}, is_required=True, is_code_bearing=True, is_identity_defining=True)
    note = AttributeDefinition.objects.create(organization=org, code='NOTE', name='Note', data_type='TEXT', is_revision_controlled=True)
    for order, attr in enumerate([diameter, length, coating, note], start=1):
        ClassificationAttribute.objects.create(classification=bolt, attribute_definition=attr, display_order=order)
    scheme = ItemCodingScheme.objects.create(organization=org, code='SEM', name='Semantic', strategy='SEMANTIC', sequence_scope='CLASSIFICATION', sequence_length=4, maximum_length=80)
    template = ItemCodingTemplate.objects.create(coding_scheme=scheme, classification=bolt, version=1, effective_from=date(2026, 1, 1), created_by=admin)
    numeric3 = AttributeEncodingRule.objects.create(organization=org, code='N3', name='3 digit numeric', encoding_type='NUMERIC', canonical_unit='MM', zero_pad_width=3)
    lookup = AttributeEncodingRule.objects.create(organization=org, code='COAT', name='Coating lookup', encoding_type='LOOKUP')
    AttributeEncodingOption.objects.create(rule=lookup, source_value='ZN', encoded_value='ZN')
    AttributeEncodingOption.objects.create(rule=lookup, source_value='NC', encoded_value='NC')
    ItemCodingTemplateSegment.objects.create(template=template, position=1, segment_type='CLASSIFICATION_CODE', classification_level=0)
    ItemCodingTemplateSegment.objects.create(template=template, position=2, segment_type='CLASSIFICATION_CODE', classification_level=1)
    ItemCodingTemplateSegment.objects.create(template=template, position=3, segment_type='CLASSIFICATION_CODE', classification_level=2)
    ItemCodingTemplateSegment.objects.create(template=template, position=4, segment_type='ATTRIBUTE', attribute_definition=diameter, encoding_rule=numeric3, prefix='M')
    ItemCodingTemplateSegment.objects.create(template=template, position=5, segment_type='ATTRIBUTE', attribute_definition=length, encoding_rule=numeric3)
    ItemCodingTemplateSegment.objects.create(template=template, position=6, segment_type='ATTRIBUTE', attribute_definition=coating, encoding_rule=lookup)
    return locals()


class TestClassificationAndAttributes:
    def test_create_hierarchy_and_retrieve_tree(self):
        data = fixture()
        response = api(data['admin']).get(reverse('item-classification-tree'), {'organization': data['org'].pk})

        assert response.status_code == status.HTTP_200_OK
        assert response.data[0]['code'] == 'M'
        assert response.data[0]['children'][0]['children'][0]['code'] == 'BLT'

    def test_reject_self_and_circular_parent(self):
        data = fixture()
        bolt = data['bolt']
        bolt.parent = bolt
        with pytest.raises(ValidationError):
            bolt.full_clean()
        data['mechanical'].parent = bolt
        with pytest.raises(ValidationError):
            data['mechanical'].full_clean()

    def test_effective_attributes_include_parent_assignments(self):
        data = fixture()
        standard = AttributeDefinition.objects.create(organization=data['org'], code='STANDARD', name='Standard', data_type='TEXT', is_required=True)
        ClassificationAttribute.objects.create(classification=data['mechanical'], attribute_definition=standard)

        response = api(data['admin']).get(reverse('item-classification-effective-attributes', kwargs={'pk': data['bolt'].pk}))

        assert response.status_code == status.HTTP_200_OK
        assert {'DIAMETER', 'LENGTH', 'COATING', 'NOTE', 'STANDARD'} <= {row['attribute']['code'] for row in response.data}

    def test_deactivate_used_classification_is_safe(self):
        data = fixture()
        Item.objects.create(organization=data['org'], item_code='LEG-1', name='Legacy', item_type=data['item_type'], classification=data['bolt'])
        response = api(data['admin']).post(reverse('item-classification-deactivate', kwargs={'pk': data['bolt'].pk}))
        assert response.status_code == status.HTTP_400_BAD_REQUEST


class TestTypedValuesAndTemplates:
    def test_typed_value_validates_data_type_choice_and_immutability(self):
        data = fixture()
        item = Item.objects.create(organization=data['org'], item_code='LEG-2', name='Legacy', item_type=data['item_type'], classification=data['bolt'])
        revision = ItemRevision.objects.create(item=item, revision='A')

        with pytest.raises(ValidationError):
            ItemRevisionAttributeValue.objects.create(item_revision=revision, attribute_definition=data['diameter'], value_text='M12')
        value = ItemRevisionAttributeValue.objects.create(item_revision=revision, attribute_definition=data['diameter'], value_decimal=Decimal('12'), unit='mm')
        assert value.unit == 'MM'
        revision.status = ItemRevision.STATUS_RELEASED
        revision.save(update_fields=['status'])
        with pytest.raises(ValidationError):
            ItemRevisionAttributeValue.objects.create(item_revision=revision, attribute_definition=data['length'], value_decimal=Decimal('50'))

    def test_template_activation_validation_and_active_edit_guard(self):
        data = fixture()
        template = activate_template(data['template'], actor=data['admin'])
        assert template.status == ItemCodingTemplate.STATUS_ACTIVE
        template.version = 2
        with pytest.raises(ValidationError):
            template.save()
        assert AuditEvent.objects.filter(action='item_coding_template_activated', target_id=str(template.pk)).exists()

    def test_unauthorized_user_cannot_activate_template(self):
        data = fixture()
        response = api(make_member()).post(reverse('item-coding-template-activate', kwargs={'pk': data['template'].pk}))
        assert response.status_code == status.HTTP_403_FORBIDDEN


class TestCodeGenerationAndDuplicates:
    def test_semantic_preview_numeric_lookup_and_fingerprint_are_deterministic(self):
        data = fixture()
        activate_template(data['template'], actor=data['admin'])
        attrs = {'LENGTH': {'value': 50, 'unit': 'mm'}, 'DIAMETER': {'value': 12, 'unit': 'mm'}, 'COATING': 'ZN'}
        preview = preview_code(organization=data['org'], classification=data['bolt'], coding_scheme=data['scheme'], attributes=attrs, name='Hex bolt')
        reordered = preview_code(organization=data['org'], classification=data['bolt'], coding_scheme=data['scheme'], attributes={'COATING': 'ZN', 'DIAMETER': {'value': '12.0', 'unit': 'MM'}, 'LENGTH': 50}, name='Hex bolt')

        assert preview['candidate_code'] == 'M-FST-BLT-M012-050-ZN'
        assert preview['semantic_identity_hash'] == reordered['semantic_identity_hash']
        assert preview['semantic_identity_payload'] == reordered['semantic_identity_payload']
        assert preview['segment_breakdown'][3]['label'] == 'Diameter'

    def test_generate_and_create_is_transactional_and_blocks_duplicate_identity(self):
        data = fixture()
        activate_template(data['template'], actor=data['admin'])
        attrs = {'DIAMETER': 12, 'LENGTH': 50, 'COATING': 'ZN'}
        result = create_item_from_code(actor=data['admin'], organization=data['org'], item_type=data['item_type'], classification=data['bolt'], name='Hex bolt M12x50', attributes=attrs, coding_scheme=data['scheme'])

        assert result['item'].item_code == 'M-FST-BLT-M012-050-ZN'
        assert result['revision'].attribute_values.count() == 3
        with pytest.raises(Exception):
            create_item_from_code(actor=data['admin'], organization=data['org'], item_type=data['item_type'], classification=data['bolt'], name='Duplicate', attributes=attrs, coding_scheme=data['scheme'])

    def test_sequential_hybrid_manual_and_legacy_strategies(self):
        data = fixture()
        seq_scheme = ItemCodingScheme.objects.create(organization=data['org'], code='SEQ', name='Sequential', strategy='SEQUENTIAL', sequence_length=4)
        seq_template = ItemCodingTemplate.objects.create(coding_scheme=seq_scheme, classification=data['bolt'], version=1, effective_from=date(2026, 1, 1))
        ItemCodingTemplateSegment.objects.create(template=seq_template, position=1, segment_type='LITERAL', literal_value='SV')
        ItemCodingTemplateSegment.objects.create(template=seq_template, position=2, segment_type='SEQUENCE', width=4)
        activate_template(seq_template, actor=data['admin'])
        seq = create_item_from_code(actor=data['admin'], organization=data['org'], item_type=data['item_type'], classification=data['bolt'], name='Service', attributes={'DIAMETER': 1, 'LENGTH': 1, 'COATING': 'NC'}, coding_scheme=seq_scheme)
        assert seq['item'].item_code == 'SV-0001'

        manual_scheme = ItemCodingScheme.objects.create(organization=data['org'], code='MAN', name='Manual', strategy='MANUAL_CONTROLLED')
        manual = create_item_from_code(actor=data['admin'], organization=data['org'], item_type=data['item_type'], classification=data['bolt'], name='Manual', attributes={'DIAMETER': 2, 'LENGTH': 2, 'COATING': 'NC'}, coding_scheme=manual_scheme, manual_code='legacy-x')
        assert manual['item'].item_code == 'LEGACY-X'
        legacy_scheme = ItemCodingScheme.objects.create(organization=data['org'], code='LEG', name='Legacy', strategy='LEGACY')
        preview = preview_code(organization=data['org'], classification=data['bolt'], coding_scheme=legacy_scheme, attributes={'DIAMETER': 3, 'LENGTH': 3, 'COATING': 'NC'}, manual_code='old-3')
        assert preview['candidate_code'] == 'OLD-3'

    def test_duplicate_identifier_and_similar_name_detection(self):
        data = fixture()
        item = Item.objects.create(organization=data['org'], item_code='EXIST-1', name='Hex Bolt Stainless', item_type=data['item_type'], classification=data['bolt'])
        ItemIdentifier.objects.create(item=item, identifier_type='MANUFACTURER_PART_NUMBER', value='mpn 42', organization_name='ACME')
        activate_template(data['template'], actor=data['admin'])
        preview = preview_code(organization=data['org'], classification=data['bolt'], coding_scheme=data['scheme'], attributes={'DIAMETER': 5, 'LENGTH': 5, 'COATING': 'NC'}, identifiers=[{'identifier_type': 'MANUFACTURER_PART_NUMBER', 'value': ' MPN42 ', 'organization_name': 'ACME'}], name='Hex Bolt Stainless Steel')

        kinds = {dup['kind'] for dup in preview['duplicates']}
        assert 'MANUFACTURER_PART_NUMBER' in kinds
        assert 'SIMILAR_NAME' in kinds

    def test_api_preview_and_controlled_create(self):
        data = fixture()
        activate_template(data['template'], actor=data['admin'])
        payload = {'organization': str(data['org'].pk), 'classification': str(data['bolt'].pk), 'coding_scheme': str(data['scheme'].pk), 'item_type': str(data['item_type'].pk), 'name': 'API Bolt', 'attributes': {'DIAMETER': 12, 'LENGTH': 50, 'COATING': 'ZN'}}
        preview = api(data['admin']).post(reverse('part-coding-preview'), payload, format='json')
        assert preview.status_code == status.HTTP_200_OK
        create = api(data['admin']).post(reverse('part-coding-controlled-create'), payload, format='json')
        assert create.status_code == status.HTTP_201_CREATED
        assert create.data['item']['item_code'] == preview.data['candidate_code']

    def test_code_bearing_identity_change_governance(self):
        data = fixture()
        assert governance_decision_for_attribute_change(data['diameter']) == 'CREATE_NEW_ITEM'
        assert governance_decision_for_attribute_change(data['note']) == 'CREATE_NEW_REVISION'


def test_existing_item_code_is_preserved_when_phase1c_fields_exist():
    data = fixture()
    item = Item.objects.create(organization=data['org'], item_code='OLD-CODE-001', name='Legacy item', item_type=data['item_type'])
    item.refresh_from_db()
    assert item.item_code == 'OLD-CODE-001'
    assert item.semantic_identity_hash == ''
