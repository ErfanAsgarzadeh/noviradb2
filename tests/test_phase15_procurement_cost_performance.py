from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from threading import Barrier, Thread

import pytest
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from opc.cost_performance_services import create_operational_kpi_snapshot, create_receipt_cost_snapshot
from opc.execution_services import ensure_executions_for_order
from opc.models import (
    ApprovedSupplierItem,
    CostSnapshot,
    InventoryBalance,
    OperationalKPISnapshot,
    ProcurementEvent,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseReceiptLine,
    RequestForQuotation,
    RFQLine,
    RFQSupplierInvitation,
    SourcingDecision,
    SourcingDecisionLine,
    Supplier,
    SupplierQuotation,
    SupplierQuotationLine,
)
from opc.procurement_services import (
    approve_sourcing_decision,
    approve_supplier_item,
    convert_sourcing_to_purchase_order,
    create_purchase_return,
    create_quotation_comparison,
    create_supplier_performance_snapshot,
    receive_purchase_order_line,
    submit_quotation,
    transition_purchase_order,
    transition_rfq,
    transition_supplier,
)
from opc.production_services import generate_order_baseline, release_production_order
from tests.factories import make_company_admin
from tests.test_opc_graph_persistence_phase3 import api
from tests.test_opc_phase10_inventory import make_location
from tests.test_opc_phase7_production_orders import create_order, released_source


pytestmark = pytest.mark.django_db


def phase15_fixture(code='P15'):
    admin, diagram, _nodes, _edges, refs = released_source(f'P-{code}')
    refs['diagram'] = diagram
    warehouse, location = make_location(code)
    supplier = Supplier.objects.create(supplier_code=f'SUP-{code}', name=f'Supplier {code}', created_by=admin)
    supplier = transition_supplier(supplier, actor=admin, target_status=Supplier.STATUS_QUALIFIED, expected_version=0, idempotency_key=f'supplier-{code}')
    asi = ApprovedSupplierItem.objects.create(supplier=supplier, item_revision=refs['component'], supplier_item_code=f'COMP-{code}', last_unit_price='12.500000', currency='USD')
    approve_supplier_item(asi, actor=admin)
    return admin, refs, warehouse, location, supplier


def sourcing_fixture(code='P15SRC'):
    admin, refs, warehouse, location, supplier = phase15_fixture(code)
    rfq = RequestForQuotation.objects.create(rfq_number=f'RFQ-{code}', title='Motor bracket buy', currency='USD', created_by=admin)
    rfq_line = RFQLine.objects.create(rfq=rfq, line_number=1, item_revision=refs['component'], quantity='10.000000', unit='EA', required_date=date(2026, 11, 10), delivery_warehouse=warehouse, delivery_location=location)
    RFQSupplierInvitation.objects.create(rfq=rfq, supplier=supplier)
    transition_rfq(rfq, actor=admin, target_status=RequestForQuotation.STATUS_RELEASED, expected_version=0, idempotency_key=f'rfq-release-{code}')
    quote = SupplierQuotation.objects.create(quotation_number=f'QT-{code}', rfq=rfq, supplier=supplier, currency='USD', technical_score='90.000', commercial_score='85.000', created_by=admin)
    qline = SupplierQuotationLine.objects.create(quotation=quote, rfq_line=rfq_line, line_number=1, quantity='10.000000', unit_price='12.500000', lead_time_days=7, promised_date=date(2026, 11, 8))
    submit_quotation(quote, actor=admin, expected_version=0, idempotency_key=f'quote-submit-{code}')
    comparison = create_quotation_comparison(rfq, actor=admin)
    decision = SourcingDecision.objects.create(decision_number=f'SD-{code}', rfq=rfq, comparison_snapshot=comparison, supplier=supplier, quotation=quote, created_by=admin)
    SourcingDecisionLine.objects.create(decision=decision, quotation_line=qline, rfq_line=rfq_line, item_revision=refs['component'], quantity='10.000000', unit='EA', unit_price='12.500000', currency='USD', promised_date=date(2026, 11, 8), delivery_warehouse=warehouse, delivery_location=location)
    return admin, refs, warehouse, location, supplier, rfq, quote, decision


