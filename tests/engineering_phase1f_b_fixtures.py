from django.contrib.auth import get_user_model

from enterprise_items.engineering_domain_services import CodeDefinitionService, CodeDefinitionVersionService, CodeSegmentService, ParameterDefinitionService, StructureDefinitionService, TechnicalDataTemplateService
from enterprise_items.models import CodeSegment, CodingOrganization, ItemType, ParameterDefinition, ParameterOption, SequenceDefinition, StructureParameter, TechnicalFieldDefinition


def build_user(username='phase1fb'):
    User = get_user_model()
    user = User.objects.create_user(username=username, password='x')
    user.is_staff = True
    user.is_superuser = True
    user.save()
    return user


def build_domain():
    actor = build_user()
    org = CodingOrganization.objects.create(name='Phase 1F-B Org', code='P1FB')
    item_type = ItemType.objects.create(organization=org, name='Part', code='PART', group='PART')
    psvc = ParameterDefinitionService()
    material = psvc.create(actor=actor, organization=org, code='MATERIAL', name='Material', data_type=ParameterDefinition.TYPE_SINGLE_SELECT)
    carbon = ParameterOption.objects.create(parameter=material, display_label='Carbon Steel', stored_value='CARBON_STEEL', sort_order=10)
    stainless = ParameterOption.objects.create(parameter=material, display_label='Stainless Steel', stored_value='STAINLESS_STEEL', sort_order=20)
    length = psvc.create(actor=actor, organization=org, code='LENGTH', name='Length', data_type=ParameterDefinition.TYPE_INTEGER)
    note = psvc.create(actor=actor, organization=org, code='NOTE', name='Note', data_type=ParameterDefinition.TYPE_TEXT)
    structure = StructureDefinitionService().create(actor=actor, organization=org, code='BOLT', name='Bolt')
    StructureDefinitionService().set_parameters(structure, rows=[
        {'parameter': material.id, 'sort_order': 10, 'requiredness': StructureParameter.REQUIRED, 'identity_defining': True},
        {'parameter': length.id, 'sort_order': 20, 'requiredness': StructureParameter.REQUIRED, 'identity_defining': True},
        {'parameter': note.id, 'sort_order': 30, 'requiredness': StructureParameter.OPTIONAL, 'identity_defining': False},
    ])
    definition = CodeDefinitionService().create(actor=actor, organization=org, structure=structure, code='INTERNAL', name='Internal', separator='-', maximum_length=40, is_default=True)
    version = CodeDefinitionVersionService().create_initial_draft(definition, actor=actor)
    CodeSegmentService().set_segments(version, rows=[
        {'segment_type': CodeSegment.TYPE_FIXED_TEXT, 'sort_order': 10, 'fixed_value': 'BLT'},
        {'segment_type': CodeSegment.TYPE_PARAMETER, 'sort_order': 20, 'parameter': material.id, 'option_encodings': [{'parameter_option': carbon.id, 'encoded_token': 'CS'}, {'parameter_option': stainless.id, 'encoded_token': 'SS'}]},
        {'segment_type': CodeSegment.TYPE_PARAMETER, 'sort_order': 30, 'parameter': length.id, 'width': 3, 'padding': '0'},
        {'segment_type': CodeSegment.TYPE_SEQUENCE, 'sort_order': 40},
    ])
    SequenceDefinition.objects.create(code_definition_version=version, scope=SequenceDefinition.SCOPE_CODE_DEFINITION, starting_value=1, current_value=0, width=3)
    active = CodeDefinitionVersionService().activate(version, actor=actor)
    template = TechnicalDataTemplateService().create(actor=actor, organization=org, structure=structure, code='BOLT_TD', name='Bolt Technical Data')
    TechnicalDataTemplateService().set_fields(template, rows=[
        {'code': 'FINISH', 'label': 'Finish', 'data_type': ParameterDefinition.TYPE_TEXT, 'scope': TechnicalFieldDefinition.SCOPE_PART, 'requiredness': TechnicalFieldDefinition.REQ_REQUIRED, 'sort_order': 10},
        {'code': 'REV_NOTE', 'label': 'Revision Note', 'data_type': ParameterDefinition.TYPE_LONG_TEXT, 'scope': TechnicalFieldDefinition.SCOPE_REVISION, 'requiredness': TechnicalFieldDefinition.REQ_RECOMMENDED, 'sort_order': 20},
    ])
    return {'actor': actor, 'org': org, 'item_type': item_type, 'structure': structure, 'definition': definition, 'version': active, 'material': material, 'carbon': carbon, 'stainless': stainless, 'length': length, 'note': note, 'template': template}


def parameter_values(ctx, length=50, option=None, note='zinc'):
    return {'MATERIAL': {'option_id': str((option or ctx['carbon']).id)}, 'LENGTH': {'value': length}, 'NOTE': {'value': note}}