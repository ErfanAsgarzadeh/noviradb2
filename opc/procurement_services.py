from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.db.models import Max, Sum
from django.utils import timezone

from enterprise_items.exceptions import EngineeringLifecycleError

from .inventory_services import receive_inventory
from .models import (
    ApprovedSupplierItem,
    InventoryBalance,
    OperationalKPISnapshot,
    ProcurementEvent,
    PurchaseOrder,
    PurchaseOrderDeliverySchedule,
    PurchaseOrderLine,
    PurchaseReceipt,
    PurchaseReceiptLine,
    PurchaseReturn,
    QuotationComparisonSnapshot,
    RequestForQuotation,
    RFQSupplierInvitation,
    SourcingDecision,
    SourcingDecisionLine,
    Supplier,
    SupplierPerformanceSnapshot,
    SupplierQuotation,
    SupplierQuotationLine,
)


class ProcurementConflict(EngineeringLifecycleError):
    def __init__(self, code, message, **extra):
        self.payload = {'code': code, 'detail': message, **extra}
        super().__init__(self.payload)


def decimal_value(value):
    return Decimal(str(value or '0')).quantize(Decimal('0.000001'))


def _require_actor(actor):
    if not actor or not getattr(actor, 'is_authenticated', False):
        raise EngineeringLifecycleError({'actor': 'Authenticated actor is required.'})


def _check_version(instance, field, expected_version):
    if expected_version is None:
        return
    expected = int(expected_version)
    current = getattr(instance, field)
    if current != expected:
        raise ProcurementConflict('version_conflict', 'Object version conflict.', expected_version=expected, current_version=current, object_id=str(instance.pk))


def _next_number(model, field, prefix):
    latest = model.objects.aggregate(Max(field))[f'{field}__max']
    if latest and str(latest).startswith(prefix):
        try:
            number = int(str(latest).split('-')[-1]) + 1
        except ValueError:
            number = 1
    else:
        number = 1
    return f'{prefix}{number:06d}'


def _append_event(event_type, *, actor=None, supplier=None, rfq=None, quotation=None, sourcing_decision=None, purchase_order=None, receipt=None, idempotency_key='', payload=None):
    if idempotency_key:
        existing = ProcurementEvent.objects.filter(event_type=event_type, idempotency_key=idempotency_key).first()
        if existing:
            return existing
    return ProcurementEvent.objects.create(
        event_number=_next_number(ProcurementEvent, 'event_number', f'PE-{timezone.now():%Y}-'),
        event_type=event_type,
        supplier=supplier,
        rfq=rfq,
        quotation=quotation,
        sourcing_decision=sourcing_decision,
        purchase_order=purchase_order,
        receipt=receipt,
        actor=actor if actor and getattr(actor, 'is_authenticated', False) else None,
        event_timestamp=timezone.now(),
        idempotency_key=idempotency_key,
        payload=payload or {},
    )


@transaction.atomic
def transition_supplier(supplier: Supplier, *, actor, target_status, expected_version=None, idempotency_key=''):
    _require_actor(actor)
    row = Supplier.objects.select_for_update().get(pk=supplier.pk)
    _check_version(row, 'supplier_version', expected_version)
    allowed = {
        Supplier.STATUS_DRAFT: {Supplier.STATUS_QUALIFIED, Supplier.STATUS_DISQUALIFIED},
        Supplier.STATUS_QUALIFIED: {Supplier.STATUS_SUSPENDED, Supplier.STATUS_DISQUALIFIED},
        Supplier.STATUS_SUSPENDED: {Supplier.STATUS_QUALIFIED, Supplier.STATUS_DISQUALIFIED},
        Supplier.STATUS_DISQUALIFIED: {Supplier.STATUS_DRAFT},
    }
    if target_status not in allowed.get(row.status, set()):
        raise EngineeringLifecycleError({'status': f'Supplier cannot move from {row.status} to {target_status}.'})
    row.status = target_status
    row.supplier_version += 1
    if target_status == Supplier.STATUS_QUALIFIED:
        row.approved_by = actor
        row.approved_at = timezone.now()
    row.save(update_fields=['status', 'supplier_version', 'approved_by', 'approved_at', 'updated_at'])
    _append_event('SUPPLIER_STATUS', actor=actor, supplier=row, idempotency_key=idempotency_key, payload={'status': target_status})
    return row


