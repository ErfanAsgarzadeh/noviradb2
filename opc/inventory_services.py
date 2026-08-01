from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID

from django.db import transaction
from django.db.models import Max, Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from enterprise_items.exceptions import EngineeringLifecycleError, EngineeringPermissionError
from enterprise_items.permissions import can_manage_engineering

from .models import (
    InventoryBalance,
    InventoryReservation,
    InventoryTransaction,
    NonconformanceRecord,
    OperationExecution,
    ProductionLot,
    ProductionMaterialConsumption,
    ProductionMaterialRequirement,
    ProductionOrder,
    ProductionSerial,
    QualityDisposition,
    QualityHold,
    StorageLocation,
    Warehouse,
)

Q6 = Decimal('0.000001')


class InventoryConflict(EngineeringLifecycleError):
    def __init__(self, code: str, detail: str, **payload):
        super().__init__({'code': code, 'detail': detail, **payload})


def decimal_value(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(Q6, rounding=ROUND_HALF_UP)


def _require_authenticated(actor):
    if not getattr(actor, 'is_authenticated', False):
        raise EngineeringPermissionError('Authentication is required for inventory commands.')


def _require_manager(actor, action='perform this inventory command'):
    if not can_manage_engineering(actor):
        raise EngineeringPermissionError(f'You do not have permission to {action}.')


def _signature_value(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if hasattr(value, 'pk'):
        return str(value.pk)
    if isinstance(value, dict):
        return {key: _signature_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_signature_value(item) for item in value]
    return value


def _payload_signature(command: str, payload: dict) -> dict:
    return {'command': command, **{key: _signature_value(value) for key, value in payload.items()}}


def _idempotent_transaction(scope: str, key: str, signature: dict):
    if not key:
        return None
    existing = InventoryTransaction.objects.filter(command_scope=scope, idempotency_key=key).first()
    if not existing:
        return None
    if existing.metadata.get('payload_signature') != signature:
        raise EngineeringLifecycleError({'idempotency_key': 'Idempotency key was reused with a different inventory payload.'})
    return existing


def _next_transaction_number() -> str:
    latest = InventoryTransaction.objects.select_for_update().aggregate(Max('transaction_number'))['transaction_number__max']
    next_number = int(str(latest).split('-')[-1]) + 1 if latest else 1
    return f'INV-{timezone.now():%Y%m%d}-{next_number:06d}'


def _validate_destination(warehouse: Warehouse, location: StorageLocation):
    if not warehouse.active:
        raise EngineeringLifecycleError({'warehouse': 'Inactive warehouse cannot receive inventory transactions.'})
    if location.warehouse_id != warehouse.pk:
        raise EngineeringLifecycleError({'location': 'Location belongs to a different warehouse.'})
    if not location.active or not location.inventory_enabled:
        raise EngineeringLifecycleError({'location': 'Location must be active and inventory-enabled.'})


def _validate_identity(item_revision, quantity: Decimal, unit: str, lot=None, serial=None):
    if quantity <= 0:
        raise EngineeringLifecycleError({'quantity': 'Quantity must be positive.'})
    if lot and lot.item_revision_id != item_revision.pk:
        raise EngineeringLifecycleError({'lot': 'Lot item revision must match stock item revision.'})
    if serial:
        if serial.item_revision_id != item_revision.pk:
            raise EngineeringLifecycleError({'serial': 'Serial item revision must match stock item revision.'})
        if quantity != Decimal('1.000000'):
            raise EngineeringLifecycleError({'quantity': 'Serial-controlled movement quantity must be exactly 1.'})
        if serial.lot_id and lot and serial.lot_id != lot.pk:
            raise EngineeringLifecycleError({'serial': 'Serial belongs to a different lot.'})
    if not unit:
        raise EngineeringLifecycleError({'unit': 'Unit of measure is required.'})


def _balance_queryset():
    return InventoryBalance.objects.select_for_update(of=('self',)).select_related('item_revision__item', 'warehouse', 'location', 'lot', 'serial')


def _find_balance(*, item_revision, warehouse, location, lot=None, serial=None, stock_status=InventoryBalance.STATUS_AVAILABLE, ownership='', unit='EA', lock=True):
    queryset = _balance_queryset() if lock else InventoryBalance.objects.select_related('item_revision__item', 'warehouse', 'location', 'lot', 'serial')
    filters = {
        'item_revision': item_revision,
        'warehouse': warehouse,
        'location': location,
        'stock_status': stock_status,
        'ownership': ownership or '',
    }
    queryset = queryset.filter(**filters)
    queryset = queryset.filter(lot=lot) if lot else queryset.filter(lot__isnull=True)
    queryset = queryset.filter(serial=serial) if serial else queryset.filter(serial__isnull=True)
    balance = queryset.first()
    if balance:
        return balance
    return InventoryBalance.objects.create(
        item_revision=item_revision,
        warehouse=warehouse,
        location=location,
        lot=lot,
        serial=serial,
        stock_status=stock_status,
        ownership=ownership or '',
        unit=unit,
    )


def _check_balance_version(balance: InventoryBalance, expected_version):
    if expected_version is None:
        return
    expected = int(expected_version)
    if balance.balance_version != expected:
        raise InventoryConflict('balance_version_conflict', 'Inventory balance version conflict.', expected_version=expected, current_version=balance.balance_version, balance_id=str(balance.pk))


def _check_reservation_version(reservation: InventoryReservation, expected_version):
    if expected_version is None:
        return
    expected = int(expected_version)
    if reservation.reservation_version != expected:
        raise InventoryConflict('reservation_version_conflict', 'Inventory reservation version conflict.', expected_version=expected, current_version=reservation.reservation_version, reservation_id=str(reservation.pk))


def _append_transaction(*, actor, transaction_type, item_revision, quantity, unit, command_scope, idempotency_key, payload_signature, source_balance=None, destination_balance=None, source_status='', destination_status='', production_order=None, operation_execution=None, material_requirement=None, material_consumption=None, quality_hold=None, nonconformance=None, disposition=None, reason_code='', notes='', metadata=None, reversal_of=None):
    tx = InventoryTransaction.objects.create(
        transaction_number=_next_transaction_number(),
        transaction_type=transaction_type,
        item_revision=item_revision,
        quantity=quantity,
        unit=unit,
        source_warehouse=source_balance.warehouse if source_balance else None,
        source_location=source_balance.location if source_balance else None,
        destination_warehouse=destination_balance.warehouse if destination_balance else None,
        destination_location=destination_balance.location if destination_balance else None,
        source_lot=source_balance.lot if source_balance else None,
        source_serial=source_balance.serial if source_balance else None,
        destination_lot=destination_balance.lot if destination_balance else None,
        destination_serial=destination_balance.serial if destination_balance else None,
        source_stock_status=source_status or (source_balance.stock_status if source_balance else ''),
        destination_stock_status=destination_status or (destination_balance.stock_status if destination_balance else ''),
        ownership=(source_balance.ownership if source_balance else destination_balance.ownership if destination_balance else ''),
        production_order=production_order,
        operation_execution=operation_execution,
        material_requirement=material_requirement,
        material_consumption=material_consumption,
        quality_hold=quality_hold,
        nonconformance=nonconformance,
        disposition=disposition,
        actor=actor,
        event_timestamp=timezone.now(),
        command_scope=command_scope,
        idempotency_key=idempotency_key or f'auto:{transaction_type}:{timezone.now().timestamp()}',
        reversal_of=reversal_of,
        reason_code=reason_code,
        notes=notes,
        metadata={'payload_signature': payload_signature, **(metadata or {})},
    )
    for balance in (source_balance, destination_balance):
        if balance:
            balance.last_transaction = tx
            balance.balance_version += 1
            balance.save(update_fields=['on_hand_quantity', 'reserved_quantity', 'balance_version', 'last_transaction', 'updated_at'])
    return tx


def _mutate_on_hand(balance: InventoryBalance, delta: Decimal):
    balance.on_hand_quantity = decimal_value(balance.on_hand_quantity + delta)
    if balance.on_hand_quantity < 0:
        raise EngineeringLifecycleError({'quantity': 'Inventory movement would create negative stock.'})
    if balance.reserved_quantity > balance.on_hand_quantity:
        raise EngineeringLifecycleError({'quantity': 'Inventory movement would leave reserved quantity above on hand.'})


def _mutate_reserved(balance: InventoryBalance, delta: Decimal):
    balance.reserved_quantity = decimal_value(balance.reserved_quantity + delta)
    if balance.reserved_quantity < 0:
        raise EngineeringLifecycleError({'quantity': 'Reservation movement would create negative reserved quantity.'})
    if balance.reserved_quantity > balance.on_hand_quantity:
        raise EngineeringLifecycleError({'quantity': 'Reservation exceeds on-hand quantity.'})


@transaction.atomic
def receive_inventory(*, actor, item_revision, quantity, unit='EA', warehouse, location, lot=None, serial=None, stock_status=InventoryBalance.STATUS_AVAILABLE, ownership='', expected_version=None, idempotency_key='', reason_code='MANUAL_RECEIPT', notes=''):
    _require_authenticated(actor)
    qty = decimal_value(quantity)
    _validate_destination(warehouse, location)
    _validate_identity(item_revision, qty, unit, lot=lot, serial=serial)
    signature = _payload_signature('receipt', locals())
    existing = _idempotent_transaction('receipt', idempotency_key, signature)
    if existing:
        return existing, existing.destination_balance if hasattr(existing, 'destination_balance') else None
    if serial and InventoryBalance.objects.filter(serial=serial, on_hand_quantity__gt=0).exclude(stock_status=InventoryBalance.STATUS_SCRAP).exists():
        raise EngineeringLifecycleError({'serial': 'Serial already exists in active inventory.'})
    balance = _find_balance(item_revision=item_revision, warehouse=warehouse, location=location, lot=lot, serial=serial, stock_status=stock_status, ownership=ownership, unit=unit)
    _check_balance_version(balance, expected_version)
    _mutate_on_hand(balance, qty)
    tx = _append_transaction(actor=actor, transaction_type=InventoryTransaction.TYPE_RECEIPT, item_revision=item_revision, quantity=qty, unit=unit, command_scope='receipt', idempotency_key=idempotency_key, payload_signature=signature, destination_balance=balance, reason_code=reason_code, notes=notes)
    return tx, balance


@transaction.atomic
def transfer_inventory(*, actor, source_balance: InventoryBalance, quantity, destination_warehouse, destination_location, destination_status=None, expected_version=None, idempotency_key='', reason_code='TRANSFER', notes=''):
    _require_authenticated(actor)
    qty = decimal_value(quantity)
    source = _balance_queryset().get(pk=source_balance.pk)
    status = destination_status or source.stock_status
    _validate_destination(destination_warehouse, destination_location)
    if source.warehouse_id == destination_warehouse.pk and source.location_id == destination_location.pk and source.stock_status == status:
        raise EngineeringLifecycleError({'destination': 'Transfer destination must differ from the source identity.'})
    signature = _payload_signature('transfer', {'source_balance': source, 'quantity': qty, 'destination_warehouse': destination_warehouse, 'destination_location': destination_location, 'destination_status': status})
    existing = _idempotent_transaction('transfer', idempotency_key, signature)
    if existing:
        return existing
    _check_balance_version(source, expected_version)
    destination = _find_balance(item_revision=source.item_revision, warehouse=destination_warehouse, location=destination_location, lot=source.lot, serial=source.serial, stock_status=status, ownership=source.ownership, unit=source.unit)
    if source.pk == destination.pk:
        raise EngineeringLifecycleError({'destination': 'Transfer destination must differ from the source identity.'})
    _mutate_on_hand(source, -qty)
    _mutate_on_hand(destination, qty)
    return _append_transaction(actor=actor, transaction_type=InventoryTransaction.TYPE_TRANSFER, item_revision=source.item_revision, quantity=qty, unit=source.unit, command_scope='transfer', idempotency_key=idempotency_key, payload_signature=signature, source_balance=source, destination_balance=destination, reason_code=reason_code, notes=notes)


def _require_released_order(requirement: ProductionMaterialRequirement):
    if requirement.production_order.status != ProductionOrder.STATUS_RELEASED:
        raise EngineeringLifecycleError({'production_order': 'Material control requires a released production order.'})


def _requirement_reserved_total(requirement):
    return requirement.inventory_reservations.exclude(status__in=[InventoryReservation.STATUS_CANCELLED]).aggregate(total=Sum('quantity'), released=Sum('released_quantity')) 


@transaction.atomic
def reserve_material(*, actor, requirement: ProductionMaterialRequirement, balance: InventoryBalance, quantity, expected_balance_version=None, idempotency_key='', notes=''):
    _require_authenticated(actor)
    requirement = ProductionMaterialRequirement.objects.select_for_update(of=('self',)).select_related('production_order', 'operation', 'component_item_revision').get(pk=requirement.pk)
    _require_released_order(requirement)
    balance = _balance_queryset().get(pk=balance.pk)
    qty = decimal_value(quantity)
    if qty <= 0:
        raise EngineeringLifecycleError({'quantity': 'Reservation quantity must be positive.'})
    _check_balance_version(balance, expected_balance_version)
    if balance.item_revision_id != requirement.component_item_revision_id or balance.unit != requirement.unit:
        raise EngineeringLifecycleError({'balance': 'Stock identity does not match the material requirement.'})
    if balance.stock_status != InventoryBalance.STATUS_AVAILABLE:
        raise EngineeringLifecycleError({'stock_status': 'Only available stock can be reserved.'})
    signature = _payload_signature('reservation', {'requirement': requirement, 'balance': balance, 'quantity': qty})
    existing = _idempotent_transaction('reservation', idempotency_key, signature)
    if existing:
        reservation = InventoryReservation.objects.filter(last_transaction=existing).first()
        return reservation, existing
    totals = requirement.inventory_reservations.exclude(status=InventoryReservation.STATUS_CANCELLED).aggregate(reserved=Sum('quantity'), released=Sum('released_quantity'))
    open_reserved = (totals['reserved'] or Decimal('0')) - (totals['released'] or Decimal('0'))
    if open_reserved + qty > requirement.total_planned_quantity:
        raise EngineeringLifecycleError({'quantity': 'Reservation cannot exceed remaining planned material requirement.'})
    if balance.available_quantity < qty:
        raise EngineeringLifecycleError({'quantity': 'Insufficient available stock for reservation.'})
    _mutate_reserved(balance, qty)
    tx = _append_transaction(actor=actor, transaction_type=InventoryTransaction.TYPE_RESERVATION, item_revision=balance.item_revision, quantity=qty, unit=balance.unit, command_scope='reservation', idempotency_key=idempotency_key, payload_signature=signature, destination_balance=balance, production_order=requirement.production_order, material_requirement=requirement, notes=notes)
    reservation = InventoryReservation.objects.create(production_order=requirement.production_order, material_requirement=requirement, item_revision=requirement.component_item_revision, balance=balance, warehouse=balance.warehouse, location=balance.location, lot=balance.lot, serial=balance.serial, quantity=qty, unit=requirement.unit, reserved_by=actor, reserved_at=tx.event_timestamp, last_transaction=tx)
    return reservation, tx


@transaction.atomic
def release_reservation(*, actor, reservation: InventoryReservation, quantity=None, expected_reservation_version=None, idempotency_key='', notes=''):
    _require_authenticated(actor)
    reservation = InventoryReservation.objects.select_for_update(of=('self',)).select_related('balance', 'material_requirement', 'production_order').get(pk=reservation.pk)
    _check_reservation_version(reservation, expected_reservation_version)
    if reservation.status in {InventoryReservation.STATUS_RELEASED, InventoryReservation.STATUS_CANCELLED, InventoryReservation.STATUS_FULLY_ISSUED}:
        raise EngineeringLifecycleError({'status': 'Reservation is not releasable.'})
    qty = decimal_value(quantity if quantity is not None else reservation.open_quantity)
    if qty <= 0 or qty > reservation.open_quantity:
        raise EngineeringLifecycleError({'quantity': 'Release quantity must be positive and cannot exceed open reservation quantity.'})
    signature = _payload_signature('reservation_release', {'reservation': reservation, 'quantity': qty})
    existing = _idempotent_transaction('reservation_release', idempotency_key, signature)
    if existing:
        return reservation, existing
    balance = _balance_queryset().get(pk=reservation.balance_id)
    _mutate_reserved(balance, -qty)
    reservation.released_quantity = decimal_value(reservation.released_quantity + qty)
    reservation.reservation_version += 1
    reservation.status = InventoryReservation.STATUS_RELEASED if reservation.open_quantity == 0 else reservation.status
    tx = _append_transaction(actor=actor, transaction_type=InventoryTransaction.TYPE_RESERVATION_RELEASE, item_revision=balance.item_revision, quantity=qty, unit=balance.unit, command_scope='reservation_release', idempotency_key=idempotency_key, payload_signature=signature, source_balance=balance, production_order=reservation.production_order, material_requirement=reservation.material_requirement, notes=notes)
    reservation.last_transaction = tx
    reservation.save(update_fields=['released_quantity', 'reservation_version', 'status', 'last_transaction', 'updated_at'])
    return reservation, tx


@transaction.atomic
def issue_material(*, actor, operation_execution: OperationExecution, requirement: ProductionMaterialRequirement, quantity, balance: InventoryBalance | None = None, reservation: InventoryReservation | None = None, expected_balance_version=None, expected_reservation_version=None, idempotency_key='', notes=''):
    _require_authenticated(actor)
    execution = OperationExecution.objects.select_for_update(of=('self',)).select_related('production_order', 'manufacturing_operation').get(pk=operation_execution.pk)
    requirement = ProductionMaterialRequirement.objects.select_for_update(of=('self',)).select_related('production_order', 'operation', 'component_item_revision').get(pk=requirement.pk)
    if requirement.production_order_id != execution.production_order_id or requirement.operation_id != execution.manufacturing_operation_id:
        raise EngineeringLifecycleError({'requirement': 'Material requirement belongs to a different operation.'})
    _require_released_order(requirement)
    qty = decimal_value(quantity)
    if reservation:
        reservation = InventoryReservation.objects.select_for_update(of=('self',)).select_related('balance').get(pk=reservation.pk)
        _check_reservation_version(reservation, expected_reservation_version)
        if reservation.material_requirement_id != requirement.pk:
            raise EngineeringLifecycleError({'reservation': 'Reservation belongs to a different material requirement.'})
        if reservation.open_quantity < qty:
            raise EngineeringLifecycleError({'quantity': 'Insufficient open reservation quantity.'})
        stock = _balance_queryset().get(pk=reservation.balance_id)
    else:
        stock = _balance_queryset().get(pk=balance.pk)
    signature = _payload_signature('production_issue', {'operation_execution': execution, 'requirement': requirement, 'balance': stock, 'reservation': reservation, 'quantity': qty})
    existing = _idempotent_transaction('production_issue', idempotency_key, signature)
    if existing:
        return existing
    _check_balance_version(stock, expected_balance_version)
    if stock.item_revision_id != requirement.component_item_revision_id:
        raise EngineeringLifecycleError({'balance': 'Stock identity does not match material requirement.'})
    if stock.stock_status != InventoryBalance.STATUS_AVAILABLE:
        raise EngineeringLifecycleError({'stock_status': 'Only available stock can be issued.'})
    if stock.on_hand_quantity < qty:
        raise EngineeringLifecycleError({'quantity': 'Insufficient on-hand stock for issue.'})
    if reservation:
        _mutate_reserved(stock, -qty)
        reservation.issued_quantity = decimal_value(reservation.issued_quantity + qty)
        reservation.reservation_version += 1
        reservation.status = InventoryReservation.STATUS_FULLY_ISSUED if reservation.open_quantity == 0 else InventoryReservation.STATUS_PARTIALLY_ISSUED
    elif stock.available_quantity < qty:
        raise EngineeringLifecycleError({'quantity': 'Direct issue requires sufficient unreserved available stock.'})
    _mutate_on_hand(stock, -qty)
    tx = _append_transaction(actor=actor, transaction_type=InventoryTransaction.TYPE_PRODUCTION_ISSUE, item_revision=stock.item_revision, quantity=qty, unit=stock.unit, command_scope='production_issue', idempotency_key=idempotency_key, payload_signature=signature, source_balance=stock, production_order=execution.production_order, operation_execution=execution, material_requirement=requirement, notes=notes)
    if reservation:
        reservation.last_transaction = tx
        reservation.save(update_fields=['issued_quantity', 'reservation_version', 'status', 'last_transaction', 'updated_at'])
    return tx


def issued_returnable_quantity(issue: InventoryTransaction) -> Decimal:
    returned = InventoryTransaction.objects.filter(reversal_of=issue, transaction_type=InventoryTransaction.TYPE_PRODUCTION_RETURN).aggregate(total=Sum('quantity'))['total'] or Decimal('0')
    return decimal_value(issue.quantity - returned)


@transaction.atomic
def return_material(*, actor, issue_transaction: InventoryTransaction, quantity, destination_warehouse, destination_location, stock_status=InventoryBalance.STATUS_AVAILABLE, idempotency_key='', notes=''):
    _require_authenticated(actor)
    issue = InventoryTransaction.objects.select_for_update().get(pk=issue_transaction.pk)
    if issue.transaction_type != InventoryTransaction.TYPE_PRODUCTION_ISSUE:
        raise EngineeringLifecycleError({'issue_transaction': 'Material return must reference a production issue transaction.'})
    qty = decimal_value(quantity)
    _validate_destination(destination_warehouse, destination_location)
    signature = _payload_signature('production_return', {'issue': issue, 'quantity': qty, 'destination_warehouse': destination_warehouse, 'destination_location': destination_location, 'stock_status': stock_status})
    existing = _idempotent_transaction('production_return', idempotency_key, signature)
    if existing:
        return existing
    if qty <= 0 or qty > issued_returnable_quantity(issue):
        raise EngineeringLifecycleError({'quantity': 'Return quantity must be positive and cannot exceed returnable issued quantity.'})
    destination = _find_balance(item_revision=issue.item_revision, warehouse=destination_warehouse, location=destination_location, lot=issue.source_lot, serial=issue.source_serial, stock_status=stock_status, ownership=issue.ownership, unit=issue.unit)
    _mutate_on_hand(destination, qty)
    return _append_transaction(actor=actor, transaction_type=InventoryTransaction.TYPE_PRODUCTION_RETURN, item_revision=issue.item_revision, quantity=qty, unit=issue.unit, command_scope='production_return', idempotency_key=idempotency_key, payload_signature=signature, destination_balance=destination, production_order=issue.production_order, operation_execution=issue.operation_execution, material_requirement=issue.material_requirement, notes=notes, reversal_of=issue)


@transaction.atomic
def adjust_inventory(*, actor, balance: InventoryBalance, quantity, adjustment_type, expected_version=None, idempotency_key='', reason_code='', notes=''):
    _require_manager(actor, 'adjust inventory')
    if not reason_code:
        raise EngineeringLifecycleError({'reason_code': 'Adjustment reason code is required.'})
    stock = _balance_queryset().get(pk=balance.pk)
    _check_balance_version(stock, expected_version)
    qty = decimal_value(quantity)
    if adjustment_type not in {InventoryTransaction.TYPE_ADJUSTMENT_IN, InventoryTransaction.TYPE_ADJUSTMENT_OUT}:
        raise EngineeringLifecycleError({'adjustment_type': 'Unsupported adjustment type.'})
    signature = _payload_signature('adjustment', {'balance': stock, 'quantity': qty, 'adjustment_type': adjustment_type, 'reason_code': reason_code})
    existing = _idempotent_transaction('adjustment', idempotency_key, signature)
    if existing:
        return existing
    if adjustment_type == InventoryTransaction.TYPE_ADJUSTMENT_OUT:
        if stock.available_quantity < qty:
            raise EngineeringLifecycleError({'quantity': 'Insufficient available stock for adjustment out.'})
        _mutate_on_hand(stock, -qty)
        return _append_transaction(actor=actor, transaction_type=adjustment_type, item_revision=stock.item_revision, quantity=qty, unit=stock.unit, command_scope='adjustment', idempotency_key=idempotency_key, payload_signature=signature, source_balance=stock, reason_code=reason_code, notes=notes)
    _mutate_on_hand(stock, qty)
    return _append_transaction(actor=actor, transaction_type=adjustment_type, item_revision=stock.item_revision, quantity=qty, unit=stock.unit, command_scope='adjustment', idempotency_key=idempotency_key, payload_signature=signature, destination_balance=stock, reason_code=reason_code, notes=notes)


def _status_move(*, actor, balance, quantity, target_status, transaction_type, command_scope, quality_hold=None, nonconformance=None, disposition=None, idempotency_key='', notes=''):
    stock = _balance_queryset().get(pk=balance.pk)
    qty = decimal_value(quantity)
    signature = _payload_signature(command_scope, {'balance': stock, 'quantity': qty, 'target_status': target_status, 'quality_hold': quality_hold, 'nonconformance': nonconformance, 'disposition': disposition})
    existing = _idempotent_transaction(command_scope, idempotency_key, signature)
    if existing:
        return existing
    if stock.available_quantity < qty and transaction_type == InventoryTransaction.TYPE_QUARANTINE:
        raise EngineeringLifecycleError({'quantity': 'Insufficient available stock for status movement.'})
    if stock.on_hand_quantity < qty:
        raise EngineeringLifecycleError({'quantity': 'Insufficient on-hand stock for status movement.'})
    target = _find_balance(item_revision=stock.item_revision, warehouse=stock.warehouse, location=stock.location, lot=stock.lot, serial=stock.serial, stock_status=target_status, ownership=stock.ownership, unit=stock.unit)
    _mutate_on_hand(stock, -qty)
    _mutate_on_hand(target, qty)
    return _append_transaction(actor=actor, transaction_type=transaction_type, item_revision=stock.item_revision, quantity=qty, unit=stock.unit, command_scope=command_scope, idempotency_key=idempotency_key, payload_signature=signature, source_balance=stock, destination_balance=target, production_order=quality_hold.production_order if quality_hold else nonconformance.production_order if nonconformance else None, quality_hold=quality_hold, nonconformance=nonconformance, disposition=disposition, notes=notes)


@transaction.atomic
def quarantine_stock(*, actor, balance: InventoryBalance, quantity, quality_hold: QualityHold | None = None, nonconformance: NonconformanceRecord | None = None, idempotency_key='', notes=''):
    _require_authenticated(actor)
    if not quality_hold and not nonconformance:
        raise EngineeringLifecycleError({'quality': 'Quarantine requires a quality hold or nonconformance reference.'})
    return _status_move(actor=actor, balance=balance, quantity=quantity, target_status=InventoryBalance.STATUS_QUARANTINED, transaction_type=InventoryTransaction.TYPE_QUARANTINE, command_scope='quarantine', quality_hold=quality_hold, nonconformance=nonconformance, idempotency_key=idempotency_key, notes=notes)


@transaction.atomic
def quality_release_stock(*, actor, balance: InventoryBalance, quantity, quality_hold: QualityHold | None = None, disposition: QualityDisposition | None = None, idempotency_key='', notes=''):
    _require_manager(actor, 'release quarantined stock')
    if balance.stock_status != InventoryBalance.STATUS_QUARANTINED:
        raise EngineeringLifecycleError({'stock_status': 'Only quarantined stock can be quality released.'})
    if quality_hold and quality_hold.active:
        raise EngineeringLifecycleError({'quality_hold': 'Quality hold must be released before stock quality release.'})
    if disposition and disposition.status not in {QualityDisposition.STATUS_APPROVED, QualityDisposition.STATUS_IMPLEMENTED}:
        raise EngineeringLifecycleError({'disposition': 'Quality release requires an approved or implemented disposition.'})
    return _status_move(actor=actor, balance=balance, quantity=quantity, target_status=InventoryBalance.STATUS_AVAILABLE, transaction_type=InventoryTransaction.TYPE_QUALITY_RELEASE, command_scope='quality_release', quality_hold=quality_hold, disposition=disposition, idempotency_key=idempotency_key, notes=notes)


@transaction.atomic
def scrap_stock(*, actor, balance: InventoryBalance, quantity, nonconformance: NonconformanceRecord | None = None, disposition: QualityDisposition | None = None, idempotency_key='', notes=''):
    _require_manager(actor, 'scrap inventory')
    if disposition and disposition.disposition_type != QualityDisposition.TYPE_SCRAP:
        raise EngineeringLifecycleError({'disposition': 'Scrap posting requires a scrap disposition.'})
    return _status_move(actor=actor, balance=balance, quantity=quantity, target_status=InventoryBalance.STATUS_SCRAP, transaction_type=InventoryTransaction.TYPE_SCRAP, command_scope='scrap', nonconformance=nonconformance, disposition=disposition, idempotency_key=idempotency_key, notes=notes)


def rebuild_inventory_balances():
    for balance in InventoryBalance.objects.select_for_update():
        balance.on_hand_quantity = Decimal('0')
        balance.reserved_quantity = Decimal('0')
        balance.balance_version += 1
        balance.save(update_fields=['on_hand_quantity', 'reserved_quantity', 'balance_version', 'updated_at'])
    for tx in InventoryTransaction.objects.order_by('server_recorded_at', 'transaction_number'):
        if tx.source_location_id:
            source = _find_balance(item_revision=tx.item_revision, warehouse=tx.source_warehouse, location=tx.source_location, lot=tx.source_lot, serial=tx.source_serial, stock_status=tx.source_stock_status, ownership=tx.ownership, unit=tx.unit)
            if tx.transaction_type == InventoryTransaction.TYPE_RESERVATION_RELEASE:
                _mutate_reserved(source, -tx.quantity)
            elif tx.transaction_type == InventoryTransaction.TYPE_PRODUCTION_ISSUE:
                _mutate_on_hand(source, -tx.quantity)
            elif tx.transaction_type in {InventoryTransaction.TYPE_TRANSFER, InventoryTransaction.TYPE_QUARANTINE, InventoryTransaction.TYPE_QUALITY_RELEASE, InventoryTransaction.TYPE_SCRAP, InventoryTransaction.TYPE_ADJUSTMENT_OUT}:
                _mutate_on_hand(source, -tx.quantity)
            source.balance_version += 1
            source.last_transaction = tx
            source.save(update_fields=['on_hand_quantity', 'reserved_quantity', 'balance_version', 'last_transaction', 'updated_at'])
        if tx.destination_location_id:
            destination = _find_balance(item_revision=tx.item_revision, warehouse=tx.destination_warehouse, location=tx.destination_location, lot=tx.destination_lot, serial=tx.destination_serial, stock_status=tx.destination_stock_status, ownership=tx.ownership, unit=tx.unit)
            if tx.transaction_type == InventoryTransaction.TYPE_RESERVATION:
                _mutate_reserved(destination, tx.quantity)
            elif tx.transaction_type in {InventoryTransaction.TYPE_RECEIPT, InventoryTransaction.TYPE_TRANSFER, InventoryTransaction.TYPE_PRODUCTION_RETURN, InventoryTransaction.TYPE_QUARANTINE, InventoryTransaction.TYPE_QUALITY_RELEASE, InventoryTransaction.TYPE_SCRAP, InventoryTransaction.TYPE_ADJUSTMENT_IN}:
                _mutate_on_hand(destination, tx.quantity)
            destination.balance_version += 1
            destination.last_transaction = tx
            destination.save(update_fields=['on_hand_quantity', 'reserved_quantity', 'balance_version', 'last_transaction', 'updated_at'])


def material_reconciliation(requirement: ProductionMaterialRequirement) -> dict:
    txs = requirement.inventory_transactions.all()
    issued = txs.filter(transaction_type=InventoryTransaction.TYPE_PRODUCTION_ISSUE).aggregate(total=Sum('quantity'))['total'] or Decimal('0')
    returned = txs.filter(transaction_type=InventoryTransaction.TYPE_PRODUCTION_RETURN).aggregate(total=Sum('quantity'))['total'] or Decimal('0')
    reserved = requirement.inventory_reservations.exclude(status=InventoryReservation.STATUS_CANCELLED).aggregate(total=Sum('quantity'), released=Sum('released_quantity')) 
    reserved_qty = (reserved['total'] or Decimal('0')) - (reserved['released'] or Decimal('0'))
    consumed_total = requirement.consumptions.aggregate(total=Sum('quantity'))['total'] or Decimal('0')
    consumed = requirement.consumptions.filter(consumption_type=ProductionMaterialConsumption.TYPE_CONSUME).aggregate(total=Sum('quantity'))['total'] or Decimal('0')
    reversed_qty = abs(requirement.consumptions.filter(consumption_type=ProductionMaterialConsumption.TYPE_REVERSAL).aggregate(total=Sum('quantity'))['total'] or Decimal('0'))
    net_issued = issued - returned
    net_consumed = consumed_total
    return {
        'requirement': str(requirement.pk),
        'planned': str(decimal_value(requirement.total_planned_quantity)),
        'reserved': str(decimal_value(reserved_qty)),
        'issued': str(decimal_value(issued)),
        'returned': str(decimal_value(returned)),
        'net_issued': str(decimal_value(net_issued)),
        'consumed': str(decimal_value(consumed)),
        'reversed': str(decimal_value(reversed_qty)),
        'net_consumed': str(decimal_value(net_consumed)),
        'variance': str(decimal_value(net_issued - net_consumed)),
        'shortage': str(decimal_value(max(Decimal('0'), requirement.total_planned_quantity - reserved_qty))),
    }


def order_material_summary(order: ProductionOrder) -> dict:
    requirements = list(order.material_requirements.all())
    rows = [material_reconciliation(req) for req in requirements]
    shortage_count = sum(1 for row in rows if Decimal(row['shortage']) > 0)
    variance_count = sum(1 for row in rows if Decimal(row['variance']) != 0)
    return {
        'requirement_count': len(rows),
        'shortage_count': shortage_count,
        'variance_count': variance_count,
        'ready': shortage_count == 0,
        'rows': rows,
    }


def operation_material_summary(execution: OperationExecution) -> dict:
    requirements = execution.production_order.material_requirements.filter(operation=execution.manufacturing_operation)
    rows = [material_reconciliation(req) for req in requirements]
    shortage_count = sum(1 for row in rows if Decimal(row['shortage']) > 0)
    variance_count = sum(1 for row in rows if Decimal(row['variance']) != 0)
    return {'required_materials': len(rows), 'shortage_count': shortage_count, 'variance_count': variance_count, 'ready': shortage_count == 0, 'rows': rows}


def material_queue(order_id=None):
    queryset = ProductionMaterialRequirement.objects.select_related('production_order', 'operation', 'component_item_revision__item').prefetch_related('inventory_reservations', 'inventory_transactions', 'consumptions')
    if order_id:
        queryset = queryset.filter(production_order_id=order_id)
    return [{**material_reconciliation(req), 'production_order': str(req.production_order_id), 'order_number': req.production_order.order_number, 'operation': str(req.operation_id), 'operation_number': req.operation.operation_number, 'component_item_revision': str(req.component_item_revision_id), 'component_code': req.component_code_snapshot, 'unit': req.unit} for req in queryset.order_by('production_order__order_number', 'operation__sequence', 'sequence')]
