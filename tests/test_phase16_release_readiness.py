from __future__ import annotations

from threading import Barrier, Thread

import pytest
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from auditlog.models import AuditEvent
from auditlog.services import log_event
from opc.models import InventoryBalance, PurchaseOrder, RequestForQuotation, RFQLine, RFQSupplierInvitation, Supplier, SupplierQuotation, SupplierQuotationLine
from opc.procurement_services import receive_purchase_order_line, transition_purchase_order, transition_rfq, transition_supplier
from release_readiness.models import ImportJob, IntegrationOutboxEvent, Notification
from release_readiness.services import create_notification, create_outbox_event, health_payload, resolve_identity, sign_identity, validate_production_configuration
from tests.factories import make_company_admin
from tests.test_opc_graph_persistence_phase3 import api
from tests.test_opc_phase10_inventory import make_location
from tests.test_phase15_procurement_cost_performance import sourcing_fixture


pytestmark = pytest.mark.django_db


def staff_user():
    user = make_company_admin()
    user.is_staff = True
    user.is_superuser = True
    user.save(update_fields=['is_staff', 'is_superuser'])
    return user


def test_health_readiness_request_ids_and_production_config_validator(settings):
    admin = staff_user()
    client = api(admin)
    response = client.get(reverse('release-health'), HTTP_X_REQUEST_ID='req-phase16', HTTP_X_CORRELATION_ID='corr-phase16')
    assert response.status_code == status.HTTP_200_OK
    assert response['X-Request-ID'] == 'req-phase16'
    assert response['X-Correlation-ID'] == 'corr-phase16'
    assert response.data['database']['ok'] is True
    assert 'api_version' in response.data['build']

    settings.DEBUG = True
    settings.ALLOWED_HOSTS = ['*']
    config = validate_production_configuration()
    assert config['valid'] is False
    assert any('DEBUG' in issue for issue in config['issues'])


def test_audit_events_capture_identifiers_and_are_immutable():
    admin = staff_user()
    client = api(admin)
    response = client.get(reverse('release-dashboard-list'), HTTP_X_REQUEST_ID='req-audit', HTTP_X_CORRELATION_ID='corr-audit', HTTP_X_COMMAND_ID='cmd-audit')
    assert response.status_code == status.HTTP_200_OK
    log_event('phase16_manual_audit', request=response.wsgi_request if hasattr(response, 'wsgi_request') else None, category='business')
    event = AuditEvent.objects.create(action='phase16_direct_audit', category='business', request_id='req-audit', correlation_id='corr-audit', command_id='cmd-audit')
    with pytest.raises(ValidationError):
        event.action = 'mutated'
        event.save()
    with pytest.raises(ValidationError):
        event.delete()


def test_notifications_deduplicate_read_ack_and_report_email_state(settings):
    admin = staff_user()
    settings.NOVIRA_EMAIL_PROVIDER = ''
    first = create_notification(recipient=admin, category='PROCUREMENT', severity=Notification.SEVERITY_CRITICAL, title='PO delayed', object_type='PurchaseOrder', object_id='po-a', dedupe_key='po-delay')
    second = create_notification(recipient=admin, category='PROCUREMENT', severity=Notification.SEVERITY_CRITICAL, title='PO delayed', object_type='PurchaseOrder', object_id='po-a', dedupe_key='po-delay')
    assert first.pk == second.pk
    assert first.email_provider_state == 'not_configured'

    client = api(admin)
    listed = client.get(reverse('release-notification-list'))
    assert listed.status_code == status.HTTP_200_OK
    read = client.post(reverse('release-notification-read', kwargs={'pk': first.pk}))
    ack = client.post(reverse('release-notification-acknowledge', kwargs={'pk': first.pk}))
    assert read.status_code == status.HTTP_200_OK
    assert ack.status_code == status.HTTP_200_OK
    first.refresh_from_db()
    assert first.read_at is not None and first.acknowledged_at is not None