def test_supplier_lifecycle_approved_item_rfq_quote_comparison_and_award():
    admin, refs, _warehouse, _location, supplier, rfq, quote, decision = sourcing_fixture('FLOW')
    assert supplier.status == Supplier.STATUS_QUALIFIED
    assert ApprovedSupplierItem.objects.get(supplier=supplier, item_revision=refs['component']).status == ApprovedSupplierItem.STATUS_APPROVED
    assert RequestForQuotation.objects.get(pk=rfq.pk).status == RequestForQuotation.STATUS_RELEASED
    assert SupplierQuotation.objects.get(pk=quote.pk).status == SupplierQuotation.STATUS_SUBMITTED
    assert rfq.comparison_snapshots.get().recommended_supplier == supplier

    approved = approve_sourcing_decision(decision, actor=admin, expected_version=0, idempotency_key='award-flow')
    assert approved.status == SourcingDecision.STATUS_APPROVED
    assert ProcurementEvent.objects.filter(event_type='SOURCING_APPROVED', sourcing_decision=approved).exists()
    with pytest.raises(ValidationError):
        approved.supplier = Supplier.objects.create(supplier_code='SUP-OTHER-FLOW', name='Other')
        approved.save()


def test_sourcing_to_po_supplier_confirmation_partial_receipt_inventory_quarantine_return_and_cost():
    admin, _refs, warehouse, location, supplier, _rfq, _quote, decision = sourcing_fixture('PO')
    approved = approve_sourcing_decision(decision, actor=admin, expected_version=0, idempotency_key='award-po')
    po = convert_sourcing_to_purchase_order(approved, actor=admin, expected_version=1, idempotency_key='convert-po')
    assert po.lines.count() == 1
    po = transition_purchase_order(po, actor=admin, target_status=PurchaseOrder.STATUS_APPROVED, expected_version=0, idempotency_key='po-approve')
    po = transition_purchase_order(po, actor=admin, target_status=PurchaseOrder.STATUS_RELEASED, expected_version=1, idempotency_key='po-release')
    with pytest.raises(ValidationError):
        po.supplier = Supplier.objects.create(supplier_code='SUP-OTHER-PO', name='Other PO')
        po.save()
    po = transition_purchase_order(po, actor=admin, target_status=PurchaseOrder.STATUS_ACKNOWLEDGED, expected_version=2, supplier_reference='ACK-1', idempotency_key='po-ack')
    po = transition_purchase_order(po, actor=admin, target_status=PurchaseOrder.STATUS_CONFIRMED, expected_version=3, supplier_reference='CONF-1', idempotency_key='po-confirm')
    line = po.lines.get()
    receipt = receive_purchase_order_line(actor=admin, purchase_order_line=line, quantity='4.000000', warehouse=warehouse, location=location, accepted_quantity='3.000000', idempotency_key='receipt-4', packing_slip='PS-1')
    receipt_line = receipt.lines.get()
    balance = InventoryBalance.objects.get(pk=receipt_line.inventory_balance_id)
    line.refresh_from_db()
    po.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_PARTIALLY_RECEIVED
    assert line.received_quantity == Decimal('4.000000')
    assert balance.stock_status == InventoryBalance.STATUS_QUARANTINED
    assert receipt_line.quality_status == PurchaseReceiptLine.QUALITY_QUARANTINED

    ret = create_purchase_return(actor=admin, receipt_line=receipt_line, quantity='1.000000', reason='Damaged packaging', idempotency_key='return-1')
    assert ret.supplier == supplier
    snapshot = create_receipt_cost_snapshot(receipt=receipt, actor=admin)
    assert snapshot.total_cost == Decimal('50.000000')
    with pytest.raises(ValidationError):
        snapshot.total_cost = Decimal('1.000000')
        snapshot.save()


