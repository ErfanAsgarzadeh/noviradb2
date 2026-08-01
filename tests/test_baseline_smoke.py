import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient


@pytest.mark.django_db
def test_core_api_mounts_require_authentication():
    """Critical API mounts resolve and retain the global auth boundary."""
    client = APIClient()

    for url_name in ('opc-diagram-list', 'enterprise-item-list', 'project-list'):
        response = client.get(reverse(url_name))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