def test_barcode_identity_resolves_supported_objects_and_detects_tampering():
    admin = staff_user()
    supplier = Supplier.objects.create(supplier_code='SUP-P16-BC', name='Barcode Supplier', created_by=admin)
    transition_supplier(supplier, actor=admin, target_status=Supplier.STATUS_QUALIFIED, expected_version=0)
    token = sign_identity('PurchaseOrder', '00000000-0000-0000-0000-000000000000')
    tampered = token[:-1] + ('0' if token[-1] != '0' else '1')
    with pytest.raises(Exception):
        resolve_identity(tampered, actor=admin)

    _admin, _refs, _warehouse, _location, _supplier, _rfq, _quote, decision = sourcing_fixture('BC')
    from opc.procurement_services import approve_sourcing_decision, convert_sourcing_to_purchase_order
    approved = approve_sourcing_decision(decision, actor=admin, expected_version=0)
    po = convert_sourcing_to_purchase_order(approved, actor=admin, expected_version=1)
    valid = resolve_identity(sign_identity('PurchaseOrder', po.pk), actor=admin)
    assert valid['object_type'] == 'PurchaseOrder'
    assert valid['object_id'] == str(po.pk)


def test_import_dry_run_row_errors_export_formula_safety_and_outbox_api():
    admin = staff_user()
    Supplier.objects.create(supplier_code='=SUP-P16-EXP', name='+Formula Supplier', created_by=admin)
    client = api(admin)
    payload = 'supplier_code,name\nSUP-P16-IMP,Imported Supplier\n,Missing Code\n'
    imported = client.post(reverse('release-import-csv'), {'schema_name': 'supplier-basic', 'schema_version': 'v1', 'content': payload, 'idempotency_key': 'phase16-import', 'dry_run': True}, format='multipart')
    assert imported.status_code == status.HTTP_201_CREATED, imported.data
    assert imported.data['row_count'] == 2
    assert imported.data['error_count'] == 1
    assert ImportJob.objects.get(id=imported.data['id']).dry_run is True

    exported = client.get(reverse('release-export-suppliers-csv'), {'limit': 10})
    assert exported.status_code == status.HTTP_200_OK
    content = exported.content.decode('utf-8')
    assert "'=SUP-P16-EXP" in content
    assert "'+Formula Supplier" in content

    outbox = client.post(reverse('release-outbox-create-test'), {'payload': {'ok': True}}, format='json', HTTP_X_CORRELATION_ID='corr-outbox', HTTP_X_COMMAND_ID='cmd-outbox')
    assert outbox.status_code == status.HTTP_201_CREATED, outbox.data
    event = IntegrationOutboxEvent.objects.get(id=outbox.data['id'])
    assert event.correlation_id == 'corr-outbox'
    with pytest.raises(ValidationError):
        event.payload = {'mutated': True}
        event.save()


@pytest.mark.django_db(transaction=True)
def test_postgresql_cross_domain_po_receipt_is_single_writer_against_inventory():
    if connection.vendor != 'postgresql':
        pytest.skip('PostgreSQL row-lock concurrency test.')
    admin, _refs, warehouse, location, _supplier, _rfq, _quote, decision = sourcing_fixture('P16CONC')
    from opc.procurement_services import approve_sourcing_decision, convert_sourcing_to_purchase_order
    approved = approve_sourcing_decision(decision, actor=admin, expected_version=0)
    po = convert_sourcing_to_purchase_order(approved, actor=admin, expected_version=1)
    po = transition_purchase_order(po, actor=admin, target_status=PurchaseOrder.STATUS_APPROVED, expected_version=0)
    po = transition_purchase_order(po, actor=admin, target_status=PurchaseOrder.STATUS_RELEASED, expected_version=1)
    line = po.lines.get()
    barrier = Barrier(2)
    results: list[str] = []

    def worker(key):
        close_old_connections()
        barrier.wait()
        try:
            receive_purchase_order_line(actor=admin, purchase_order_line=line, quantity='10.000000', warehouse=warehouse, location=location, accepted_quantity='10.000000', idempotency_key=key)
            results.append('ok')
        except Exception:
            results.append('conflict')
        finally:
            close_old_connections()

    threads = [Thread(target=worker, args=(f'p16-receipt-{idx}',)) for idx in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results.count('ok') == 1
    line.refresh_from_db()
    assert line.received_quantity == line.quantity
    assert InventoryBalance.objects.filter(item_revision=line.item_revision, warehouse=warehouse, location=location).exists()