def test_procurement_api_conflict_payload_and_read_models():
    admin, _refs, _warehouse, _location, supplier = phase15_fixture('API')
    client = api(admin)
    stale = client.post(reverse('opc-supplier-suspend', kwargs={'pk': supplier.pk}), {'expected_version': 99, 'idempotency_key': 'stale-supplier'}, format='json')
    assert stale.status_code == status.HTTP_409_CONFLICT
    assert stale.data['code'] == 'version_conflict'
    ok = client.get(reverse('opc-supplier-list'), {'status': Supplier.STATUS_QUALIFIED})
    assert ok.status_code == status.HTTP_200_OK
    rows = ok.data['results'] if isinstance(ok.data, dict) and 'results' in ok.data else ok.data
    assert any(row['supplier_code'] == supplier.supplier_code for row in rows)


def test_supplier_performance_oee_and_kpi_snapshots_are_immutable():
    admin, refs, warehouse, location, supplier, _rfq, _quote, decision = sourcing_fixture('KPI')
    approved = approve_sourcing_decision(decision, actor=admin, expected_version=0, idempotency_key='award-kpi')
    po = convert_sourcing_to_purchase_order(approved, actor=admin, expected_version=1, idempotency_key='convert-kpi')
    po = transition_purchase_order(po, actor=admin, target_status=PurchaseOrder.STATUS_APPROVED, expected_version=0)
    po = transition_purchase_order(po, actor=admin, target_status=PurchaseOrder.STATUS_RELEASED, expected_version=1)
    receipt = receive_purchase_order_line(actor=admin, purchase_order_line=po.lines.get(), quantity='2.000000', warehouse=warehouse, location=location, accepted_quantity='2.000000', idempotency_key='receipt-kpi', quarantine=False)
    supplier_snapshot = create_supplier_performance_snapshot(supplier=supplier, period_start=date(2026, 1, 1), period_end=date(2027, 1, 1))
    assert supplier_snapshot.receipt_count >= 1
    assert supplier_snapshot.quality_acceptance_percent == Decimal('100.000')

    order = release_production_order(generate_order_baseline(create_order(admin, refs['diagram'], refs), actor=admin, expected_version=0), actor=admin, expected_version=1)
    ensure_executions_for_order(order, actor=admin)
    execution = order.operation_executions.first()
    execution.produced_quantity = Decimal('5.000000')
    execution.accepted_quantity = Decimal('4.000000')
    execution.scrapped_quantity = Decimal('1.000000')
    execution.actual_run_seconds = 1800
    execution.actual_setup_seconds = 300
    execution.save()
    start = timezone.make_aware(datetime(2026, 11, 1, 8))
    end = start + timedelta(hours=8)
    kpi = create_operational_kpi_snapshot(period_start=start, period_end=end, actor=admin, production_order=order)
    assert isinstance(kpi, OperationalKPISnapshot)
    assert kpi.quality_percent == Decimal('80.000')
    with pytest.raises(ValidationError):
        kpi.oee_percent = Decimal('1.000')
        kpi.save()
    assert receipt.receipt_number


@pytest.mark.django_db(transaction=True)
def test_postgresql_concurrent_po_release_is_single_writer():
    if connection.vendor != 'postgresql':
        pytest.skip('PostgreSQL row-lock concurrency test.')
    admin, _refs, _warehouse, _location, _supplier, _rfq, _quote, decision = sourcing_fixture('CONC')
    approved = approve_sourcing_decision(decision, actor=admin, expected_version=0)
    po = convert_sourcing_to_purchase_order(approved, actor=admin, expected_version=1)
    po = transition_purchase_order(po, actor=admin, target_status=PurchaseOrder.STATUS_APPROVED, expected_version=0)
    barrier = Barrier(2)
    results = []

    def worker(key):
        close_old_connections()
        barrier.wait()
        try:
            transition_purchase_order(PurchaseOrder.objects.get(pk=po.pk), actor=admin, target_status=PurchaseOrder.STATUS_RELEASED, expected_version=1, idempotency_key=key)
            results.append('ok')
        except Exception:
            results.append('conflict')
        finally:
            close_old_connections()

    threads = [Thread(target=worker, args=(f'release-{idx}',)) for idx in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results.count('ok') == 1