@transaction.atomic
def approve_supplier_item(asi: ApprovedSupplierItem, *, actor):
    _require_actor(actor)
    row = ApprovedSupplierItem.objects.select_for_update().get(pk=asi.pk)
    if row.supplier.status != Supplier.STATUS_QUALIFIED:
        raise EngineeringLifecycleError({'supplier': 'Approved supplier item requires a qualified supplier.'})
    row.status = ApprovedSupplierItem.STATUS_APPROVED
    row.approved_by = actor
    row.approved_at = timezone.now()
    row.save(update_fields=['status', 'approved_by', 'approved_at', 'updated_at'])
    return row


@transaction.atomic
def transition_rfq(rfq: RequestForQuotation, *, actor, target_status, expected_version=None, idempotency_key=''):
    _require_actor(actor)
    row = RequestForQuotation.objects.select_for_update().get(pk=rfq.pk)
    _check_version(row, 'rfq_version', expected_version)
    allowed = {
        RequestForQuotation.STATUS_DRAFT: {RequestForQuotation.STATUS_RELEASED, RequestForQuotation.STATUS_CANCELLED},
        RequestForQuotation.STATUS_RELEASED: {RequestForQuotation.STATUS_CLOSED, RequestForQuotation.STATUS_CANCELLED},
    }
    if target_status not in allowed.get(row.status, set()):
        raise EngineeringLifecycleError({'status': f'RFQ cannot move from {row.status} to {target_status}.'})
    if target_status == RequestForQuotation.STATUS_RELEASED:
        if not row.lines.exists():
            raise EngineeringLifecycleError({'lines': 'RFQ requires at least one line before release.'})
        row.released_by = actor
        row.released_at = timezone.now()
        for invitation in row.invitations.select_related('supplier'):
            if invitation.supplier.status != Supplier.STATUS_QUALIFIED:
                raise EngineeringLifecycleError({'supplier': 'RFQ invitations require qualified suppliers.'})
            invitation.status = RFQSupplierInvitation.STATUS_SENT
            invitation.invited_at = timezone.now()
            invitation.save(update_fields=['status', 'invited_at'])
    row.status = target_status
    row.rfq_version += 1
    row.save(update_fields=['status', 'rfq_version', 'released_by', 'released_at', 'updated_at'])
    if target_status == RequestForQuotation.STATUS_RELEASED:
        _append_event('RFQ_RELEASED', actor=actor, rfq=row, idempotency_key=idempotency_key, payload={'rfq': row.rfq_number})
    return row


@transaction.atomic
def submit_quotation(quotation: SupplierQuotation, *, actor, expected_version=None, idempotency_key=''):
    _require_actor(actor)
    row = SupplierQuotation.objects.select_for_update(of=('self',)).select_related('rfq', 'supplier').get(pk=quotation.pk)
    _check_version(row, 'quotation_version', expected_version)
    if row.status != SupplierQuotation.STATUS_DRAFT:
        raise EngineeringLifecycleError({'status': 'Only draft quotations can be submitted.'})
    if row.rfq.status != RequestForQuotation.STATUS_RELEASED:
        raise EngineeringLifecycleError({'rfq': 'Quotation submission requires a released RFQ.'})
    if row.supplier.status != Supplier.STATUS_QUALIFIED:
        raise EngineeringLifecycleError({'supplier': 'Quotation submission requires a qualified supplier.'})
    if not row.lines.exists():
        raise EngineeringLifecycleError({'lines': 'Quotation requires at least one line.'})
    row.status = SupplierQuotation.STATUS_SUBMITTED
    row.submitted_at = timezone.now()
    row.quotation_version += 1
    row.save(update_fields=['status', 'submitted_at', 'quotation_version', 'updated_at'])
    RFQSupplierInvitation.objects.filter(rfq=row.rfq, supplier=row.supplier).update(status=RFQSupplierInvitation.STATUS_RESPONDED, responded_at=timezone.now())
    _append_event('QUOTE_SUBMITTED', actor=actor, supplier=row.supplier, rfq=row.rfq, quotation=row, idempotency_key=idempotency_key)
    return row


