from __future__ import annotations

from decimal import Decimal
from threading import Barrier, Thread

import pytest
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection
from django.urls import reverse
from rest_framework import status

from opc.execution_services import command_execution, ensure_executions_for_order
from opc.inventory_services import (
    adjust_inventory,
    issue_material,
    material_reconciliation,
    quality_release_stock,
    quarantine_stock,
    receive_inventory,
    release_reservation,
    reserve_material,
    return_material,
    scrap_stock,
    transfer_inventory,
)
from opc.models import (
    InventoryBalance,
    InventoryReservation,
    InventoryTransaction,
    NonconformanceRecord,
    OperationExecution,
    ProductionLot,
    ProductionMaterialRequirement,
    ProductionSerial,
    QualityDisposition,
    StorageLocation,
    Warehouse,
)
from opc.production_services import generate_order_baseline, release_production_order
from opc.quality_services import create_ncr, propose_disposition, release_quality_hold
from tests.test_opc_graph_persistence_phase3 import api
from tests.test_opc_phase7_production_orders import create_order, released_source


pytestmark = pytest.mark.django_db


def material_control_order(code='P-P10'):
    admin, diagram, _nodes, _edges, refs = released_source(code)
    order = generate_order_baseline(create_order(admin, diagram, refs), actor=admin, expected_version=0)
    order = release_production_order(order, actor=admin, expected_version=1)
    ensure_executions_for_order(order, actor=admin)
    return admin, order, refs


def make_location(code='P10'):
    warehouse = Warehouse.objects.create(code=f'WH-{code}', name=f'Warehouse {code}', warehouse_type=Warehouse.TYPE_GENERAL)
    location = StorageLocation.objects.create(warehouse=warehouse, code='BIN-01', name='Bin 01', location_type=StorageLocation.TYPE_BIN)
    return warehouse, location


def ready_cut_execution(order, admin):
    material_execution, cut_execution, _output = order.operation_executions.select_related('manufacturing_operation').order_by('manufacturing_operation__sequence')
    material_execution, _event = command_execution(material_execution, actor=admin, expected_version=0, command='dispatch')
    material_execution, _event = command_execution(material_execution, actor=admin, expected_version=material_execution.execution_version, command='start')
    material_execution, _event = command_execution(material_execution, actor=admin, expected_version=material_execution.execution_version, command='record_output', produced_quantity='5.000000', accepted_quantity='5.000000')
    material_execution, _event = command_execution(material_execution, actor=admin, expected_version=material_execution.execution_version, command='complete')
    cut_execution.refresh_from_db()
    cut_execution, _event = command_execution(cut_execution, actor=admin, expected_version=cut_execution.execution_version, command='dispatch')
    cut_execution, _event = command_execution(cut_execution, actor=admin, expected_version=cut_execution.execution_version, command='start')
    return cut_execution


def test_warehouse_location_identity_and_serial_policy():
    admin, order, refs = material_control_order('P-P10-ID')
    warehouse, location = make_location('ID')
    child = StorageLocation.objects.create(warehouse=warehouse, parent=location, code='BIN-02', name='Bin 02')
    location.parent = child
    with pytest.raises(ValidationError):
        location.save()

    lot = ProductionLot.objects.create(lot_number='LOT-ID', item_revision=refs['component'], production_order=order, created_by=admin)
    serial = ProductionSerial.objects.create(serial_number='SN-ID', item_revision=refs['component'], production_order=order, lot=lot, created_by=admin)
    tx, balance = receive_inventory(actor=admin, item_revision=refs['component'], quantity='1.000000', warehouse=warehouse, location=location, lot=lot, serial=serial, idempotency_key='serial-receipt')
    assert tx.transaction_number.startswith('INV-')
    assert balance.on_hand_quantity == Decimal('1.000000')
    with pytest.raises(Exception):
        receive_inventory(actor=admin, item_revision=refs['component'], quantity='2.000000', warehouse=warehouse, location=location, lot=lot, serial=serial, idempotency_key='bad-serial-qty')


