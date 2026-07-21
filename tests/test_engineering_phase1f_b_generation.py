import pytest

from enterprise_items.engineering_domain_errors import CodeDecodeError, CodeGenerationError, DuplicatePartError
from enterprise_items.engineering_domain_services import CodeDecodeService, CodePreviewService, PartCreationService, PartIdentityService
from tests.engineering_phase1f_b_fixtures import build_domain, parameter_values


@pytest.mark.django_db
def test_generation_preview_decode_identity_and_malformed_separators_are_controlled():
    ctx = build_domain()
    preview = CodePreviewService().preview(version=ctx['version'], organization=ctx['org'], parameter_values=parameter_values(ctx, length=50))
    assert preview['generated_code'] == 'BLT-CS-050-001'
    assert preview['preview_only'] is True
    assert preview['sequence']['preview'] is True
    assert 'Preview sequence is not committed' in preview['warnings'][0]
    seq = ctx['version'].sequences.get()
    assert seq.current_value == 0
    decoded = CodeDecodeService().decode(code=preview['generated_code'], version=ctx['version'])
    assert decoded['confidence'] == 100
    identity_a = PartIdentityService().build_identity(organization=ctx['org'], structure=ctx['structure'], parameter_values=parameter_values(ctx, length=50, note='a'))
    identity_b = PartIdentityService().build_identity(organization=ctx['org'], structure=ctx['structure'], parameter_values=parameter_values(ctx, length=50, note='b'))
    assert identity_a['identity_hash'] == identity_b['identity_hash']
    with pytest.raises(CodeDecodeError):
        CodeDecodeService().decode(code='BAD', version=ctx['version'])


@pytest.mark.django_db
def test_missing_mapping_inactive_option_max_length_and_duplicate_preview():
    ctx = build_domain()
    ctx['definition'].maximum_length = 5
    ctx['definition'].save()
    with pytest.raises(CodeGenerationError):
        CodePreviewService().preview(version=ctx['version'], organization=ctx['org'], parameter_values=parameter_values(ctx, length=999))
    ctx['definition'].maximum_length = 40
    ctx['definition'].save()
    PartCreationService().create(actor=ctx['actor'], organization=ctx['org'], structure=ctx['structure'], item_type=ctx['item_type'], name='Bolt A', parameter_values=parameter_values(ctx, length=60), part_technical_values={'FINISH': {'value': 'Painted'}}, idempotency_key='dup-a')
    duplicate_preview = CodePreviewService().preview(version=ctx['version'], organization=ctx['org'], parameter_values=parameter_values(ctx, length=60))
    assert duplicate_preview['duplicates'][0]['kind'] == 'SEMANTIC_IDENTITY'
    with pytest.raises(DuplicatePartError):
        PartCreationService().create(actor=ctx['actor'], organization=ctx['org'], structure=ctx['structure'], item_type=ctx['item_type'], name='Bolt B', parameter_values=parameter_values(ctx, length=60), part_technical_values={'FINISH': {'value': 'Painted'}}, idempotency_key='dup-b')