@transaction.atomic
def create_quotation_comparison(rfq: RequestForQuotation, *, actor, policy_version='quotation-comparison-v1'):
    _require_actor(actor)
    row = RequestForQuotation.objects.select_for_update().get(pk=rfq.pk)
    quotes = SupplierQuotation.objects.filter(rfq=row, status=SupplierQuotation.STATUS_SUBMITTED).prefetch_related('lines__rfq_line', 'supplier')
    if not quotes.exists():
        raise EngineeringLifecycleError({'quotations': 'Comparison requires at least one submitted quotation.'})
    commercial = []
    technical = []
    best = None
    best_total = None
    for quote in quotes:
        total = quote.lines.aggregate(total=Sum('unit_price'))['total'] or Decimal('0')
        extended_total = sum((line.extended_price for line in quote.lines.all()), Decimal('0'))
        row_payload = {
            'quotation': str(quote.pk),
            'quotation_number': quote.quotation_number,
            'supplier': str(quote.supplier_id),
            'supplier_code': quote.supplier.supplier_code,
            'currency': quote.currency,
            'line_unit_price_total': str(decimal_value(total)),
            'extended_total': str(decimal_value(extended_total)),
            'commercial_score': str(quote.commercial_score),
        }
        commercial.append(row_payload)
        technical.append({'quotation': str(quote.pk), 'supplier_code': quote.supplier.supplier_code, 'technical_score': str(quote.technical_score)})
        if best_total is None or extended_total < best_total:
            best_total = extended_total
            best = quote
    return QuotationComparisonSnapshot.objects.create(
        rfq=row,
        snapshot_number=_next_number(QuotationComparisonSnapshot, 'snapshot_number', f'QC-{timezone.now():%Y}-'),
        policy_version=policy_version,
        technical_matrix=technical,
        commercial_matrix=commercial,
        recommended_supplier=best.supplier if best else None,
        recommended_quotation=best,
        created_by=actor,
    )


@transaction.atomic
def approve_sourcing_decision(decision: SourcingDecision, *, actor, expected_version=None, idempotency_key=''):
    _require_actor(actor)
    row = SourcingDecision.objects.select_for_update(of=('self',)).select_related('supplier', 'quotation').get(pk=decision.pk)
    _check_version(row, 'decision_version', expected_version)
    if row.status != SourcingDecision.STATUS_DRAFT:
        raise EngineeringLifecycleError({'status': 'Only draft sourcing decisions can be approved.'})
    if row.supplier.status != Supplier.STATUS_QUALIFIED:
        raise EngineeringLifecycleError({'supplier': 'Award requires a qualified supplier.'})
    if not row.lines.exists():
        raise EngineeringLifecycleError({'lines': 'Sourcing decision requires at least one awarded line.'})
    row.status = SourcingDecision.STATUS_APPROVED
    row.approved_by = actor
    row.approved_at = timezone.now()
    row.decision_version += 1
    row.save(update_fields=['status', 'approved_by', 'approved_at', 'decision_version', 'updated_at'])
    if row.quotation_id:
        row.quotation.status = SupplierQuotation.STATUS_AWARDED
        row.quotation.save(update_fields=['status', 'updated_at'])
    _append_event('SOURCING_APPROVED', actor=actor, supplier=row.supplier, rfq=row.rfq, quotation=row.quotation, sourcing_decision=row, idempotency_key=idempotency_key)
    return row


