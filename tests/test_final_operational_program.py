from __future__ import annotations

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from release_readiness.models import (
    BackupEvidence,
    CutoverRehearsal,
    MasterDataBatch,
    OperationalDefect,
    PilotScope,
    PilotSignoffRecord,
    PilotSignoffRequirement,
    PilotUserProvisioning,
    ProductionPilot,
    ReconciliationSnapshot,
    TrainingAssignment,
    TrainingCourse,
    UATCampaign,
    UATExecution,
    UATScenario,
)
from release_readiness.services import (
    create_default_signoff_checklist,
    evaluate_signoff_checklist,
    final_technical_status,
    freeze_release_baseline,
    pilot_gate_summary,
    record_signoff,
    transition_pilot,
)
from tests.factories import make_company_admin
from tests.test_opc_graph_persistence_phase3 import api


pytestmark = pytest.mark.django_db


def staff_user(username='final-admin'):
    user = make_company_admin()
    user.username = username
    user.is_staff = True
    user.is_superuser = False
    user.save(update_fields=['username', 'is_staff', 'is_superuser'])
    return user


def baseline(actor):
    return freeze_release_baseline(
        actor=actor,
        release_name='Novira Final Program',
        evidence={
            'backend_manifest_result': '78 passed',
            'frontend_manifest_result': '45 passed',
            'playwright_result': '4 passed',
            'backup_artifact_identity': 'phase16-final',
            'restore_rehearsal_identity': 'phase16-restore',
        },
    )


def pilot_fixture(actor):
    release = baseline(actor)
    checklist = create_default_signoff_checklist(actor=actor, release_baseline=release)
    pilot = ProductionPilot.objects.create(
        pilot_number='PILOT-FINAL-001',
        name='Final controlled production pilot',
        release_baseline=release,
        checklist=checklist,
        incident_owner_role='Incident Commander',
        rollback_plan='Restore verified backup and freeze writes.',
        technical_gate_passed=True,
        uat_gate_passed=True,
        master_data_gate_passed=True,
        backup_gate_passed=True,
    )
    PilotScope.objects.create(pilot=pilot, plants=['PLANT-1'], warehouses=['WH-1'], projects=['PROJ-1'], enabled_domains=['production', 'inventory', 'quality'])
    PilotUserProvisioning.objects.create(
        pilot=pilot,
        user=actor,
        organizational_role='Pilot Administrator',
        plant_scope=['PLANT-1'],
        warehouse_scope=['WH-1'],
        training_state=PilotUserProvisioning.TRAINING_COMPLETE,
        account_state=PilotUserProvisioning.ACCOUNT_ACTIVE,
        approver=actor,
    )
    BackupEvidence.objects.create(
        evidence_number='BKP-FINAL-001',
        database_name='novira_phase16_clean',
        artifact_identity='novira_phase16_clean_20260801083734.dump',
        checksum='499ABFF0F7E06A5F6C9EE90B9C58821F9C80DDD02F579320670C9D8B5CDC06C3',
        table_count=246,
        index_count=1553,
        restored_database_name='novira_phase16_restore',
        restore_verified=True,
    )
    CutoverRehearsal.objects.create(
        pilot=pilot,
        rehearsal_number='CUT-FINAL-001',
        started_at=timezone.now(),
        ended_at=timezone.now(),
        steps=['backup', 'migrate', 'import dry run', 'reconcile', 'smoke', 'activation simulation', 'rollback simulation', 'restore'],
        backup_reference='BKP-FINAL-001',
        reconciliation_reference='REC-FINAL-001',
        smoke_test_reference='public-smoke-4-passed',
        activation_simulation=True,
        rollback_simulation=True,
        restore_reference='novira_phase16_restore',
        result=CutoverRehearsal.RESULT_PASS,
    )
    ReconciliationSnapshot.objects.create(pilot=pilot, snapshot_number='REC-FINAL-001', snapshot_type='daily', result=ReconciliationSnapshot.RESULT_CLEAN, created_by=actor)
    return pilot


