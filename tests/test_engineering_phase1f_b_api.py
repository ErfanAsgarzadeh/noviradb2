import pytest
from rest_framework.test import APIClient

from tests.engineering_phase1f_b_fixtures import build_domain, build_user, parameter_values


@pytest.mark.django_db
def test_engineering_parameter_structure_code_and_part_api_happy_path_and_errors():
    ctx = build_domain()
    client = APIClient()
    client.force_authenticate(ctx['actor'])
    resp = client.get('/api/engineering/parameters/')
    assert resp.status_code == 200
    assert any(row['code'] == 'MATERIAL' for row in resp.json())
    resp = client.post('/api/engineering/parameters/', {'organization': str(ctx['org'].id), 'code': 'COLOR', 'name': 'Color', 'data_type': 'SINGLE_SELECT'}, format='json')
    assert resp.status_code == 201
    color_id = resp.json()['id']
    resp = client.post(f'/api/engineering/parameters/{color_id}/options/bulk/', {'options': [{'display_label': 'Red', 'stored_value': 'RED'}]}, format='json')
    assert resp.status_code == 200
    resp = client.post('/api/engineering/parameters/bulk/', {'organization': str(ctx['org'].id), 'rows': [{'code': 'A', 'name': 'A', 'data_type': 'TEXT'}, {'code': 'A', 'name': 'Dup', 'data_type': 'TEXT'}]}, format='json')
    assert resp.status_code == 400
    assert resp.json()['committed'] is False
    resp = client.get('/api/engineering/structures/')
    assert resp.status_code == 200
    resp = client.post(f"/api/engineering/structures/{ctx['structure'].id}/validate/")
    assert resp.status_code == 200
    assert resp.json()['ready'] is True
    resp = client.post('/api/engineering/parts/preview/', {'organization': str(ctx['org'].id), 'structure': str(ctx['structure'].id), 'parameter_values': parameter_values(ctx, length=90)}, format='json')
    assert resp.status_code == 200
    assert resp.json()['generated_code'] == 'BLT-CS-090-001'
    resp = client.post('/api/engineering/parts/create/', {'organization': str(ctx['org'].id), 'structure': str(ctx['structure'].id), 'item_type': str(ctx['item_type'].id), 'name': 'Bolt 90', 'parameter_values': parameter_values(ctx, length=90), 'part_technical_values': {'FINISH': {'value': 'Painted'}}, 'idempotency_key': 'api-90'}, format='json')
    assert resp.status_code == 201
    part_id = resp.json()['item']['id']
    resp = client.post('/api/engineering/parts/create/', {'organization': str(ctx['org'].id), 'structure': str(ctx['structure'].id), 'item_type': str(ctx['item_type'].id), 'name': 'Bolt 90', 'parameter_values': parameter_values(ctx, length=90), 'part_technical_values': {'FINISH': {'value': 'Painted'}}, 'idempotency_key': 'api-90'}, format='json')
    assert resp.status_code == 200
    assert resp.json()['idempotent_replay'] is True
    resp = client.post('/api/engineering/parts/decode/', {'code': 'BLT-CS-090-001', 'version': str(ctx['version'].id)}, format='json')
    assert resp.status_code == 200
    resp = client.get(f'/api/engineering/parts/{part_id}/engineering-context/')
    assert resp.status_code == 200
    assert resp.json()['coding_snapshot']['final_part_number'] == 'BLT-CS-090-001'


@pytest.mark.django_db
def test_engineering_api_requires_authentication():
    client = APIClient()
    assert client.get('/api/engineering/parameters/').status_code in (401, 403)

@pytest.mark.django_db
def test_engineering_schema_and_duplicate_error_shape_are_authenticated():
    ctx = build_domain()
    client = APIClient()
    client.force_authenticate(ctx['actor'])
    schema = client.get('/api/engineering/schema/')
    assert schema.status_code == 200
    assert schema.json()['schema_version'] == 'phase1f-b'
    assert any(row['path'] == 'parts/create/' for row in schema.json()['endpoints'])
    first = client.post('/api/engineering/parts/create/', {'organization': str(ctx['org'].id), 'structure': str(ctx['structure'].id), 'item_type': str(ctx['item_type'].id), 'name': 'Bolt duplicate source', 'parameter_values': parameter_values(ctx, length=75), 'part_technical_values': {'FINISH': {'value': 'Painted'}}, 'idempotency_key': 'dup-shape-a'}, format='json')
    assert first.status_code == 201
    duplicate = client.post('/api/engineering/parts/create/', {'organization': str(ctx['org'].id), 'structure': str(ctx['structure'].id), 'item_type': str(ctx['item_type'].id), 'name': 'Bolt duplicate blocked', 'parameter_values': parameter_values(ctx, length=75), 'part_technical_values': {'FINISH': {'value': 'Painted'}}, 'idempotency_key': 'dup-shape-b'}, format='json')
    assert duplicate.status_code == 409
    payload = duplicate.json()['error']
    assert payload['code'] == 'duplicate_part'
    assert payload['metadata']['existing_part_number'] == 'BLT-CS-075-001'
    assert payload['metadata']['duplicates'][0]['reason']