def test_receipt_transfer_adjustment_projection_and_idempotency():
    admin, _order, refs = material_control_order('P-P10-LEDGER')
    warehouse, location = make_location('LEDGER')
    dest_warehouse = Warehouse.objects.create(code='WH-LEDGER-2', name='Ledger 2')
    dest_location = StorageLocation.objects.create(warehouse=dest_warehouse, code='BIN-02', name='Bin 02')

    first_tx, balance = receive_inventory(actor=admin, item_revision=refs['component'], quantity='10.000000', warehouse=warehouse, location=location, idempotency_key='receipt-10')
    retry_tx, retry_balance = receive_inventory(actor=admin, item_revision=refs['component'], quantity='10.000000', warehouse=warehouse, location=location, idempotency_key='receipt-10')
    balance.refresh_from_db()
    assert retry_tx.pk == first_tx.pk
    assert balance.on_hand_quantity == Decimal('10.000000')

    transfer_tx = transfer_inventory(actor=admin, source_balance=balance, quantity='3.000000', destination_warehouse=dest_warehouse, destination_location=dest_location, expected_version=balance.balance_version, idempotency_key='transfer-3')
    balance.refresh_from_db()
    moved = InventoryBalance.objects.get(warehouse=dest_warehouse, location=dest_location)
    assert transfer_tx.transaction_type == InventoryTransaction.TYPE_TRANSFER
    assert balance.on_hand_quantity == Decimal('7.000000')
    assert moved.on_hand_quantity == Decimal('3.000000')

    with pytest.raises(Exception):
        adjust_inventory(actor=admin, balance=balance, quantity='99.000000', adjustment_type=InventoryTransaction.TYPE_ADJUSTMENT_OUT, expected_version=balance.balance_version, idempotency_key='bad-adjust', reason_code='COUNT')
    adjust_inventory(actor=admin, balance=balance, quantity='1.000000', adjustment_type=InventoryTransaction.TYPE_ADJUSTMENT_IN, expected_version=balance.balance_version, idempotency_key='adjust-in', reason_code='COUNT')
    with pytest.raises(Exception):
        first_tx.notes = 'mutate ledger'
        first_tx.save()


def test_reservation_issue_return_and_reconciliation():
    admin, order, refs = material_control_order('P-P10-MAT')
    warehouse, location = make_location('MAT')
    _tx, balance = receive_inventory(actor=admin, item_revision=refs['component'], quantity='8.000000', warehouse=warehouse, location=location, idempotency_key='mat-receipt')
    requirement = order.material_requirements.get()

    reservation, reserve_tx = reserve_material(actor=admin, requirement=requirement, balance=balance, quantity='5.000000', expected_balance_version=balance.balance_version, idempotency_key='reserve-5')
    balance.refresh_from_db()
    assert reserve_tx.transaction_type == InventoryTransaction.TYPE_RESERVATION
    assert reservation.status == InventoryReservation.STATUS_ACTIVE
    assert balance.reserved_quantity == Decimal('5.000000')

    released_reservation, release_tx = release_reservation(actor=admin, reservation=reservation, quantity='1.000000', expected_reservation_version=reservation.reservation_version, idempotency_key='release-1')
    released_reservation.refresh_from_db()
    assert release_tx.transaction_type == InventoryTransaction.TYPE_RESERVATION_RELEASE
    assert released_reservation.open_quantity == Decimal('4.000000')

    cut_execution = ready_cut_execution(order, admin)
    issue_tx = issue_material(actor=admin, operation_execution=cut_execution, requirement=requirement, reservation=released_reservation, quantity='2.000000', expected_reservation_version=released_reservation.reservation_version, idempotency_key='issue-2')
    returned_tx = return_material(actor=admin, issue_transaction=issue_tx, quantity='1.000000', destination_warehouse=warehouse, destination_location=location, idempotency_key='return-1')
    assert returned_tx.transaction_type == InventoryTransaction.TYPE_PRODUCTION_RETURN
    summary = material_reconciliation(requirement)
    assert summary['issued'] == '2.000000'
    assert summary['returned'] == '1.000000'
    assert summary['net_issued'] == '1.000000'