def approve_all(checklist, actor):
    for requirement in checklist.requirements.all():
        record_signoff(requirement=requirement, actor=actor, decision=PilotSignoffRecord.DECISION_APPROVED, evidence_reference=f'evidence:{requirement.area}')


def test_default_signoff_checklist_is_fail_closed_and_records_are_immutable():
    admin = staff_user('signoff-admin')
    checklist = create_default_signoff_checklist(actor=admin, release_baseline=baseline(admin))
    evaluation = evaluate_signoff_checklist(checklist)
    assert evaluation['total_mandatory'] == 15
    assert evaluation['pending'] == 15
    assert evaluation['readiness_decision'] == 'BLOCKED'

    requirement = checklist.requirements.get(area='security')
    with pytest.raises(ValidationError):
        record_signoff(requirement=requirement, actor=admin, decision=PilotSignoffRecord.DECISION_APPROVED_WITH_CONDITIONS)
    record = record_signoff(requirement=requirement, actor=admin, decision=PilotSignoffRecord.DECISION_REJECTED, comment='Needs review')
    with pytest.raises(ValidationError):
        record.comment = 'mutated'
        record.save()
    with pytest.raises(ValidationError):
        record.delete()


def test_wrong_role_signoff_and_activation_with_pending_signoffs_are_blocked():
    admin = staff_user('gate-admin')
    other = staff_user('gate-other')
    pilot = pilot_fixture(admin)
    req = pilot.checklist.requirements.get(area='security')
    req.assigned_user = admin
    req.save(update_fields=['assigned_user'])
    other.is_staff = False
    other.save(update_fields=['is_staff'])
    with pytest.raises(PermissionDenied):
        record_signoff(requirement=req, actor=other, decision=PilotSignoffRecord.DECISION_APPROVED)

    pilot.status = ProductionPilot.STATUS_READY
    pilot.save(update_fields=['status'])
    with pytest.raises(ValidationError):
        transition_pilot(pilot=pilot, actor=admin, action='activate', expected_version=pilot.activation_version)
    assert 'signoff_incomplete' in pilot_gate_summary(pilot)['blockers']


def test_pilot_lifecycle_activation_requires_gates_and_writes_outbox(settings):
    if connection.vendor != 'postgresql':
        pytest.skip('Production activation gate requires PostgreSQL readiness.')
    admin = staff_user('activate-admin')
    settings.DEBUG = False
    settings.ALLOWED_HOSTS = ['novira.example.test']
    settings.SESSION_COOKIE_SECURE = True
    settings.CSRF_COOKIE_SECURE = True
    settings.SECURE_SSL_REDIRECT = True
    settings.SECRET_KEY = 'not-default-final-program-secret'
    settings.NOVIRA_BACKUP_DESTINATION_CLASS = 'managed-postgresql-backup'
    settings.NOVIRA_MONITORING_DESTINATION_CLASS = 'operations-monitoring'
    pilot = pilot_fixture(admin)
    approve_all(pilot.checklist, admin)

    pilot = transition_pilot(pilot=pilot, actor=admin, action='start_preparation', expected_version=0)
    pilot = transition_pilot(pilot=pilot, actor=admin, action='request_signoff', expected_version=1)
    pilot = transition_pilot(pilot=pilot, actor=admin, action='mark_ready', expected_version=2)
    activated = transition_pilot(pilot=pilot, actor=admin, action='activate', expected_version=3)
    assert activated.status == ProductionPilot.STATUS_ACTIVE
    assert activated.activated_by == admin


