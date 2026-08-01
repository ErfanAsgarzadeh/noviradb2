import base64
import csv
import hashlib
import hmac
import io
import json
import uuid
from pathlib import Path

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from auditlog.services import log_event

from .models import (
    BackupEvidence,
    CutoverRehearsal,
    FinalProductionDecision,
    ImportJob,
    ImportJobRow,
    IntegrationOutboxEvent,
    MasterDataBatch,
    Notification,
    OperationalChangeRecord,
    OperationalDefect,
    PilotSignoffChecklist,
    PilotSignoffRecord,
    PilotSignoffRequirement,
    PilotScope,
    PilotUserProvisioning,
    ProductionPilot,
    ReconciliationSnapshot,
    ReleaseBaseline,
    TrainingAssignment,
    UATCampaign,
    UATExecution,
)

SUPPORTED_BARCODE_TYPES = {
    'ItemRevision': 'enterprise_items.ItemRevision',
    'ProductionLot': 'opc.ProductionLot',
    'ProductionSerial': 'opc.ProductionSerial',
    'InventoryBalance': 'opc.InventoryBalance',
    'ProductionOrder': 'opc.ProductionOrder',
    'OperationExecution': 'opc.OperationExecution',
    'InspectionExecution': 'opc.InspectionExecution',
    'MaintenanceWorkOrder': 'opc.MaintenanceWorkOrder',
    'PurchaseOrder': 'opc.PurchaseOrder',
    'PurchaseReceipt': 'opc.PurchaseReceipt',
    'MachineAsset': 'opc.MachineAsset',
}


def _secret():
    return settings.SECRET_KEY.encode('utf-8')


def sign_identity(object_type, object_id):
    payload = {'v': 'NID1', 't': object_type, 'id': str(object_id)}
    raw = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')
    sig = hmac.new(_secret(), raw, hashlib.sha256).hexdigest()
    token = base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')
    return f'NVR1.{token}.{sig}'