@transaction.atomic
def convert_sourcing_to_purchase_order(decision: SourcingDecision, *, actor, expected_version=None, idempotency_key=''):
    _require_actor(actor)
    existing = None
    if idempotency_key:
        event = ProcurementEvent.objects.filter(event_type='PO_RELEASED', idempotency_key=f'convert:{idempotency_key}').select_related('purchase_order').first()
        existing = event.purchase_order if event else None
    if existing:
        return existing
    row = SourcingDecision.objects.select_for_update(of=('self',)).select_related('supplier', 'quotation').get(pk=decision.pk)
    _check_version(row, 'decision_version', expected_version)
    if row.status not in {SourcingDecision.STATUS_APPROVED, SourcingDecision.STATUS_CONVERTED}:
        raise EngineeringLifecycleError({'status': 'Sourcing decision must be approved before PO conversion.'})
    if row.converted_purchase_order_id:
        return row.converted_purchase_order
    po = PurchaseOrder.objects.create(
        po_number=_next_number(PurchaseOrder, 'po_number', f'PO-{timezone.now():%Y}-'),
        supplier=row.supplier,
        supplier_site=row.quotation.supplier_site if row.quotation_id else None,
        status=PurchaseOrder.STATUS_DRAFT,
        currency=row.quotation.currency if row.quotation_id else 'USD',
        sourcing_decision=row,
        quotation=row.quotation,
        project=row.project,
        created_by=actor,
    )
    for idx, line in enumerate(row.lines.select_related('item_revision', 'delivery_warehouse', 'delivery_location'), start=1):
        PurchaseOrderLine.objects.create(
            purchase_order=po,
            line_number=idx,
            item_revision=line.item_revision,
            quantity=line.quantity,
            unit=line.unit,
            unit_price=line.unit_price,
            currency=line.currency,
            sourcing_decision_line=line,
            delivery_warehouse=line.delivery_warehouse,
            delivery_location=line.delivery_location,
            required_date=line.promised_date,
        )
    row.status = SourcingDecision.STATUS_CONVERTED
    row.converted_purchase_order = po
    row.decision_version += 1
    row.save(update_fields=['status', 'converted_purchase_order', 'decision_version', 'updated_at'])
    _append_event('PO_RELEASED', actor=actor, supplier=po.supplier, sourcing_decision=row, purchase_order=po, idempotency_key=f'convert:{idempotency_key}' if idempotency_key else '', payload={'converted': True})
    return po


@transaction.atomic
def transition_purchase_order(po: PurchaseOrder, *, actor, target_status, expected_version=None, idempotency_key='', supplier_reference=''):
    _require_actor(actor)
    row = PurchaseOrder.objects.select_for_update().get(pk=po.pk)
    _check_version(row, 'po_version', expected_version)
    allowed = {
        PurchaseOrder.STATUS_DRAFT: {PurchaseOrder.STATUS_APPROVED, PurchaseOrder.STATUS_CANCELLED},
        PurchaseOrder.STATUS_APPROVED: {PurchaseOrder.STATUS_RELEASED, PurchaseOrder.STATUS_CANCELLED},
        PurchaseOrder.STATUS_RELEASED: {PurchaseOrder.STATUS_ACKNOWLEDGED, PurchaseOrder.STATUS_CONFIRMED, PurchaseOrder.STATUS_CANCELLED},
        PurchaseOrder.STATUS_ACKNOWLEDGED: {PurchaseOrder.STATUS_CONFIRMED, PurchaseOrder.STATUS_CANCELLED},
        PurchaseOrder.STATUS_CONFIRMED: {PurchaseOrder.STATUS_CANCELLED, PurchaseOrder.STATUS_CLOSED},
        PurchaseOrder.STATUS_PARTIALLY_RECEIVED: {PurchaseOrder.STATUS_CLOSED, PurchaseOrder.STATUS_CANCELLED},
        PurchaseOrder.STATUS_RECEIVED: {PurchaseOrder.STATUS_CLOSED},
    }
    if target_status not in allowed.get(row.status, set()):
        raise EngineeringLifecycleError({'status': f'PO cannot move from {row.status} to {target_status}.'})
    now = timezone.now()
    if target_status == PurchaseOrder.STATUS_APPROVED:
        row.approved_by = actor
        row.approved_at = now
    if target_status == PurchaseOrder.STATUS_RELEASED:
        if not row.lines.exists():
            raise EngineeringLifecycleError({'lines': 'PO requires at least one line before release.'})
        row.released_by = actor
        row.released_at = now
    if target_status == PurchaseOrder.STATUS_ACKNOWLEDGED:
        row.acknowledged_at = now
        row.supplier_reference = supplier_reference or row.supplier_reference
    if target_status == PurchaseOrder.STATUS_CONFIRMED:
        row.confirmed_at = now
        row.supplier_reference = supplier_reference or row.supplier_reference
        for line in row.lines.all():
            if not line.schedules.exists():
                PurchaseOrderDeliverySchedule.objects.create(purchase_order_line=line, schedule_number=1, quantity=line.quantity, promised_date=line.required_date or timezone.localdate())
    row.status = target_status
    row.po_version += 1
    row.save()
    if target_status == PurchaseOrder.STATUS_RELEASED:
        _append_event('PO_RELEASED', actor=actor, supplier=row.supplier, purchase_order=row, idempotency_key=idempotency_key)
    elif target_status == PurchaseOrder.STATUS_CONFIRMED:
        _append_event('PO_CONFIRMED', actor=actor, supplier=row.supplier, purchase_order=row, idempotency_key=idempotency_key)
    return row


