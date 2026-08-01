from __future__ import annotations

from datetime import date
from decimal import Decimal
from threading import Barrier, Thread

import pytest
from django.db import close_old_connections, connection
from django.urls import reverse
from rest_framework import status

from enterprise_items.models import BOM, BOMLine, BOMRevision, ItemRevision
from opc.inventory_services import receive_inventory
from opc.models import (
    ItemPlanningPolicy,
    MRPDemandSnapshot,
    MRPRecommendation,
    MRPRun,
    PlanningCalendar,
    PlanningDemand,
    ProductionOrder,
    PurchaseRequisition,
    ScheduledSupply,
)
from opc.mrp_services import (
    convert_recommendation,
    create_demand,
    run_mrp,
    transition_demand,
    transition_recommendation,
    update_demand,
)
from tests.factories import make_company_admin
from tests.test_engineering_bom_opc_phase1b import make_item_revision
from tests.test_opc_graph_persistence_phase3 import api
from tests.test_opc_phase7_production_orders import released_source
from tests.test_opc_phase10_inventory import make_location


pytestmark = pytest.mark.django_db


def make_mbom(parent, components):
    bom = BOM.objects.create(parent_item_revision=parent, bom_type=BOM.TYPE_MANUFACTURING)
    revision = BOMRevision.objects.create(bom=bom, revision='M1', status=BOMRevision.STATUS_RELEASED, effective_from=date(2026, 8, 1))
    for sequence, (component, quantity, extra) in enumerate(components, start=10):
        BOMLine.objects.create(
            bom_revision=revision,
            sequence=sequence,
            component_item_revision=component,
            quantity=quantity,
            unit='EA',
            is_phantom=extra.get('phantom', False),
            scrap_percent=extra.get('scrap_percent', '0.000'),
        )
    return revision


def policy(item_revision, procurement_type, **attrs):
    defaults = {
        'planning_method': ItemPlanningPolicy.METHOD_MRP,
        'procurement_type': procurement_type,
        'safety_stock': '0.000000',
        'minimum_stock': '0.000000',
        'minimum_order_quantity': '0.000000',
        'maximum_order_quantity': '0.000000',
        'order_multiple': '1.000000',
        'fixed_lot_quantity': None,
    }
    defaults.update(attrs)
    return ItemPlanningPolicy.objects.create(item_revision=item_revision, **defaults)


def approved_demand(admin, item_revision, *, quantity='10.000000', required_date=date(2026, 9, 15), warehouse=None):
    if warehouse is None:
        warehouse, _location = make_location(f'P11{item_revision.item.item_code[-5:]}')
    demand = create_demand(
        actor=admin,
        demand_type=PlanningDemand.TYPE_FORECAST,
        item_revision=item_revision,
        warehouse=warehouse,
        quantity=quantity,
        unit='EA',
        required_date=required_date,
        priority=PlanningDemand.PRIORITY_NORMAL,
    )
    return transition_demand(demand, actor=admin, expected_version=0, action='approve')


def test_planning_demand_lifecycle_calendar_and_version_conflict():
    admin = make_company_admin()
    item = make_item_revision('P-P11-DEMAND', status_value=ItemRevision.STATUS_RELEASED)
    warehouse, _location = make_location('P11DM')
    calendar = PlanningCalendar.objects.create(code='CAL-P11', name='P11 Calendar', holidays=['2026-09-14'])
    policy(item, ItemPlanningPolicy.PROCUREMENT_BUY, supply_lead_days=2, order_multiple='5.000000', safety_stock='1.000000')

    demand = create_demand(actor=admin, demand_type=PlanningDemand.TYPE_FORECAST, item_revision=item, warehouse=warehouse, quantity='4.000000', unit='EA', required_date=date(2026, 9, 15))
    changed = update_demand(demand, actor=admin, expected_version=0, quantity='6.000000')

    assert calendar.working_weekdays == [0, 1, 2, 3, 4]
    assert changed.demand_version == 1
    with pytest.raises(Exception):
        update_demand(changed, actor=admin, expected_version=0, quantity='7.000000')

    approved = transition_demand(changed, actor=admin, expected_version=1, action='approve')
    assert approved.status == PlanningDemand.STATUS_APPROVED


