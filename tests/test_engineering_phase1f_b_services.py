import pytest

from enterprise_items.engineering_domain_errors import EngineeringValidationError, ImmutableVersionError
from enterprise_items.engineering_domain_services import CodeSegmentService, ParameterDefinitionService, StructureDefinitionService, StructureValidationService
from enterprise_items.models import CodeSegment, ParameterDefinition, ParameterOption, StructureParameter
from tests.engineering_phase1f_b_fixtures import build_domain


@pytest.mark.django_db
def test_parameter_service_rejects_unsafe_data_type_change_after_values_exist():
    ctx = build_domain()
    from enterprise_items.engineering_domain_services import PartCreationService
    PartCreationService().create(actor=ctx['actor'], organization=ctx['org'], structure=ctx['structure'], item_type=ctx['item_type'], name='Bolt', parameter_values={'MATERIAL': {'option_id': str(ctx['carbon'].id)}, 'LENGTH': {'value': 50}}, part_technical_values={'FINISH': {'value': 'Painted'}}, idempotency_key='svc-unsafe')
    with pytest.raises(EngineeringValidationError):
        ParameterDefinitionService().update(ctx['length'], actor=ctx['actor'], data_type=ParameterDefinition.TYPE_TEXT)


@pytest.mark.django_db
def test_structure_service_rejects_used_parameter_removal_and_reports_ready():
    ctx = build_domain()
    report = StructureValidationService().validate(ctx['structure'])
    assert report['ready'] is True
    from enterprise_items.engineering_domain_services import PartCreationService
    PartCreationService().create(actor=ctx['actor'], organization=ctx['org'], structure=ctx['structure'], item_type=ctx['item_type'], name='Bolt', parameter_values={'MATERIAL': {'option_id': str(ctx['carbon'].id)}, 'LENGTH': {'value': 51}}, part_technical_values={'FINISH': {'value': 'Painted'}}, idempotency_key='svc-remove')
    with pytest.raises(EngineeringValidationError):
        StructureDefinitionService().set_parameters(ctx['structure'], rows=[{'parameter': ctx['material'].id, 'requiredness': StructureParameter.REQUIRED, 'identity_defining': True}])


@pytest.mark.django_db
def test_active_version_segment_mutation_is_rejected_and_token_validation_runs():
    ctx = build_domain()
    with pytest.raises(ImmutableVersionError):
        CodeSegmentService().set_segments(ctx['version'], rows=[])
    draft = ctx['definition'].versions.create(version_number=2, status='DRAFT')
    StructureParameter.objects.get_or_create(structure=ctx['structure'], parameter=ctx['material'], defaults={'sort_order': 100})
    with pytest.raises(EngineeringValidationError):
        CodeSegmentService().set_segments(draft, rows=[
            {'segment_type': CodeSegment.TYPE_PARAMETER, 'parameter': ctx['material'].id, 'option_encodings': [{'parameter_option': ctx['carbon'].id, 'encoded_token': 'AA'}, {'parameter_option': ctx['stainless'].id, 'encoded_token': 'aa'}]},
        ])