@transaction.atomic
def receive_purchase_order_line(*, actor, purchase_order_line: PurchaseOrderLine, quantity, warehouse=None, location=None, accepted_quantity=None, idempotency_key='', packing_slip='', quarantine=True):
    _require_actor(actor)
    if idempotency_key:
        existing = PurchaseReceipt.objects.filter(purchase_order=purchase_order_line.purchase_order, idempotency_key=idempotency_key).first()
        if existing:
            return existing
    line = PurchaseOrderLine.objects.select_for_update(of=('self',)).select_related('purchase_order', 'item_revision').get(pk=purchase_order_line.pk)
    po = PurchaseOrder.objects.select_for_update().get(pk=line.purchase_order_id)
    if po.status not in {PurchaseOrder.STATUS_RELEASED, PurchaseOrder.STATUS_ACKNOWLEDGED, PurchaseOrder.STATUS_CONFIRMED, PurchaseOrder.STATUS_PARTIALLY_RECEIVED}:
        raise EngineeringLifecycleError({'status': 'PO must be released, acknowledged or confirmed before receipt.'})
    qty = decimal_value(quantity)
    accepted = decimal_value(accepted_quantity if accepted_quantity is not None else 0)
    if qty <= 0:
        raise EngineeringLifecycleError({'quantity': 'Receipt quantity must be positive.'})
    if line.received_quantity + qty > line.quantity:
        raise EngineeringLifecycleError({'quantity': 'Receipt would exceed open PO line quantity.'})
    dest_wh = warehouse or line.delivery_warehouse
    dest_loc = location or line.delivery_location
    if not dest_wh or not dest_loc:
        raise EngineeringLifecycleError({'location': 'Receipt requires a destination warehouse and location.'})
    stock_status = InventoryBalance.STATUS_QUARANTINED if quarantine else InventoryBalance.STATUS_AVAILABLE
    inv_tx, balance = receive_inventory(
        actor=actor,
        item_revision=line.item_revision,
        quantity=qty,
        unit=line.unit,
        warehouse=dest_wh,
        location=dest_loc,
        stock_status=stock_status,
        idempotency_key=f'po-receipt:{idempotency_key}' if idempotency_key else '',
        reason_code='PURCHASE_RECEIPT',
        notes=f'PO {po.po_number} line {line.line_number}',
    )
    receipt = PurchaseReceipt.objects.create(
        receipt_number=_next_number(PurchaseReceipt, 'receipt_number', f'PRC-{timezone.now():%Y}-'),
        purchase_order=po,
        supplier=po.supplier,
        received_at=timezone.now(),
        packing_slip=packing_slip,
        idempotency_key=idempotency_key,
        received_by=actor,
    )
    PurchaseReceiptLine.objects.create(
        receipt=receipt,
        purchase_order_line=line,
        item_revision=line.item_revision,
        quantity=qty,
        accepted_quantity=accepted,
        unit=line.unit,
        warehouse=dest_wh,
        location=dest_loc,
        inventory_transaction=inv_tx,
        inventory_balance=balance,
        quality_status=PurchaseReceiptLine.QUALITY_QUARANTINED if quarantine else PurchaseReceiptLine.QUALITY_RELEASED,
    )
    line.received_quantity = decimal_value(line.received_quantity + qty)
    line.accepted_quantity = decimal_value(line.accepted_quantity + accepted)
    line.line_version += 1
    line.save(update_fields=['received_quantity', 'accepted_quantity', 'line_version', 'updated_at'])
    open_total = sum((l.open_quantity for l in po.lines.all()), Decimal('0'))
    po.status = PurchaseOrder.STATUS_RECEIVED if open_total == 0 else PurchaseOrder.STATUS_PARTIALLY_RECEIVED
    po.po_version += 1
    po.save(update_fields=['status', 'po_version', 'updated_at'])
    _append_event('RECEIPT_POSTED', actor=actor, supplier=po.supplier, purchase_order=po, receipt=receipt, idempotency_key=idempotency_key)
    return receipt