def test_mrp_explodes_multilevel_phantom_and_nets_inventory_supply_lots_and_lead_time():
    admin = make_company_admin()
    parent = make_item_revision('P-P11-MAKE', status_value=ItemRevision.STATUS_RELEASED)
    phantom = make_item_revision('P-P11-PHANTOM', status_value=ItemRevision.STATUS_RELEASED)
    component = make_item_revision('P-P11-BUY', status_value=ItemRevision.STATUS_RELEASED, make_or_buy='BUY')
    warehouse, location = make_location('P11NET')

    make_mbom(parent, [(phantom, '2.000000', {'phantom': True})])
    make_mbom(phantom, [(component, '3.000000', {'scrap_percent': '10.000'})])
    policy(parent, ItemPlanningPolicy.PROCUREMENT_MAKE, supply_lead_days=3)
    policy(phantom, ItemPlanningPolicy.PROCUREMENT_PHANTOM)
    policy(component, ItemPlanningPolicy.PROCUREMENT_BUY, supply_lead_days=2, safety_stock='2.000000', lot_sizing_method=ItemPlanningPolicy.LOT_MULTIPLE, order_multiple='5.000000')
    receive_inventory(actor=admin, item_revision=component, quantity='5.000000', warehouse=warehouse, location=location, idempotency_key='p11-receipt')
    ScheduledSupply.objects.create(supply_number='SS-P11-000001', item_revision=component, quantity='4.000000', unit='EA', expected_date=date(2026, 9, 15), warehouse=warehouse, status=ScheduledSupply.STATUS_APPROVED, created_by=admin)
    approved_demand(admin, parent, quantity='2.000000', warehouse=warehouse)

    run = run_mrp(actor=admin, horizon_start=date(2026, 9, 1), horizon_end=date(2026, 9, 30), warehouse=warehouse)

    component_req = run.requirements.get(item_revision=component, bucket_date=date(2026, 9, 15))
    assert run.status == MRPRun.STATUS_COMPLETED
    assert run.input_checksum and run.result_checksum
    assert component_req.gross_requirement == Decimal('13.200000')
    assert component_req.scheduled_receipt == Decimal('4.000000')
    assert component_req.safety_stock == Decimal('2.000000')
    assert component_req.planned_receipt == Decimal('10.000000')
    assert component_req.planned_release_date == date(2026, 9, 11)
    assert run.recommendations.filter(item_revision=component, recommendation_type=MRPRecommendation.TYPE_PURCHASE_REQUISITION).exists()
    assert run.pegging.filter(bom_path__0__phantom=True).exists()

    snapshot = MRPDemandSnapshot.objects.get(mrp_run=run)
    with pytest.raises(Exception):
        snapshot.quantity = Decimal('99.000000')
        snapshot.save()


def test_recommendation_freshness_blocks_stale_approval_and_conversion_creates_drafts():
    admin = make_company_admin()
    item = make_item_revision('P-P11-BUYCONV', status_value=ItemRevision.STATUS_RELEASED, make_or_buy='BUY')
    policy(item, ItemPlanningPolicy.PROCUREMENT_BUY)
    approved_demand(admin, item, quantity='3.000000')
    run = run_mrp(actor=admin, horizon_start=date(2026, 9, 1), horizon_end=date(2026, 9, 30))
    rec = run.recommendations.get(item_revision=item)

    transition_recommendation(rec, actor=admin, expected_version=0, action='approve')
    rec.refresh_from_db()
    converted = convert_recommendation(rec, actor=admin, expected_version=1, idempotency_key='buy-conv')

    assert converted.status == MRPRecommendation.STATUS_CONVERTED
    assert PurchaseRequisition.objects.get(source_recommendation=converted).status == PurchaseRequisition.STATUS_DRAFT

    stale_item = make_item_revision('P-P11-STALE', status_value=ItemRevision.STATUS_RELEASED, make_or_buy='BUY')
    policy(stale_item, ItemPlanningPolicy.PROCUREMENT_BUY)
    approved_demand(admin, stale_item, quantity='2.000000')
    stale_run = run_mrp(actor=admin, horizon_start=date(2026, 9, 1), horizon_end=date(2026, 9, 30))
    stale_rec = stale_run.recommendations.get(item_revision=stale_item)
    create_demand(actor=admin, demand_type=PlanningDemand.TYPE_FORECAST, item_revision=stale_item, warehouse=stale_rec.warehouse, quantity='1.000000', unit='EA', required_date=date(2026, 9, 20))

    with pytest.raises(Exception):
        transition_recommendation(stale_rec, actor=admin, expected_version=0, action='approve')


