import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase

from enterprise_items.models import (
    CodeDefinition,
    CodeDefinitionVersion,
    CodeOptionEncoding,
    CodeSegment,
    CodingOrganization,
    Item,
    ItemClassification,
    ItemRevision,
    ItemType,
    ParameterDefinition,
    ParameterMetadataField,
    ParameterMetadataValue,
    ParameterOption,
    PartParameterValue,
    PartTechnicalValue,
    RevisionTechnicalValue,
    SequenceDefinition,
    StructureDefinition,
    StructureParameter,
    TechnicalDataTemplate,
    TechnicalFieldDefinition,
)


class Phase1FDomainModelTests(TestCase):
    def setUp(self):
        self.org = CodingOrganization.objects.create(name='Phase 1F Org', code='P1F')
        self.other_org = CodingOrganization.objects.create(name='Phase 1F Other Org', code='P1FO')
        self.item_type = ItemType.objects.create(organization=self.org, name='Part', code='PART', group='PART')
        self.other_item_type = ItemType.objects.create(organization=self.other_org, name='Part', code='PART', group='PART')

    def parameter(self, code='P', data_type=ParameterDefinition.TYPE_TEXT, org=None, active=True):
        return ParameterDefinition.objects.create(organization=org or self.org, code=code, name=code.title(), data_type=data_type, active=active)

    def structure(self, code='STRUCT', org=None):
        return StructureDefinition.objects.create(organization=org or self.org, code=code, name=code.title())

    def definition_version(self, status=CodeDefinitionVersion.STATUS_DRAFT):
        structure = self.structure('CD_STRUCT')
        code_definition = CodeDefinition.objects.create(organization=self.org, structure=structure, code='CD', name='Internal Code')
        return structure, code_definition, CodeDefinitionVersion.objects.create(code_definition=code_definition, version_number=1, status=status, configuration_snapshot={'segments': []})

    def part_context(self):
        material = self.parameter('MAT', ParameterDefinition.TYPE_SINGLE_SELECT)
        option = ParameterOption.objects.create(parameter=material, display_label='Carbon Steel', stored_value='CARBON_STEEL', sort_order=10)
        structure = self.structure('PART_STRUCT')
        sp = StructureParameter.objects.create(structure=structure, parameter=material, sort_order=10, requiredness=StructureParameter.REQUIRED, identity_defining=True)
        definition = CodeDefinition.objects.create(organization=self.org, structure=structure, code='PART_CODE', name='Part Code')
        version = CodeDefinitionVersion.objects.create(code_definition=definition, version_number=1, status=CodeDefinitionVersion.STATUS_ACTIVE, configuration_snapshot={'segments': []})
        part = Item.objects.create(organization=self.org, item_type=self.item_type, structure=structure, code_definition=definition, code_definition_version=version, item_code='P1F-001', name='Part 001', status='ACTIVE', semantic_identity_hash='stable-hash')
        revision = ItemRevision.objects.create(item=part, revision='R0', title='Initial')
        template = TechnicalDataTemplate.objects.create(organization=self.org, structure=structure, code='TD', name='Technical Data')
        return material, option, structure, sp, definition, version, part, revision, template

    def test_parameter_definition_code_uniqueness_data_types_and_active_flag(self):
        first = self.parameter('DIAMETER', ParameterDefinition.TYPE_UNIT_VALUE)
        same_other_org = self.parameter('DIAMETER', ParameterDefinition.TYPE_UNIT_VALUE, org=self.other_org)
        inactive = self.parameter('OLD', ParameterDefinition.TYPE_TEXT, active=False)
        self.assertFalse(inactive.active)
        self.assertEqual(first.code, 'DIAMETER')
        self.assertEqual(same_other_org.organization, self.other_org)
        with self.assertRaises(ValidationError):
            ParameterDefinition.objects.create(organization=self.org, code='BAD', name='Bad', data_type='BAD_TYPE')
        with self.assertRaises(ValidationError):
            ParameterDefinition.objects.create(organization=self.org, code='DIAMETER', name='Duplicate', data_type=ParameterDefinition.TYPE_TEXT)

    def test_parameter_option_scope_order_inactive_and_select_only_policy(self):
        material = self.parameter('MATERIAL', ParameterDefinition.TYPE_SINGLE_SELECT)
        color = self.parameter('COLOR', ParameterDefinition.TYPE_SINGLE_SELECT)
        ParameterOption.objects.create(parameter=material, display_label='B', stored_value='B', sort_order=20, active=False)
        first = ParameterOption.objects.create(parameter=material, display_label='A', stored_value='A', sort_order=10)
        same_value_other_parameter = ParameterOption.objects.create(parameter=color, display_label='A', stored_value='A', sort_order=10)
        self.assertEqual(list(material.options.values_list('stored_value', flat=True)), ['A', 'B'])
        self.assertFalse(material.options.get(stored_value='B').active)
        self.assertEqual(same_value_other_parameter.parameter, color)
        with self.assertRaises(ValidationError):
            ParameterOption.objects.create(parameter=self.parameter('LENGTH', ParameterDefinition.TYPE_INTEGER), display_label='10', stored_value='10')
        with self.assertRaises(ValidationError):
            ParameterOption.objects.create(parameter=material, display_label='A2', stored_value='A', sort_order=30)
        self.assertEqual(first.parameter.organization, self.org)

    def test_parameter_metadata_field_and_value_validation(self):
        parameter = self.parameter('PRESSURE', ParameterDefinition.TYPE_DECIMAL)
        other_parameter = self.parameter('OTHER', ParameterDefinition.TYPE_TEXT, org=self.other_org)
        field = ParameterMetadataField.objects.create(organization=self.org, code='STD', label='Standard', data_type=ParameterDefinition.TYPE_TEXT, default_value={'value': 'ASME'}, active=False)
        self.assertFalse(field.active)
        value = ParameterMetadataValue.objects.create(parameter=parameter, field_definition=field, value={'value': 'ASME B16'})
        self.assertEqual(value.value['value'], 'ASME B16')
        with self.assertRaises(ValidationError):
            ParameterMetadataField.objects.create(organization=self.org, code='STD', label='Duplicate', data_type=ParameterDefinition.TYPE_TEXT)
        with self.assertRaises(ValidationError):
            ParameterMetadataValue.objects.create(parameter=other_parameter, field_definition=field, value={'value': 'wrong org'})
        with self.assertRaises(ValidationError):
            ParameterMetadataValue.objects.create(parameter=parameter, field_definition=field, value={'raw': 'not canonical'})
        with self.assertRaises(ValidationError):
            ParameterMetadataValue.objects.create(parameter=parameter, field_definition=field, value={'value': 'second'})

    def test_structure_definition_uniqueness_status_and_classification_org(self):
        classification = ItemClassification.objects.create(organization=self.org, code='BOLT', name='Bolt')
        other_classification = ItemClassification.objects.create(organization=self.other_org, code='BOLT', name='Bolt')
        structure = StructureDefinition.objects.create(organization=self.org, code='BOLT', name='Bolt', classification=classification, status=StructureDefinition.STATUS_ACTIVE)
        self.assertEqual(structure.status, StructureDefinition.STATUS_ACTIVE)
        StructureDefinition.objects.create(organization=self.other_org, code='BOLT', name='Other Bolt')
        with self.assertRaises(ValidationError):
            StructureDefinition.objects.create(organization=self.org, code='BOLT', name='Duplicate')
        with self.assertRaises(ValidationError):
            StructureDefinition.objects.create(organization=self.org, code='BAD', name='Bad', classification=other_classification)

    def test_structure_parameter_constraints_conditions_defaults_and_inactive_policy(self):
        diameter = self.parameter('DIAMETER', ParameterDefinition.TYPE_UNIT_VALUE)
        inactive = self.parameter('INACTIVE', ParameterDefinition.TYPE_TEXT, active=False)
        other = self.parameter('OTHERORG', ParameterDefinition.TYPE_TEXT, org=self.other_org)
        structure = self.structure('BOLT')
        sp = StructureParameter.objects.create(structure=structure, parameter=diameter, sort_order=10, requiredness=StructureParameter.REQUIRED, identity_defining=True, default_value={'value': '12.00', 'unit': 'mm'}, visibility_condition={'field': 'X', 'equals': 'Y'})
        self.assertTrue(sp.identity_defining)
        self.assertEqual(list(structure.parameters.values_list('sort_order', flat=True)), [10])
        with self.assertRaises(ValidationError):
            StructureParameter.objects.create(structure=structure, parameter=diameter, sort_order=20)
        with self.assertRaises(ValidationError):
            StructureParameter.objects.create(structure=structure, parameter=other, sort_order=20)
        with self.assertRaises(ValidationError):
            StructureParameter.objects.create(structure=structure, parameter=inactive, sort_order=20)
        with self.assertRaises(ValidationError):
            StructureParameter.objects.create(structure=structure, parameter=diameter, sort_order=30, requiredness='MUST')
        with self.assertRaises(ValidationError):
            StructureParameter.objects.create(structure=structure, parameter=self.parameter('BADDEFAULT', ParameterDefinition.TYPE_INTEGER), sort_order=30, default_value={'value': 'not int'})
        with self.assertRaises(ValidationError):
            StructureParameter.objects.create(structure=structure, parameter=self.parameter('BADCOND'), sort_order=30, visibility_condition={'python': 'exec()'})

    def test_code_definition_constraints_separator_max_length_and_default_policy(self):
        structure = self.structure('MOTOR')
        other_structure = self.structure('OTHER', org=self.other_org)
        CodeDefinition.objects.create(organization=self.org, structure=structure, code='INTERNAL', name='Internal', is_default=True, maximum_length=40)
        with self.assertRaises(ValidationError):
            CodeDefinition.objects.create(organization=self.org, structure=structure, code='INTERNAL', name='Duplicate')
        with self.assertRaises(ValidationError):
            CodeDefinition.objects.create(organization=self.org, structure=other_structure, code='BADORG', name='Bad Org')
        with self.assertRaises(ValidationError):
            CodeDefinition.objects.create(organization=self.org, structure=structure, code='SECOND_DEFAULT', name='Second', is_default=True)
        with self.assertRaises(ValidationError):
            CodeDefinition.objects.create(organization=self.org, structure=structure, code='BADSEP', name='Bad Sep', separator=' -')
        with self.assertRaises(ValidationError):
            CodeDefinition.objects.create(organization=self.org, structure=structure, code='BADLEN', name='Bad Len', maximum_length=0)

    def test_code_definition_version_lifecycle_database_constraint_and_traceability(self):
        structure, definition, active = self.definition_version(CodeDefinitionVersion.STATUS_ACTIVE)
        with self.assertRaises(ValidationError):
            CodeDefinitionVersion.objects.create(code_definition=definition, version_number=2, status=CodeDefinitionVersion.STATUS_ACTIVE, configuration_snapshot={})
        draft = CodeDefinitionVersion.objects.create(code_definition=definition, version_number=2, status=CodeDefinitionVersion.STATUS_DRAFT, configuration_snapshot={'draft': True})
        draft.configuration_snapshot = {'draft': 'edited'}
        draft.save()
        self.assertEqual(draft.configuration_snapshot['draft'], 'edited')
        active.configuration_snapshot = {'changed': True}
        with self.assertRaises(ValidationError):
            active.save()
        active.status = CodeDefinitionVersion.STATUS_SUPERSEDED
        active.save()
        self.assertEqual(active.status, CodeDefinitionVersion.STATUS_SUPERSEDED)
        part = Item.objects.create(organization=self.org, item_type=self.item_type, structure=structure, code_definition=definition, code_definition_version=active, item_code='TRACE-001', name='Trace', status='ACTIVE')
        with self.assertRaises(ProtectedError):
            active.delete()
        self.assertEqual(part.code_definition_version_id, active.id)

    def test_queryset_update_can_bypass_active_snapshot_immutability_and_is_documented(self):
        _, _, active = self.definition_version(CodeDefinitionVersion.STATUS_ACTIVE)
        CodeDefinitionVersion.objects.filter(pk=active.pk).update(configuration_snapshot={'bypassed': True})
        active.refresh_from_db()
        self.assertEqual(active.configuration_snapshot, {'bypassed': True})

    def test_code_segment_validation_order_parameter_membership_and_mixed_config(self):
        material = self.parameter('MAT', ParameterDefinition.TYPE_SINGLE_SELECT)
        outsider = self.parameter('OUT', ParameterDefinition.TYPE_TEXT)
        structure, definition, version = self.definition_version()
        StructureParameter.objects.create(structure=structure, parameter=material, sort_order=10)
        CodeSegment.objects.create(version=version, segment_type=CodeSegment.TYPE_FIXED_TEXT, sort_order=10, fixed_value='BLT')
        CodeSegment.objects.create(version=version, segment_type=CodeSegment.TYPE_PARAMETER, sort_order=20, parameter=material, width=3, padding='0')
        CodeSegment.objects.create(version=version, segment_type=CodeSegment.TYPE_SEQUENCE, sort_order=30)
        with self.assertRaises(ValidationError):
            CodeSegment.objects.create(version=version, segment_type=CodeSegment.TYPE_FIXED_TEXT, sort_order=40)
        with self.assertRaises(ValidationError):
            CodeSegment.objects.create(version=version, segment_type=CodeSegment.TYPE_PARAMETER, sort_order=40)
        with self.assertRaises(ValidationError):
            CodeSegment.objects.create(version=version, segment_type=CodeSegment.TYPE_PARAMETER, sort_order=40, parameter=outsider)
        with self.assertRaises(ValidationError):
            CodeSegment.objects.create(version=version, segment_type=CodeSegment.TYPE_PARAMETER, sort_order=40, parameter=material, fixed_value='BAD')
        with self.assertRaises(ValidationError):
            CodeSegment.objects.create(version=version, segment_type=CodeSegment.TYPE_PARAMETER, sort_order=40, parameter=material, width=0)
        with self.assertRaises(ValidationError):
            CodeSegment.objects.create(version=version, segment_type=CodeSegment.TYPE_PARAMETER, sort_order=40, parameter=material, padding='00')
        with self.assertRaises(ValidationError):
            CodeSegment.objects.create(version=version, segment_type=CodeSegment.TYPE_PARAMETER, sort_order=40, parameter=material, condition={'eval': 'bad'})

    def test_code_option_encoding_context_normalized_tokens_and_inactive_history(self):
        material = self.parameter('MATSEL', ParameterDefinition.TYPE_SINGLE_SELECT)
        text_param = self.parameter('TEXT_PARAM', ParameterDefinition.TYPE_TEXT)
        option = ParameterOption.objects.create(parameter=material, display_label='Carbon Steel', stored_value='CARBON_STEEL', sort_order=10)
        other_option = ParameterOption.objects.create(parameter=material, display_label='Cast Steel', stored_value='CAST_STEEL', sort_order=20)
        wrong_option = ParameterOption.objects.create(parameter=self.parameter('COLOR', ParameterDefinition.TYPE_SINGLE_SELECT), display_label='Red', stored_value='RED', sort_order=10)
        structure, definition, version = self.definition_version()
        StructureParameter.objects.create(structure=structure, parameter=material, sort_order=10)
        StructureParameter.objects.create(structure=structure, parameter=text_param, sort_order=20)
        segment = CodeSegment.objects.create(version=version, segment_type=CodeSegment.TYPE_PARAMETER, sort_order=10, parameter=material)
        text_segment = CodeSegment.objects.create(version=version, segment_type=CodeSegment.TYPE_PARAMETER, sort_order=20, parameter=text_param)
        fixed_segment = CodeSegment.objects.create(version=version, segment_type=CodeSegment.TYPE_FIXED_TEXT, sort_order=30, fixed_value='X')
        CodeOptionEncoding.objects.create(code_segment=segment, parameter_option=option, encoded_token='cs')
        CodeOptionEncoding.objects.create(code_segment=segment, parameter_option=other_option, encoded_token='CS', active=False)
        with self.assertRaises(ValidationError):
            CodeOptionEncoding.objects.create(code_segment=segment, parameter_option=other_option, encoded_token=' cS ')
        with self.assertRaises(ValidationError):
            CodeOptionEncoding.objects.create(code_segment=segment, parameter_option=wrong_option, encoded_token='RD')
        with self.assertRaises(ValidationError):
            CodeOptionEncoding.objects.create(code_segment=text_segment, parameter_option=option, encoded_token='TXT')
        with self.assertRaises(ValidationError):
            CodeOptionEncoding.objects.create(code_segment=fixed_segment, parameter_option=option, encoded_token='FX')
        with self.assertRaises(ValidationError):
            CodeOptionEncoding.objects.create(code_segment=segment, parameter_option=other_option, encoded_token='')

    def test_sequence_definition_schema_invariants(self):
        _, _, version = self.definition_version()
        SequenceDefinition.objects.create(code_definition_version=version, scope=SequenceDefinition.SCOPE_CODE_DEFINITION, starting_value=1, current_value=0, width=5, padding_character='0')
        with self.assertRaises(ValidationError):
            SequenceDefinition.objects.create(code_definition_version=version, scope=SequenceDefinition.SCOPE_CODE_DEFINITION, starting_value=1, current_value=0)
        with self.assertRaises(ValidationError):
            SequenceDefinition.objects.create(code_definition_version=version, scope='BAD', starting_value=1, current_value=0)
        with self.assertRaises(ValidationError):
            SequenceDefinition.objects.create(code_definition_version=version, scope=SequenceDefinition.SCOPE_GLOBAL, starting_value=5, current_value=2)
        with self.assertRaises(ValidationError):
            SequenceDefinition.objects.create(code_definition_version=version, scope=SequenceDefinition.SCOPE_GLOBAL, starting_value=0, current_value=0, width=0)
        with self.assertRaises(ValidationError):
            SequenceDefinition.objects.create(code_definition_version=version, scope=SequenceDefinition.SCOPE_GLOBAL, starting_value=0, current_value=0, padding_character='00')
        with self.assertRaises(ValidationError):
            SequenceDefinition.objects.create(code_definition_version=version, scope=SequenceDefinition.SCOPE_GLOBAL, starting_value=0, current_value=0, reset_policy='DAILY')

    def test_technical_templates_and_fields_scope_types_options_and_validation_config(self):
        structure = self.structure('TECH')
        other_structure = self.structure('TECH_OTHER', org=self.other_org)
        template = TechnicalDataTemplate.objects.create(organization=self.org, structure=structure, code='TECH', name='Tech Template', version=1)
        TechnicalDataTemplate.objects.create(organization=self.other_org, structure=other_structure, code='TECH', name='Other Tech Template')
        with self.assertRaises(ValidationError):
            TechnicalDataTemplate.objects.create(organization=self.org, structure=structure, code='TECH', name='Duplicate')
        with self.assertRaises(ValidationError):
            TechnicalDataTemplate.objects.create(organization=self.org, structure=other_structure, code='BADORG', name='Bad Org')
        TechnicalFieldDefinition.objects.create(template=template, code='FINISH', label='Finish', data_type=ParameterDefinition.TYPE_TEXT, scope=TechnicalFieldDefinition.SCOPE_PART, sort_order=10)
        TechnicalFieldDefinition.objects.create(template=template, code='MATERIAL', label='Material', data_type=ParameterDefinition.TYPE_SINGLE_SELECT, scope=TechnicalFieldDefinition.SCOPE_REVISION, options=[{'label': 'A', 'value': 'A'}], sort_order=20)
        with self.assertRaises(ValidationError):
            TechnicalFieldDefinition.objects.create(template=template, code='FINISH', label='Duplicate', data_type=ParameterDefinition.TYPE_TEXT, sort_order=30)
        with self.assertRaises(ValidationError):
            TechnicalFieldDefinition.objects.create(template=template, code='BAD_SCOPE', label='Bad', data_type=ParameterDefinition.TYPE_TEXT, scope='ITEM', sort_order=30)
        with self.assertRaises(ValidationError):
            TechnicalFieldDefinition.objects.create(template=template, code='BAD_OPTIONS', label='Bad', data_type=ParameterDefinition.TYPE_TEXT, options=[{'label': 'A'}], sort_order=30)
        with self.assertRaises(ValidationError):
            TechnicalFieldDefinition.objects.create(template=template, code='BAD_DEFAULT', label='Bad', data_type=ParameterDefinition.TYPE_INTEGER, default_value={'value': 'not int'}, sort_order=30)
        with self.assertRaises(ValidationError):
            TechnicalFieldDefinition.objects.create(template=template, code='BAD_ORDER', label='Bad', data_type=ParameterDefinition.TYPE_TEXT, sort_order=10)

    def test_part_parameter_and_technical_values_are_typed_and_context_scoped(self):
        material, option, structure, sp, definition, version, part, revision, template = self.part_context()
        part_field = TechnicalFieldDefinition.objects.create(template=template, code='FINISH', label='Finish', data_type=ParameterDefinition.TYPE_TEXT, scope=TechnicalFieldDefinition.SCOPE_PART, sort_order=10)
        revision_field = TechnicalFieldDefinition.objects.create(template=template, code='NOTE', label='Note', data_type=ParameterDefinition.TYPE_LONG_TEXT, scope=TechnicalFieldDefinition.SCOPE_REVISION, sort_order=20)
        PartParameterValue.objects.create(part=part, structure_parameter=sp, parameter=material, value={'option_id': str(option.id)}, normalized_value='CARBON_STEEL', display_value='Carbon Steel')
        PartTechnicalValue.objects.create(part=part, technical_field=part_field, value={'value': 'Painted'})
        RevisionTechnicalValue.objects.create(revision=revision, technical_field=revision_field, value={'value': 'Initial note'})
        self.assertEqual(part.parameter_values.get().display_value, 'Carbon Steel')
        with self.assertRaises(ValidationError):
            PartParameterValue.objects.create(part=part, structure_parameter=sp, parameter=material, value={'raw': str(option.id)}, normalized_value='BAD')
        with self.assertRaises(ValidationError):
            PartTechnicalValue.objects.create(part=part, technical_field=revision_field, value={'value': 'wrong scope'})
        with self.assertRaises(ValidationError):
            RevisionTechnicalValue.objects.create(revision=revision, technical_field=part_field, value={'value': 'wrong scope'})
        with self.assertRaises(ValidationError):
            PartTechnicalValue.objects.create(part=part, technical_field=part_field, value={'raw': 'bad'})
        with self.assertRaises(ValidationError):
            PartParameterValue.objects.create(part=part, structure_parameter=sp, parameter=self.parameter('OTHERP'), value={'value': 'x'}, normalized_value='x')

    def test_item_integration_null_legacy_and_consistency_traceability(self):
        legacy = Item.objects.create(organization=self.org, item_type=self.item_type, item_code='LEGACY-001', name='Legacy', semantic_identity_hash='legacy-hash')
        self.assertIsNone(legacy.structure_id)
        self.assertEqual(legacy.coding_snapshot, {})
        old_code = legacy.item_code
        old_hash = legacy.semantic_identity_hash
        legacy.name = 'Legacy renamed'
        legacy.save()
        self.assertEqual(legacy.item_code, old_code)
        self.assertEqual(legacy.semantic_identity_hash, old_hash)
        _, _, structure, _, definition, version, part, _, _ = self.part_context()
        self.assertEqual(part.structure, structure)
        other_structure = self.structure('OTHER_FOR_ITEM')
        other_definition = CodeDefinition.objects.create(organization=self.org, structure=other_structure, code='OTHER_DEF', name='Other Def')
        with self.assertRaises(ValidationError):
            Item.objects.create(organization=self.org, item_type=self.item_type, structure=structure, code_definition=other_definition, item_code='BAD-001', name='Bad')
        draft_version = CodeDefinitionVersion.objects.create(code_definition=definition, version_number=2, status=CodeDefinitionVersion.STATUS_DRAFT)
        with self.assertRaises(ValidationError):
            Item.objects.create(organization=self.org, item_type=self.item_type, structure=structure, code_definition=definition, code_definition_version=draft_version, item_code='BAD-002', name='Bad')
        with self.assertRaises(ProtectedError):
            structure.delete()
        with self.assertRaises(ProtectedError):
            definition.delete()
        with self.assertRaises(ProtectedError):
            version.delete()

    def test_canonical_structured_value_schemas(self):
        examples = [
            (ParameterDefinition.TYPE_TEXT, {'value': 'abc'}),
            (ParameterDefinition.TYPE_LONG_TEXT, {'value': 'long'}),
            (ParameterDefinition.TYPE_INTEGER, {'value': 12}),
            (ParameterDefinition.TYPE_DECIMAL, {'value': '12.50'}),
            (ParameterDefinition.TYPE_BOOLEAN, {'value': True}),
            (ParameterDefinition.TYPE_DATE, {'value': '2026-07-20'}),
            (ParameterDefinition.TYPE_UNIT_VALUE, {'value': '12.50', 'unit': 'mm'}),
            (ParameterDefinition.TYPE_RANGE, {'minimum': '10', 'maximum': '15', 'unit': 'mm'}),
            (ParameterDefinition.TYPE_SINGLE_SELECT, {'option_id': 'option-1'}),
            (ParameterDefinition.TYPE_MULTI_SELECT, {'option_ids': ['option-1', 'option-2']}),
            (ParameterDefinition.TYPE_REFERENCE, {'target_type': 'document', 'target_id': 'doc-1'}),
        ]
        for index, (data_type, value) in enumerate(examples):
            field = ParameterMetadataField.objects.create(organization=self.org, code=f'F{index}', label=f'Field {index}', data_type=data_type)
            parameter = self.parameter(f'P{index}', data_type)
            ParameterMetadataValue.objects.create(parameter=parameter, field_definition=field, value=value)