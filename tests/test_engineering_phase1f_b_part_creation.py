import pytest

from enterprise_items.engineering_domain_errors import DuplicatePartError, TechnicalDataValidationError
from enterprise_items.engineering_domain_services import PartCreationService
from enterprise_items.models import Item, ItemRevision, PartParameterValue, PartTechnicalValue, RevisionTechnicalValue
from tests.engineering_phase1f_b_fixtures import build_domain, parameter_values


@pytest.mark.django_db
def test_part_creation_is_atomic_persists_values_snapshot_and_idempotency():
    ctx = build_domain()
    service = PartCreationService()
    result = service.create(actor=ctx['actor'], organization=ctx['org'], structure=ctx['structure'], item_type=ctx['item_type'], name='Bolt 70', parameter_values=parameter_values(ctx, length=70), part_technical_values={'FINISH': {'value': 'Painted'}}, revision_technical_values={'REV_NOTE': {'value': 'Initial'}}, idempotency_key='part-70')
    item = result['item']
    assert item.item_code == 'BLT-CS-070-001'
    assert item.structure == ctx['structure']
    assert item.code_definition == ctx['definition']
    assert item.code_definition_version == ctx['version']
    assert item.coding_snapshot['schema_version'] == 1
    assert item.semantic_identity_hash == item.coding_snapshot['identity_hash']
    assert ItemRevision.objects.filter(item=item).count() == 1
    assert PartParameterValue.objects.filter(part=item).count() == 3
    assert PartTechnicalValue.objects.filter(part=item).count() == 1
    assert RevisionTechnicalValue.objects.filter(revision__item=item).count() == 1
    replay = service.create(actor=ctx['actor'], organization=ctx['org'], structure=ctx['structure'], item_type=ctx['item_type'], name='Bolt 70', parameter_values=parameter_values(ctx, length=70), part_technical_values={'FINISH': {'value': 'Painted'}}, idempotency_key='part-70')
    assert replay['idempotent_replay'] is True
    assert Item.objects.filter(item_code=item.item_code).count() == 1


@pytest.mark.django_db
def test_part_creation_rollback_on_technical_failure_and_duplicate_blocking():
    ctx = build_domain()
    before = Item.objects.count()
    with pytest.raises(TechnicalDataValidationError):
        PartCreationService().create(actor=ctx['actor'], organization=ctx['org'], structure=ctx['structure'], item_type=ctx['item_type'], name='Bad', parameter_values=parameter_values(ctx, length=80), part_technical_values={}, idempotency_key='bad-tech')
    assert Item.objects.count() == before
    PartCreationService().create(actor=ctx['actor'], organization=ctx['org'], structure=ctx['structure'], item_type=ctx['item_type'], name='Bolt 81', parameter_values=parameter_values(ctx, length=81), part_technical_values={'FINISH': {'value': 'Painted'}}, idempotency_key='dup-81')
    with pytest.raises(DuplicatePartError):
        PartCreationService().create(actor=ctx['actor'], organization=ctx['org'], structure=ctx['structure'], item_type=ctx['item_type'], name='Bolt 81 duplicate', parameter_values=parameter_values(ctx, length=81), part_technical_values={'FINISH': {'value': 'Painted'}}, idempotency_key='dup-81b')