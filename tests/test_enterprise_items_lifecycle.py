from datetime import date

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from auditlog.models import AuditEvent
from enterprise_items.models import CodingOrganization, Item, ItemRevision, ItemType
from enterprise_items.serializers import ItemRevisionSerializer, ItemSerializer
from enterprise_items.services import (
    EngineeringLifecycleError,
    EngineeringPermissionError,
    approve_revision,
    release_revision,
    submit_revision_for_review,
)
from tests.factories import make_company_admin, make_member, make_project_manager

pytestmark = pytest.mark.django_db


def api(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def make_item(code='PART-001', *, status_value='DRAFT', is_active=True):
    org = CodingOrganization.objects.create(name=f'Engineering {code}', code=f'ENG-{code[-3:]}')
    item_type = ItemType.objects.create(organization=org, name='Part', code=f'PART-{code[-3:]}', group='PART')
    return Item.objects.create(
        organization=org,
        item_code=code,
        name=f'Part {code}',
        item_type=item_type,
        base_unit='ea',
        status=status_value,
        tracking_mode='BATCH',
        make_or_buy='MAKE',
        is_active=is_active,
    )


def make_revision(item, revision='R0', *, status_value=ItemRevision.STATUS_DRAFT, effective_from=None, effective_to=None):
    return ItemRevision.objects.create(
        item=item,
        revision=revision,
        title=f'{item.item_code} {revision}',
        effective_from=effective_from,
        effective_to=effective_to,
        status=status_value,
        drawing_no=f'DRW-{revision}',
        specification='baseline technical spec',
    )


class TestItemIdentityValidation:
    def test_item_serializer_normalizes_code_and_unit(self):
        item = make_item('PART-010')
        serializer = ItemSerializer(instance=item, data={'item_code': ' part-010 ', 'base_unit': ' kg '}, partial=True)

        assert serializer.is_valid(), serializer.errors
        updated = serializer.save()

        assert updated.item_code == 'part-010'
        assert updated.base_unit == 'KG'

    def test_item_code_is_case_insensitive_within_organization(self):
        item = make_item('PART-020')
        serializer = ItemSerializer(data={
            'organization': str(item.organization_id),
            'item_code': 'part-020',
            'name': 'Duplicate code',
            'item_type': str(item.item_type_id),
            'base_unit': 'EA',
            'tracking_mode': 'NONE',
            'make_or_buy': 'BUY',
        })

        assert not serializer.is_valid()
        assert 'item_code' in serializer.errors

    def test_revision_code_is_unique_per_item_but_allowed_on_other_items(self):
        item = make_item('PART-030')
        other = make_item('PART-031')
        make_revision(item, 'A')

        with pytest.raises(ValidationError):
            make_revision(item, 'a')

        assert make_revision(other, 'A').revision == 'A'

    def test_revision_rejects_invalid_effective_range(self):
        item = make_item('PART-040')

        with pytest.raises(ValidationError):
            make_revision(item, 'A', effective_from=date(2026, 7, 18), effective_to=date(2026, 7, 17))


class TestRevisionLifecycleServices:
    def test_draft_review_approve_release_sets_metadata_and_item_default(self):
        admin = make_company_admin()
        item = make_item('PART-100')
        revision = make_revision(item, 'A', effective_from=date(2026, 8, 1))

        revision = submit_revision_for_review(revision, actor=admin)
        assert revision.status == ItemRevision.STATUS_UNDER_REVIEW
        assert revision.submitted_by == admin
        assert revision.submitted_at is not None

        revision = approve_revision(revision, actor=admin)
        assert revision.status == ItemRevision.STATUS_APPROVED
        assert revision.approved_by == admin
        assert revision.approved_at is not None

        revision = release_revision(revision, actor=admin)
        item.refresh_from_db()

        assert revision.status == ItemRevision.STATUS_RELEASED
        assert revision.released_by == admin
        assert revision.released_at is not None
        assert item.default_revision == 'A'
        assert item.status == 'ACTIVE'
        assert AuditEvent.objects.filter(action='item_revision_released', target_id=str(revision.pk)).exists()

    def test_release_requires_valid_transition(self):
        admin = make_company_admin()
        revision = make_revision(make_item('PART-110'), 'A', effective_from=date(2026, 8, 1))

        with pytest.raises(EngineeringLifecycleError):
            release_revision(revision, actor=admin)

    def test_project_manager_can_submit_but_cannot_release(self):
        manager = make_project_manager()
        revision = make_revision(make_item('PART-120'), 'A', effective_from=date(2026, 8, 1))

        assert submit_revision_for_review(revision, actor=manager).status == ItemRevision.STATUS_UNDER_REVIEW

        revision.status = ItemRevision.STATUS_APPROVED
        revision.save(update_fields=['status'])
        with pytest.raises(EngineeringPermissionError):
            release_revision(revision, actor=manager)

    def test_supersede_closes_current_release_without_overlap(self):
        admin = make_company_admin()
        item = make_item('PART-130')
        old = make_revision(item, 'A', status_value=ItemRevision.STATUS_APPROVED, effective_from=date(2026, 8, 1))
        old = release_revision(old, actor=admin)
        new = make_revision(item, 'B', status_value=ItemRevision.STATUS_APPROVED, effective_from=date(2026, 9, 1))

        new = release_revision(new, actor=admin, supersede_current=True)
        old.refresh_from_db()
        item.refresh_from_db()

        assert old.status == ItemRevision.STATUS_SUPERSEDED
        assert old.effective_to == date(2026, 8, 31)
        assert old.superseded_by == new
        assert new.status == ItemRevision.STATUS_RELEASED
        assert item.default_revision == 'B'

    def test_second_current_release_requires_explicit_supersede(self):
        admin = make_company_admin()
        item = make_item('PART-140')
        release_revision(make_revision(item, 'A', status_value=ItemRevision.STATUS_APPROVED, effective_from=date(2026, 8, 1)), actor=admin)
        next_revision = make_revision(item, 'B', status_value=ItemRevision.STATUS_APPROVED, effective_from=date(2026, 9, 1))

        with pytest.raises(EngineeringLifecycleError):
            release_revision(next_revision, actor=admin)


class TestRevisionApi:
    def test_revision_actions_are_available_through_existing_router(self):
        admin = make_company_admin()
        revision = make_revision(make_item('PART-200'), 'A', effective_from=date(2026, 8, 1))
        client = api(admin)

        submit = client.post(reverse('item-revision-submit', kwargs={'pk': revision.pk}))
        assert submit.status_code == status.HTTP_200_OK
        assert submit.data['status'] == ItemRevision.STATUS_UNDER_REVIEW

        approve = client.post(reverse('item-revision-approve', kwargs={'pk': revision.pk}))
        assert approve.status_code == status.HTTP_200_OK
        assert approve.data['status'] == ItemRevision.STATUS_APPROVED

        release = client.post(reverse('item-revision-release', kwargs={'pk': revision.pk}))
        assert release.status_code == status.HTTP_200_OK
        assert release.data['status'] == ItemRevision.STATUS_RELEASED
        assert release.data['released_by'] == admin.id

    def test_member_cannot_create_revision(self):
        item = make_item('PART-210')
        response = api(make_member()).post(reverse('item-revision-list'), {
            'item': str(item.pk),
            'revision': 'A',
            'title': 'Unauthorized',
        }, format='json')

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_status_cannot_be_changed_by_generic_patch(self):
        admin = make_company_admin()
        revision = make_revision(make_item('PART-220'), 'A')

        response = api(admin).patch(
            reverse('item-revision-detail', kwargs={'pk': revision.pk}),
            {'status': ItemRevision.STATUS_RELEASED},
            format='json',
        )
        revision.refresh_from_db()

        assert response.status_code == status.HTTP_200_OK
        assert revision.status == ItemRevision.STATUS_DRAFT

    def test_released_revision_technical_fields_are_immutable(self):
        admin = make_company_admin()
        revision = release_revision(
            make_revision(make_item('PART-230'), 'A', status_value=ItemRevision.STATUS_APPROVED, effective_from=date(2026, 8, 1)),
            actor=admin,
        )

        serializer = ItemRevisionSerializer(instance=revision, data={'drawing_no': 'DRW-CHANGED'}, partial=True)

        assert not serializer.is_valid()
        assert 'drawing_no' in serializer.errors

    def test_only_draft_revision_can_be_deleted(self):
        admin = make_company_admin()
        item = make_item('PART-240')
        draft = make_revision(item, 'A')
        released = release_revision(
            make_revision(item, 'B', status_value=ItemRevision.STATUS_APPROVED, effective_from=date(2026, 8, 1)),
            actor=admin,
        )

        blocked = api(admin).delete(reverse('item-revision-detail', kwargs={'pk': released.pk}))
        removed = api(admin).delete(reverse('item-revision-detail', kwargs={'pk': draft.pk}))

        assert blocked.status_code == status.HTTP_400_BAD_REQUEST
        assert removed.status_code == status.HTTP_204_NO_CONTENT