def test_data_onboarding_uat_training_reconciliation_and_access_review_are_truthful():
    admin = staff_user('ops-admin')
    pilot = pilot_fixture(admin)
    batch = MasterDataBatch.objects.create(
        batch_number='MDB-FINAL-001',
        pilot=pilot,
        domain='suppliers',
        schema_version='supplier-basic-v1',
        owner_role='Procurement Lead',
        approver_role='Procurement Approver',
        idempotency_key='mdb-final-001',
        dry_run_summary={'valid_rows': 2, 'error_rows': 0},
        reconciliation_summary={'duplicates': 0, 'orphan_references': 0},
        row_count=2,
        status=MasterDataBatch.STATUS_RECONCILED,
        approved_by=admin,
    )
    assert batch.status == MasterDataBatch.STATUS_RECONCILED

    campaign = UATCampaign.objects.create(pilot=pilot, campaign_number='UAT-FINAL-001', title='Pilot UAT', technical_gate_passed=True)
    scenario = UATScenario.objects.create(campaign=campaign, scenario_number='UAT-001', domain='inventory', title='Receive and reconcile inventory')
    execution = UATExecution.objects.create(scenario=scenario, human_actor=admin, result=UATExecution.RESULT_PASS, evidence_reference='automated-plus-human-record-supported')
    with pytest.raises(ValidationError):
        execution.result = UATExecution.RESULT_FAIL
        execution.save()

    course = TrainingCourse.objects.create(course_code='TRN-PILOT-ADMIN', title='Pilot administrator operations', role='Pilot Administrator')
    assignment = TrainingAssignment.objects.create(course=course, user=admin, pilot=pilot, status=TrainingAssignment.STATUS_COMPLETED, completed_at=timezone.now())
    assert assignment.status == TrainingAssignment.STATUS_COMPLETED
    assert pilot.user_provisioning.first().training_state == PilotUserProvisioning.TRAINING_COMPLETE

    clean = ReconciliationSnapshot.objects.create(pilot=pilot, snapshot_number='REC-FINAL-002', snapshot_type='inventory', result=ReconciliationSnapshot.RESULT_CLEAN, created_by=admin)
    with pytest.raises(ValidationError):
        clean.result = ReconciliationSnapshot.RESULT_MISMATCH
        clean.save()


def test_final_technical_status_separates_human_signoff_from_technical_failure(settings):
    if connection.vendor != 'postgresql':
        pytest.skip('Final technical-ready state requires PostgreSQL production configuration.')
    admin = staff_user('status-admin')
    settings.DEBUG = False
    settings.ALLOWED_HOSTS = ['novira.example.test']
    settings.SESSION_COOKIE_SECURE = True
    settings.CSRF_COOKIE_SECURE = True
    settings.SECURE_SSL_REDIRECT = True
    settings.SECRET_KEY = 'not-default-final-program-secret'
    settings.NOVIRA_BACKUP_DESTINATION_CLASS = 'managed-postgresql-backup'
    settings.NOVIRA_MONITORING_DESTINATION_CLASS = 'operations-monitoring'
    pilot = pilot_fixture(admin)
    status_payload = final_technical_status(pilot)
    assert status_payload['status'] == 'TECHNICALLY_READY_HUMAN_SIGNOFF_PENDING'
    OperationalDefect.objects.create(defect_number='DEF-FINAL-001', pilot=pilot, severity=OperationalDefect.SEV_HIGH, title='Blocking defect')
    blocked = final_technical_status(pilot)
    assert blocked['status'] == 'NOT_READY'
    assert 'critical_or_high_defects_open' in blocked['blockers']


def test_operations_api_exposes_gate_and_blocks_activation():
    admin = staff_user('api-admin')
    pilot = pilot_fixture(admin)
    pilot.status = ProductionPilot.STATUS_READY
    pilot.save(update_fields=['status'])
    client = api(admin)
    gate = client.get(reverse('release-pilot-gate', kwargs={'pk': pilot.pk}))
    assert gate.status_code == status.HTTP_200_OK
    assert gate.data['ready'] is False
    activate = client.post(reverse('release-pilot-transition', kwargs={'pk': pilot.pk}), {'action': 'activate', 'expected_version': 0}, format='json')
    assert activate.status_code == status.HTTP_400_BAD_REQUEST