def test_make_recommendation_conversion_creates_draft_production_order_from_released_sources():
    admin, diagram, _nodes, _edges, refs = released_source('P-P11-MAKECONV')
    policy(diagram.item_revision, ItemPlanningPolicy.PROCUREMENT_MAKE)
    approved_demand(admin, diagram.item_revision, quantity='2.000000')
    run = run_mrp(actor=admin, horizon_start=date(2026, 9, 1), horizon_end=date(2026, 9, 30))
    rec = run.recommendations.get(item_revision=diagram.item_revision)

    transition_recommendation(rec, actor=admin, expected_version=0, action='approve')
    rec.refresh_from_db()
    converted = convert_recommendation(rec, actor=admin, expected_version=1, idempotency_key='make-conv')

    order = ProductionOrder.objects.get(source_mrp_recommendations=converted)
    assert order.status == ProductionOrder.STATUS_DRAFT
    assert order.source_opc_diagram == diagram
    assert order.source_manufacturing_bom_revision == refs['mbom_revision']


def test_mrp_api_run_and_recommendation_actions():
    admin = make_company_admin()
    item = make_item_revision('P-P11-API', status_value=ItemRevision.STATUS_RELEASED, make_or_buy='BUY')
    policy(item, ItemPlanningPolicy.PROCUREMENT_BUY)
    warehouse, _location = make_location('P11API')
    client = api(admin)
    created = client.post(reverse('opc-planning-demand-list'), {'demand_type': PlanningDemand.TYPE_FORECAST, 'item_revision': str(item.pk), 'warehouse': str(warehouse.pk), 'quantity': '4.000000', 'unit': 'EA', 'required_date': '2026-09-15'}, format='json')
    assert created.status_code == status.HTTP_201_CREATED, created.data
    approved = client.post(reverse('opc-planning-demand-approve', kwargs={'pk': created.data['id']}), {'expected_version': 0}, format='json')
    assert approved.status_code == status.HTTP_200_OK, approved.data
    run = client.post(reverse('opc-mrp-run-list'), {'horizon_start': '2026-09-01', 'horizon_end': '2026-09-30'}, format='json')
    assert run.status_code == status.HTTP_201_CREATED, run.data
    rec = run.data['recommendations'][0]
    approved_rec = client.post(reverse('opc-mrp-recommendation-approve', kwargs={'pk': rec['id']}), {'expected_version': rec['recommendation_version']}, format='json')
    assert approved_rec.status_code == status.HTTP_200_OK, approved_rec.data
    converted = client.post(reverse('opc-mrp-recommendation-convert', kwargs={'pk': rec['id']}), {'expected_version': approved_rec.data['recommendation_version'], 'idempotency_key': 'api-convert'}, format='json')
    assert converted.status_code == status.HTTP_200_OK, converted.data
    assert converted.data['converted_purchase_requisition']


@pytest.mark.django_db(transaction=True)
def test_postgresql_concurrent_recommendation_conversion_is_single_writer():
    if connection.vendor != 'postgresql':
        pytest.skip('PostgreSQL row-lock concurrency test.')
    admin = make_company_admin()
    item = make_item_revision('P-P11-CONC', status_value=ItemRevision.STATUS_RELEASED, make_or_buy='BUY')
    policy(item, ItemPlanningPolicy.PROCUREMENT_BUY)
    approved_demand(admin, item, quantity='6.000000')
    run = run_mrp(actor=admin, horizon_start=date(2026, 9, 1), horizon_end=date(2026, 9, 30))
    rec = run.recommendations.get(item_revision=item)
    transition_recommendation(rec, actor=admin, expected_version=0, action='approve')
    rec.refresh_from_db()
    barrier = Barrier(2)
    results: list[str] = []

    def worker(key):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            convert_recommendation(MRPRecommendation.objects.get(pk=rec.pk), actor=admin.__class__.objects.get(pk=admin.pk), expected_version=1, idempotency_key=key)
            results.append('ok')
        except Exception:
            results.append('blocked')
        finally:
            close_old_connections()

    threads = [Thread(target=worker, args=(f'conc-{idx}',)) for idx in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    rec.refresh_from_db()
    assert sorted(results) == ['blocked', 'ok']
    assert rec.status == MRPRecommendation.STATUS_CONVERTED
    assert PurchaseRequisition.objects.filter(source_recommendation=rec).count() == 1