@transaction.atomic
def create_purchase_return(*, actor, receipt_line: PurchaseReceiptLine, quantity, reason='', idempotency_key=''):
    _require_actor(actor)
    line = PurchaseReceiptLine.objects.select_related('receipt__supplier', 'receipt__purchase_order').get(pk=receipt_line.pk)
    ret = PurchaseReturn.objects.create(
        return_number=_next_number(PurchaseReturn, 'return_number', f'PRT-{timezone.now():%Y}-'),
        supplier=line.receipt.supplier,
        purchase_order=line.receipt.purchase_order,
        receipt_line=line,
        quantity=decimal_value(quantity),
        reason=reason,
        created_by=actor,
    )
    _append_event('RETURN_CREATED', actor=actor, supplier=ret.supplier, purchase_order=ret.purchase_order, receipt=line.receipt, idempotency_key=idempotency_key, payload={'return': ret.return_number})
    return ret


def create_supplier_performance_snapshot(*, supplier: Supplier, period_start, period_end):
    receipts = PurchaseReceipt.objects.filter(supplier=supplier, received_at__date__gte=period_start, received_at__date__lte=period_end).prefetch_related('lines__purchase_order_line')
    receipt_count = receipts.count()
    late = 0
    accepted = Decimal('0')
    received = Decimal('0')
    for receipt in receipts:
        for line in receipt.lines.all():
            received += line.quantity
            accepted += line.accepted_quantity
            required = line.purchase_order_line.required_date
            if required and receipt.received_at.date() > required:
                late += 1
    on_time = Decimal('100') if receipt_count == 0 else Decimal('100') * Decimal(receipt_count - late) / Decimal(receipt_count)
    quality = Decimal('100') if received == 0 else Decimal('100') * accepted / received
    score = (on_time * Decimal('0.6')) + (quality * Decimal('0.4'))
    return SupplierPerformanceSnapshot.objects.create(
        supplier=supplier,
        snapshot_number=_next_number(SupplierPerformanceSnapshot, 'snapshot_number', f'SPS-{timezone.now():%Y}-'),
        period_start=period_start,
        period_end=period_end,
        on_time_delivery_percent=on_time.quantize(Decimal('0.001')),
        quality_acceptance_percent=quality.quantize(Decimal('0.001')),
        receipt_count=receipt_count,
        score=score.quantize(Decimal('0.001')),
        evidence={'received_quantity': str(decimal_value(received)), 'accepted_quantity': str(decimal_value(accepted)), 'late_receipts': late},
    )