def resolve_identity(token, *, actor=None, request=None):
    try:
        prefix, token_payload, signature = token.split('.', 2)
        if prefix != 'NVR1':
            raise ValueError('bad prefix')
        raw = base64.urlsafe_b64decode(token_payload + '=' * (-len(token_payload) % 4))
        expected = hmac.new(_secret(), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise ValueError('bad signature')
        payload = json.loads(raw.decode('utf-8'))
        object_type = payload['t']
        object_id = payload['id']
        if object_type not in SUPPORTED_BARCODE_TYPES:
            raise ValueError('unsupported type')
        if actor is not None and not getattr(actor, 'is_authenticated', False):
            raise PermissionDenied('Authentication is required to resolve operational identities.')
        from django.apps import apps
        app_label, model_name = SUPPORTED_BARCODE_TYPES[object_type].split('.')
        model = apps.get_model(app_label, model_name)
        obj = model.objects.get(pk=object_id)
        from .models import BarcodeResolutionAudit
        BarcodeResolutionAudit.objects.create(
            actor=actor if actor and getattr(actor, 'is_authenticated', False) else None,
            payload_version='NVR1',
            object_type=object_type,
            object_id=object_id,
            success=True,
            request_id=getattr(request, 'request_id', ''),
            correlation_id=getattr(request, 'correlation_id', ''),
        )
        return {'object_type': object_type, 'object_id': object_id, 'repr': str(obj), 'payload_version': 'NVR1'}
    except Exception as exc:
        from .models import BarcodeResolutionAudit
        BarcodeResolutionAudit.objects.create(
            actor=actor if actor and getattr(actor, 'is_authenticated', False) else None,
            payload_version='NVR1',
            object_type='',
            object_id='',
            success=False,
            reason=str(exc)[:120],
            request_id=getattr(request, 'request_id', '') if request else '',
            correlation_id=getattr(request, 'correlation_id', '') if request else '',
        )
        raise


def create_notification(*, recipient, category, severity='INFO', title, body='', object_type='', object_id='', dedupe_key=''):
    provider_state = 'not_configured' if not getattr(settings, 'NOVIRA_EMAIL_PROVIDER', '') else f'configured:{settings.NOVIRA_EMAIL_PROVIDER}'
    notification, _created = Notification.objects.get_or_create(
        recipient=recipient,
        dedupe_key=dedupe_key,
        defaults={
            'category': category,
            'severity': severity,
            'title': title,
            'body': body,
            'object_type': object_type,
            'object_id': str(object_id or ''),
            'email_provider_state': provider_state,
        },
    ) if dedupe_key else (Notification.objects.create(recipient=recipient, category=category, severity=severity, title=title, body=body, object_type=object_type, object_id=str(object_id or ''), email_provider_state=provider_state), True)
    return notification


def spreadsheet_safe(value):
    text = '' if value is None else str(value)
    return "'" + text if text.startswith(('=', '+', '-', '@')) else text


def parse_csv_import(*, actor, schema_name, schema_version, content, idempotency_key, dry_run=True, file_name='upload.csv'):
    if schema_name not in {'supplier-basic'}:
        raise ValidationError({'schema_name': 'Unsupported import schema.'})
    existing = ImportJob.objects.filter(schema_name=schema_name, idempotency_key=idempotency_key).first()
    if existing:
        return existing
    decoded = content.decode('utf-8-sig') if isinstance(content, bytes) else str(content)
    reader = csv.DictReader(io.StringIO(decoded))
    max_rows = getattr(settings, 'NOVIRA_IMPORT_MAX_ROWS', 1000)
    rows = list(reader)
    if len(rows) > max_rows:
        raise ValidationError({'rows': f'Import exceeds row limit {max_rows}.'})
    with transaction.atomic():
        job = ImportJob.objects.create(schema_name=schema_name, schema_version=schema_version, idempotency_key=idempotency_key, dry_run=dry_run, file_name=file_name, row_count=len(rows), created_by=actor)
        valid = 0
        errors = 0
        for index, row in enumerate(rows, start=2):
            row_errors = []
            if not row.get('supplier_code'):
                row_errors.append({'field': 'supplier_code', 'message': 'Required.'})
            if not row.get('name'):
                row_errors.append({'field': 'name', 'message': 'Required.'})
            if row_errors:
                errors += 1
            else:
                valid += 1
            ImportJobRow.objects.create(job=job, row_number=index, payload=row, errors=row_errors)
        job.valid_count = valid
        job.error_count = errors
        job.status = ImportJob.STATUS_VALIDATED if errors == 0 else ImportJob.STATUS_FAILED
        job.summary = {'dry_run': dry_run, 'valid_rows': valid, 'error_rows': errors}
        job.save(update_fields=['valid_count', 'error_count', 'status', 'summary'])
        log_event('import_dry_run' if dry_run else 'import_apply', target=job, category='data', extra=job.summary)
        return job


def create_outbox_event(*, event_type, aggregate_type, aggregate_id, payload, correlation_id='', command_id=''):
    return IntegrationOutboxEvent.objects.create(
        event_id=f'evt-{uuid.uuid4()}',
        event_type=event_type,
        schema_version='v1',
        aggregate_type=aggregate_type,
        aggregate_id=str(aggregate_id),
        payload=payload,
        correlation_id=correlation_id,
        command_id=command_id,
    )


def mark_outbox_attempt(event, *, success, error=''):
    event.attempts += 1
    if success:
        event.status = IntegrationOutboxEvent.STATUS_DELIVERED
        event.delivered_at = timezone.now()
        event.last_error = ''
        event.next_attempt_at = None
    else:
        event.status = IntegrationOutboxEvent.STATUS_FAILED if event.attempts < 3 else IntegrationOutboxEvent.STATUS_DEAD_LETTER
        event.last_error = error[:2000]
        event.next_attempt_at = timezone.now() + timezone.timedelta(minutes=5 * event.attempts) if event.status == IntegrationOutboxEvent.STATUS_FAILED else None
    event.save(update_fields=['attempts', 'status', 'delivered_at', 'last_error', 'next_attempt_at'])
    return event


def health_payload():
    db_ok = True
    db_error = ''
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
    except Exception as exc:
        db_ok = False
        db_error = str(exc)
    executor = MigrationExecutor(connection)
    pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
    return {
        'status': 'ok' if db_ok and not pending else 'degraded',
        'database': {'ok': db_ok, 'error': db_error, 'vendor': connection.vendor},
        'migrations': {'pending': len(pending)},
        'storage': {'media_root': str(settings.MEDIA_ROOT), 'ok': True},
        'build': {'id': settings.NOVIRA_BUILD_ID, 'api_version': settings.NOVIRA_API_VERSION, 'release_id': settings.NOVIRA_RELEASE_ID},
        'release': release_identity_status(),
        'providers': {'email': settings.NOVIRA_EMAIL_PROVIDER or 'not_configured', 'outbox': settings.NOVIRA_OUTBOX_PROVIDER or 'not_configured'},
    }


def validate_production_configuration():
    issues = []
    if settings.DEBUG:
        issues.append('DEBUG must be false for production.')
    if not settings.ALLOWED_HOSTS or settings.ALLOWED_HOSTS == ['*']:
        issues.append('DJANGO_ALLOWED_HOSTS must be explicit for production.')
    if not settings.SESSION_COOKIE_SECURE or not settings.CSRF_COOKIE_SECURE:
        issues.append('Secure cookies must be enabled for production.')
    if not settings.SECURE_SSL_REDIRECT:
        issues.append('SECURE_SSL_REDIRECT must be enabled for production.')
    if settings.SECRET_KEY.startswith('test-secret-key'):
        issues.append('Production secret key is not configured.')
    if connection.vendor != 'postgresql':
        issues.append('Production database must be PostgreSQL.')
    if not settings.CSRF_TRUSTED_ORIGINS:
        issues.append('CSRF trusted origins must be configured.')
    if settings.NOVIRA_EXPECTED_RELEASE_ID and settings.NOVIRA_RELEASE_ID != settings.NOVIRA_EXPECTED_RELEASE_ID:
        issues.append('Active release identifier does not match expected release identifier.')
    if not settings.NOVIRA_BACKUP_DESTINATION_CLASS:
        issues.append('Backup destination class must be documented for production.')
    if not settings.NOVIRA_MONITORING_DESTINATION_CLASS:
        issues.append('Monitoring destination class must be documented for production.')
    return {
        'valid': not issues,
        'issues': issues,
        'environment': settings.NOVIRA_ENVIRONMENT_NAME,
        'release': release_identity_status(),
        'providers': {'email': settings.NOVIRA_EMAIL_PROVIDER or 'not_configured', 'outbox': settings.NOVIRA_OUTBOX_PROVIDER or 'not_configured'},
    }


def release_identity_status():
    active = settings.NOVIRA_RELEASE_ID or ''
    expected = settings.NOVIRA_EXPECTED_RELEASE_ID or active
    return {'active': active, 'expected': expected, 'matches': bool(active) and active == expected}


def generate_release_identity(*, release_name, roots=None):
    roots = roots or [Path(settings.BASE_DIR), Path(settings.BASE_DIR).parent / 'FRONT' / 'ktcproject']
    executor = MigrationExecutor(connection)
    leaves = [f'{app}.{name}' for app, name in sorted(executor.loader.graph.leaf_nodes())]
    selected = {}
    candidates = [
        Path(settings.BASE_DIR) / 'release_readiness' / 'models.py',
        Path(settings.BASE_DIR) / 'release_readiness' / 'services.py',
        Path(settings.BASE_DIR) / 'KTCProject' / 'settings.py',
        Path(settings.BASE_DIR).parent / 'FRONT' / 'ktcproject' / 'package.json',
    ]
    for path in candidates:
        if path.exists():
            selected[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    with connection.cursor() as cursor:
        if connection.vendor == 'postgresql':
            cursor.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")
        else:
            cursor.execute("SELECT count(*) FROM sqlite_master WHERE type='table'")
        table_count = cursor.fetchone()[0]
        cursor.execute("SELECT count(*) FROM pg_indexes WHERE schemaname='public'" if connection.vendor == 'postgresql' else "SELECT count(*) FROM sqlite_master WHERE type='index'")
        index_count = cursor.fetchone()[0]
    schema_signature = hashlib.sha256(json.dumps({'leaves': leaves, 'tables': table_count, 'indexes': index_count}, sort_keys=True).encode()).hexdigest()
    payload = {'release_name': release_name, 'timestamp': timezone.now().isoformat(), 'leaves': leaves, 'files': selected, 'schema': schema_signature}
    release_identifier = 'NVR-' + hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:24].upper()
    return {'release_identifier': release_identifier, 'migration_leaves': leaves, 'selected_file_checksums': selected, 'schema_signature': schema_signature, 'table_count': table_count, 'index_count': index_count}


def freeze_release_baseline(*, actor, release_name, evidence=None):
    evidence = evidence or {}
    identity = generate_release_identity(release_name=release_name)
    baseline = ReleaseBaseline.objects.create(
        release_name=release_name,
        release_identifier=identity['release_identifier'],
        migration_leaves=identity['migration_leaves'],
        selected_file_checksums=identity['selected_file_checksums'],
        schema_signature=identity['schema_signature'],
        postgres_version=evidence.get('postgres_version', ''),
        backend_manifest_result=evidence.get('backend_manifest_result', ''),
        frontend_manifest_result=evidence.get('frontend_manifest_result', ''),
        playwright_result=evidence.get('playwright_result', ''),
        backup_artifact_identity=evidence.get('backup_artifact_identity', ''),
        restore_rehearsal_identity=evidence.get('restore_rehearsal_identity', ''),
        known_warnings=evidence.get('known_warnings', []),
        residual_risks=evidence.get('residual_risks', []),
        pilot_constraints=evidence.get('pilot_constraints', []),
        status=ReleaseBaseline.STATUS_FROZEN,
        frozen_at=timezone.now(),
        created_by=actor if actor and actor.is_authenticated else None,
    )
    return baseline


REQUIRED_SIGNOFF_AREAS = [
    ('application_ownership', 'Application Owner'),
    ('database_infrastructure', 'DBA'),
    ('security', 'Security Lead'),
    ('backup_recovery', 'Operations Lead'),
    ('engineering', 'Engineering Lead'),
    ('pmo', 'PMO Lead'),
    ('production', 'Production Lead'),
    ('planning', 'Planning Lead'),
    ('inventory_warehouse', 'Warehouse Lead'),
    ('quality', 'Quality Lead'),
    ('maintenance', 'Maintenance Lead'),
    ('procurement', 'Procurement Lead'),
    ('costing_reporting', 'Cost Analyst'),
    ('incident_response', 'Incident Commander'),
    ('pilot_operations', 'Pilot Administrator'),
]


def create_default_signoff_checklist(*, actor, release_baseline=None, title='Production pilot sign-off'):
    number = f'PSC-{timezone.now():%Y%m%d%H%M%S}'
    checklist = PilotSignoffChecklist.objects.create(checklist_number=number, title=title, status=PilotSignoffChecklist.STATUS_ACTIVE, release_baseline=release_baseline, created_by=actor)
    for area, role in REQUIRED_SIGNOFF_AREAS:
        PilotSignoffRequirement.objects.create(
            checklist=checklist,
            area=area,
            responsible_role=role,
            requirement_text=f'{role} must review evidence and record a human decision for {area.replace("_", " ")}.',
            mandatory=True,
            blocking=True,
        )
    return checklist


def evaluate_signoff_checklist(checklist):
    requirements = checklist.requirements.all()
    total = requirements.filter(mandatory=True).count()
    approved = requirements.filter(mandatory=True, status=PilotSignoffRequirement.STATUS_APPROVED).count()
    conditional = requirements.filter(mandatory=True, status=PilotSignoffRequirement.STATUS_APPROVED_WITH_CONDITIONS).count()
    rejected = requirements.filter(mandatory=True, status=PilotSignoffRequirement.STATUS_REJECTED).count()
    pending = requirements.filter(mandatory=True, status=PilotSignoffRequirement.STATUS_PENDING).count()
    blocking = []
    today = timezone.localdate()
    for req in requirements.filter(mandatory=True, blocking=True):
        if req.status in {PilotSignoffRequirement.STATUS_PENDING, PilotSignoffRequirement.STATUS_REJECTED}:
            blocking.append({'area': req.area, 'status': req.status, 'role': req.responsible_role})
        if req.status == PilotSignoffRequirement.STATUS_APPROVED_WITH_CONDITIONS and req.conditions:
            blocking.append({'area': req.area, 'status': req.status, 'conditions': req.conditions})
        if req.due_date and req.due_date < today and req.status != PilotSignoffRequirement.STATUS_APPROVED:
            blocking.append({'area': req.area, 'status': 'EXPIRED', 'due_date': req.due_date.isoformat()})
    ready = not blocking and total == approved
    return {
        'checklist_revision': checklist.revision,
        'total_mandatory': total,
        'approved': approved,
        'approved_with_conditions': conditional,
        'rejected': rejected,
        'pending': pending,
        'blocking_conditions': blocking,
        'readiness_decision': 'READY' if ready else 'BLOCKED',
    }


@transaction.atomic
def record_signoff(*, requirement, actor, decision, comment='', evidence_reference='', conditions=None, supersedes=None):
    if not actor or not getattr(actor, 'is_authenticated', False):
        raise PermissionDenied('Authenticated human actor is required for sign-off.')
    if requirement.assigned_user_id and requirement.assigned_user_id != actor.pk and not actor.is_staff:
        raise PermissionDenied('Actor is not assigned to this sign-off requirement.')
    if decision == PilotSignoffRecord.DECISION_APPROVED_WITH_CONDITIONS and not conditions:
        raise ValidationError({'conditions': 'Approved-with-conditions requires explicit conditions.'})
    record = PilotSignoffRecord.objects.create(
        requirement=requirement,
        supersedes=supersedes,
        human_actor=actor,
        decision=decision,
        comment=comment,
        evidence_reference=evidence_reference,
        conditions=conditions or [],
        requirement_version=requirement.version,
        server_recorded_identity=f'user:{actor.pk}',
    )
    status_map = {
        PilotSignoffRecord.DECISION_APPROVED: PilotSignoffRequirement.STATUS_APPROVED,
        PilotSignoffRecord.DECISION_APPROVED_WITH_CONDITIONS: PilotSignoffRequirement.STATUS_APPROVED_WITH_CONDITIONS,
        PilotSignoffRecord.DECISION_REJECTED: PilotSignoffRequirement.STATUS_REJECTED,
        PilotSignoffRecord.DECISION_NOT_APPLICABLE: PilotSignoffRequirement.STATUS_NOT_APPLICABLE,
    }
    requirement.status = status_map[decision]
    requirement.conditions = conditions or []
    requirement.version += 1
    requirement.evidence_reference = evidence_reference or requirement.evidence_reference
    requirement.save(update_fields=['status', 'conditions', 'version', 'evidence_reference'])
    create_notification(recipient=actor, category='SIGNOFF', severity='INFO', title='Sign-off recorded', object_type='PilotSignoffRequirement', object_id=requirement.pk, dedupe_key=f'signoff-recorded-{requirement.pk}-{requirement.version}')
    return record


def pilot_gate_summary(pilot):
    checklist_eval = evaluate_signoff_checklist(pilot.checklist) if pilot.checklist_id else {'readiness_decision': 'BLOCKED', 'blocking_conditions': [{'area': 'signoff', 'status': 'MISSING'}]}
    critical_high = OperationalDefect.objects.filter(pilot=pilot, status=OperationalDefect.STATUS_OPEN, severity__in=[OperationalDefect.SEV_CRITICAL, OperationalDefect.SEV_HIGH]).count()
    users = PilotUserProvisioning.objects.filter(pilot=pilot, account_state=PilotUserProvisioning.ACCOUNT_ACTIVE).count()
    blockers = []
    if not pilot.technical_gate_passed:
        blockers.append('technical_release_gate_not_passed')
    if not validate_production_configuration()['valid']:
        blockers.append('production_config_invalid')
    if not pilot.backup_gate_passed:
        blockers.append('backup_gate_not_passed')
    if checklist_eval['readiness_decision'] != 'READY':
        blockers.append('signoff_incomplete')
    if not hasattr(pilot, 'scope'):
        blockers.append('pilot_scope_missing')
    if users == 0:
        blockers.append('pilot_users_missing')
    if not pilot.master_data_gate_passed:
        blockers.append('master_data_gate_not_passed')
    if not pilot.uat_gate_passed:
        blockers.append('uat_gate_not_passed')
    if critical_high:
        blockers.append('critical_or_high_defects_open')
    if not pilot.incident_owner_role:
        blockers.append('incident_owner_missing')
    if not pilot.rollback_plan:
        blockers.append('rollback_plan_missing')
    return {'ready': not blockers, 'blockers': blockers, 'signoff': checklist_eval, 'active_users': users, 'critical_high_defects': critical_high}


@transaction.atomic
def transition_pilot(*, pilot, actor, action, expected_version=None):
    if not actor or not actor.is_authenticated:
        raise PermissionDenied('Authenticated actor required.')
    locked = ProductionPilot.objects.select_for_update(of=('self',)).get(pk=pilot.pk)
    if expected_version is not None and int(expected_version) != locked.activation_version:
        raise ValidationError({'expected_version': 'Pilot activation version conflict.', 'current_version': locked.activation_version})
    transitions = {
        'start_preparation': (ProductionPilot.STATUS_DRAFT, ProductionPilot.STATUS_PREPARING),
        'request_signoff': (ProductionPilot.STATUS_PREPARING, ProductionPilot.STATUS_SIGNOFF_PENDING),
        'mark_ready': (ProductionPilot.STATUS_SIGNOFF_PENDING, ProductionPilot.STATUS_READY),
        'pause': (ProductionPilot.STATUS_ACTIVE, ProductionPilot.STATUS_PAUSED),
        'resume': (ProductionPilot.STATUS_PAUSED, ProductionPilot.STATUS_ACTIVE),
        'begin_stabilization': (ProductionPilot.STATUS_ACTIVE, ProductionPilot.STATUS_STABILIZING),
        'complete': (ProductionPilot.STATUS_STABILIZING, ProductionPilot.STATUS_COMPLETED),
        'fail': (locked.status, ProductionPilot.STATUS_FAILED),
        'cancel': (locked.status, ProductionPilot.STATUS_CANCELLED),
    }
    if action == 'activate':
        gate = pilot_gate_summary(locked)
        if locked.status != ProductionPilot.STATUS_READY or not gate['ready']:
            raise ValidationError({'pilot_activation': f'Pilot activation is blocked: {json.dumps(gate, sort_keys=True, default=str)}'})
        locked.status = ProductionPilot.STATUS_ACTIVE
        locked.activated_at = timezone.now()
        locked.activated_by = actor
        locked.activation_version += 1
        locked.save(update_fields=['status', 'activated_at', 'activated_by', 'activation_version', 'updated_at'])
        create_outbox_event(event_type='pilot.activated', aggregate_type='ProductionPilot', aggregate_id=locked.pk, payload={'release_identifier': locked.release_baseline.release_identifier, 'checklist_revision': locked.checklist.revision if locked.checklist_id else None})
        return locked
    current, target = transitions.get(action, (None, None))
    if current is None or (current != locked.status and action not in {'fail', 'cancel'}):
        raise ValidationError({'action': 'Pilot lifecycle action is not allowed.'})
    locked.status = target
    locked.activation_version += 1
    locked.save(update_fields=['status', 'activation_version', 'updated_at'])
    return locked


def access_review(pilot):
    rows = []
    for row in PilotUserProvisioning.objects.filter(pilot=pilot).select_related('user'):
        rows.append({
            'user': str(row.user_id),
            'active': row.account_state == PilotUserProvisioning.ACCOUNT_ACTIVE,
            'role': row.organizational_role,
            'scopes': {'plants': row.plant_scope, 'projects': row.project_scope, 'warehouses': row.warehouse_scope, 'suppliers': row.supplier_scope},
            'elevated_permissions': row.elevated_access or row.user.is_superuser,
            'conflicting_roles': row.conflicting_roles,
            'training_status': row.training_state,
            'flags': [
                flag for flag, active in {
                    'superuser': row.user.is_superuser,
                    'inactive_user_active_permissions': not row.user.is_active and row.account_state == PilotUserProvisioning.ACCOUNT_ACTIVE,
                    'training_incomplete': row.training_state != PilotUserProvisioning.TRAINING_COMPLETE,
                }.items() if active
            ],
        })
    return rows


def final_technical_status(pilot):
    gate = pilot_gate_summary(pilot)
    clean_reconciliation = not ReconciliationSnapshot.objects.filter(pilot=pilot, result=ReconciliationSnapshot.RESULT_MISMATCH).exists()
    cutover_passed = CutoverRehearsal.objects.filter(pilot=pilot, result=CutoverRehearsal.RESULT_PASS, activation_simulation=True, rollback_simulation=True).exists()
    backup_ok = BackupEvidence.objects.filter(restore_verified=True, status=BackupEvidence.STATUS_SUCCESS).exists()
    technical_blockers = list(gate['blockers'])
    if not clean_reconciliation:
        technical_blockers.append('reconciliation_mismatch')
    if not cutover_passed:
        technical_blockers.append('cutover_rehearsal_missing')
    if not backup_ok:
        technical_blockers.append('backup_restore_evidence_missing')
    technical_blockers = [b for b in technical_blockers if b != 'signoff_incomplete']
    if technical_blockers:
        return {'status': 'NOT_READY', 'blockers': technical_blockers}
    if gate['signoff']['readiness_decision'] != 'READY':
        return {'status': 'TECHNICALLY_READY_HUMAN_SIGNOFF_PENDING', 'blockers': []}
    return {'status': 'TECHNICALLY_READY', 'blockers': []}
