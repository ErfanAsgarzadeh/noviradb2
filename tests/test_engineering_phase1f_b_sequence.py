import pytest
from concurrent.futures import ThreadPoolExecutor
from django.db import close_old_connections

from enterprise_items.engineering_domain_errors import SequenceExhaustedError
from enterprise_items.engineering_domain_services import SequenceAllocationService
from enterprise_items.models import SequenceDefinition
from tests.engineering_phase1f_b_fixtures import build_domain


@pytest.mark.django_db(transaction=True)
def test_sequence_preview_does_not_increment_commit_allocates_unique_and_exhausts():
    ctx = build_domain()
    seq = ctx['version'].sequences.get()
    assert SequenceAllocationService().next_value(ctx['version'], preview=True)[0] == '001'
    seq.refresh_from_db()
    assert seq.current_value == 0
    assert SequenceAllocationService().next_value(ctx['version'], preview=False)[0] == '001'
    assert SequenceAllocationService().next_value(ctx['version'], preview=False)[0] == '002'
    seq.refresh_from_db()
    assert seq.current_value == 2
    seq.current_value = 999
    seq.save()
    with pytest.raises(SequenceExhaustedError):
        SequenceAllocationService().next_value(ctx['version'], preview=False)


@pytest.mark.django_db(transaction=True)
def test_concurrent_sequence_allocations_are_unique_and_scope_keys_are_supported():
    ctx = build_domain()
    def allocate(_):
        close_old_connections()
        return SequenceAllocationService().next_value(ctx['version'], preview=False)[0]
    with ThreadPoolExecutor(max_workers=5) as pool:
        values = list(pool.map(allocate, range(5)))
    assert len(values) == len(set(values)) == 5
    version = ctx['version']
    version.sequences.all().delete()
    SequenceDefinition.objects.create(code_definition_version=version, scope=SequenceDefinition.SCOPE_PREFIX, scope_key='BLT-CS', starting_value=1, current_value=0, width=2)
    assert SequenceAllocationService().next_value(version, preview=True, prefix='blt-cs')[0] == '01'