def test_quarantine_quality_release_scrap_and_blocked_issue():
    admin, order, refs = material_control_order('P-P10-QUAL')
    warehouse, location = make_location('QUAL')
    _tx, balance = receive_inventory(actor=admin, item_revision=refs['component'], quantity='4.000000', warehouse=warehouse, location=location, idempotency_key='qual-receipt')
    ncr = create_ncr(actor=admin, production_order=order, defect_code='MAT', defect_description='Material defect', affected_quantity='1.000000')

    quarantine_tx = quarantine_stock(actor=admin, balance=balance, quantity='2.000000', nonconformance=ncr, idempotency_key='quarantine-2')
    quarantined = InventoryBalance.objects.get(stock_status=InventoryBalance.STATUS_QUARANTINED)
    assert quarantine_tx.transaction_type == InventoryTransaction.TYPE_QUARANTINE
    with pytest.raises(Exception):
        reserve_material(actor=admin, requirement=order.material_requirements.get(), balance=quarantined, quantity='1.000000', idempotency_key='bad-reserve-quarantine')

    release_tx = quality_release_stock(actor=admin, balance=quarantined, quantity='1.000000', idempotency_key='quality-release-1')
    assert release_tx.transaction_type == InventoryTransaction.TYPE_QUALITY_RELEASE
    ncr, disposition = propose_disposition(ncr, actor=admin, expected_version=0, disposition_type=QualityDisposition.TYPE_SCRAP, quantity='1.000000', reason='Scrap defective stock')
    disposition.status = QualityDisposition.STATUS_APPROVED
    disposition.save(update_fields=['status'])
    scrap_tx = scrap_stock(actor=admin, balance=quarantined, quantity='1.000000', nonconformance=ncr, disposition=disposition, idempotency_key='scrap-1')
    assert scrap_tx.transaction_type == InventoryTransaction.TYPE_SCRAP
    assert InventoryBalance.objects.get(stock_status=InventoryBalance.STATUS_SCRAP).on_hand_quantity == Decimal('1.000000')


def test_inventory_api_conflict_and_material_queue():
    admin, order, refs = material_control_order('P-P10-API')
    client = api(admin)
    warehouse = client.post(reverse('opc-warehouse-list'), {'code': 'WH-API', 'name': 'API Warehouse'}, format='json')
    assert warehouse.status_code == status.HTTP_201_CREATED, warehouse.data
    location = client.post(reverse('opc-storage-location-list'), {'warehouse': warehouse.data['id'], 'code': 'BIN-01', 'name': 'Bin 01'}, format='json')
    assert location.status_code == status.HTTP_201_CREATED, location.data
    receipt = client.post(reverse('opc-inventory-balance-receive'), {'item_revision': str(refs['component'].pk), 'quantity': '5.000000', 'unit': 'EA', 'warehouse': warehouse.data['id'], 'location': location.data['id'], 'idempotency_key': 'api-receipt'}, format='json')
    assert receipt.status_code == status.HTTP_201_CREATED, receipt.data
    stale = client.post(reverse('opc-inventory-balance-transfer', kwargs={'pk': receipt.data['balance']['id']}), {'quantity': '1.000000', 'destination_warehouse': warehouse.data['id'], 'destination_location': location.data['id'], 'destination_status': InventoryBalance.STATUS_QUARANTINED, 'expected_version': 999, 'idempotency_key': 'api-stale-transfer'}, format='json')
    assert stale.status_code == status.HTTP_409_CONFLICT
    queue = client.get(reverse('opc-material-control-list'), {'production_order': str(order.pk)})
    assert queue.status_code == status.HTTP_200_OK, queue.data
    assert queue.data[0]['component_code'] == refs['component'].item.item_code


@pytest.mark.django_db(transaction=True)
def test_postgresql_concurrent_reservations_cannot_create_negative_available_stock():
    if connection.vendor != 'postgresql':
        pytest.skip('PostgreSQL row-lock concurrency test.')
    admin, order, refs = material_control_order('P-P10-CONC')
    warehouse, location = make_location('CONC')
    _tx, balance = receive_inventory(actor=admin, item_revision=refs['component'], quantity='5.000000', warehouse=warehouse, location=location, idempotency_key='conc-receipt')
    requirement = order.material_requirements.get()
    barrier = Barrier(2)
    results: list[str] = []

    def reserve_worker(key: str):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            reserve_material(
                actor=admin.__class__.objects.get(pk=admin.pk),
                requirement=ProductionMaterialRequirement.objects.get(pk=requirement.pk),
                balance=InventoryBalance.objects.get(pk=balance.pk),
                quantity='4.000000',
                idempotency_key=key,
            )
            results.append('ok')
        except Exception:
            results.append('blocked')
        finally:
            close_old_connections()

    threads = [Thread(target=reserve_worker, args=(f'conc-{idx}',)) for idx in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    balance.refresh_from_db()
    assert sorted(results) == ['blocked', 'ok']
    assert balance.on_hand_quantity == Decimal('5.000000')
    assert balance.reserved_quantity == Decimal('4.000000')
    assert balance.available_quantity == Decimal('1.000000')
