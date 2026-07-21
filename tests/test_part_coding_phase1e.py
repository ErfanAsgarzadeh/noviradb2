import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from enterprise_items.coding_services import activate_template
from enterprise_items.coding_profile_services import decode_code, preview_with_profile, resolve_profile
from enterprise_items.models import ItemCodeSequence
from tests.test_part_coding_phase1c import fixture

pytestmark = pytest.mark.django_db


def api(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def test_profile_adapter_resolves_active_template_and_preview_does_not_commit_sequence():
    data = fixture()
    activate_template(data['template'], actor=data['admin'])

    resolved = resolve_profile(classification=data['bolt'])
    profile = resolved['selected_profile']

    assert profile['code'] == 'SEM'
    assert profile['status'] == 'ACTIVE'
    assert resolved['resolution_type'] == 'EXACT'

    attrs = {'DIAMETER': 12, 'LENGTH': 50, 'COATING': 'ZN'}
    preview = preview_with_profile(organization=data['org'], classification=data['bolt'], profile=profile['id'], attributes=attrs, name='Hex bolt')

    assert preview['candidate_code'] == 'M-FST-BLT-M012-050-ZN'
    assert preview['resolved_profile']['id'] == profile['id']
    assert ItemCodeSequence.objects.count() == 0


def test_decoder_returns_segment_explanation_for_known_profile():
    data = fixture()
    activate_template(data['template'], actor=data['admin'])

    decoded = decode_code(part_number='M-FST-BLT-M012-050-ZN', profile=str(data['template'].pk), classification=data['bolt'])

    assert decoded['status'] == 'DECODED'
    assert decoded['matched_profile']['code'] == 'SEM'
    assert decoded['segment_breakdown'][3]['label'] == 'Diameter'
    assert decoded['decoded_values'][0]['token'] == 'M012'


def test_coding_api_profiles_resolve_preview_and_decode():
    data = fixture()
    activate_template(data['template'], actor=data['admin'])
    client = api(data['admin'])

    profiles = client.get(reverse('coding-profiles'))
    assert profiles.status_code == status.HTTP_200_OK
    assert profiles.data['results']

    resolved = client.post(reverse('coding-resolve'), {'classification': str(data['bolt'].pk)}, format='json')
    assert resolved.status_code == status.HTTP_200_OK
    profile_id = resolved.data['selected_profile']['id']

    payload = {'organization': str(data['org'].pk), 'classification': str(data['bolt'].pk), 'profile': profile_id, 'attributes': {'DIAMETER': 12, 'LENGTH': 50, 'COATING': 'ZN'}, 'name': 'API Phase 1E Bolt'}
    preview = client.post(reverse('coding-preview'), payload, format='json')
    assert preview.status_code == status.HTTP_200_OK
    assert preview.data['candidate_code'] == 'M-FST-BLT-M012-050-ZN'

    decoded = client.post(reverse('coding-decode'), {'part_number': preview.data['candidate_code'], 'profile': profile_id}, format='json')
    assert decoded.status_code == status.HTTP_200_OK
    assert decoded.data['status'] == 'DECODED'
