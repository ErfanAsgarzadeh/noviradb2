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

from opc.execution_services import command_execution, ensure_executions_for_order
from opc.inventory_services import receive_inventory
from opc.maintenance_services import (
    create_maintenance_request,
    create_work_order,
    generate_preventive_work_orders,
    issue_spare_part,
    record_checklist_result,
    record_meter_reading,
    reliability_metrics,
    return_spare_part,
    transition_work_order,
)
from opc.models import (
    AssetMeter,
    AssetMeterReading,
    CalendarException,
    MachineCapacity,
    MachineDowntimeEvent,
    MachineMaintenanceProfile,
    MaintenanceChecklistResult,
    MaintenanceEvent,
    MaintenanceRequest,
    MaintenanceSparePartRequirement,
    MaintenanceTaskChecklistItem,
    MaintenanceTaskTemplate,
    MaintenanceWorkOrder,
    PreventiveMaintenancePlan,
    ResourceCalendar,
    Shift,
)
from opc.production_services import generate_order_baseline, release_production_order
from tests.test_opc_graph_persistence_phase3 import api
from tests.test_opc_phase10_inventory import make_location
from tests.test_opc_phase7_production_orders import create_order, released_source
from tests.test_opc_phase8_execution import complete_execution


pytestmark = pytest.mark.django_db


def aware(value: datetime):
    return timezone.make_aware(value) if value.tzinfo is None else value


def maintenance_refs(code='P-P13'):
    admin, diagram, _nodes, _edges, refs = released_source(code)
    template, profile = configure_machine_for_maintenance(refs, code)
    return admin, refs, template, profile


def configure_machine_for_maintenance(refs, code='P-P13'):
    calendar = ResourceCalendar.objects.create(code=f'P13-MC-{code}', name='Machine maintenance calendar', calendar_type=ResourceCalendar.TYPE_MACHINE, timezone='UTC')
    for weekday in range(5):
        Shift.objects.create(resource_calendar=calendar, code=f'DAY-{code}-{weekday}', name='Day', weekday=weekday, local_start_time=datetime.min.time().replace(hour=8), local_end_time=datetime.min.time().replace(hour=16))
    MachineCapacity.objects.create(machine=refs['machine'], resource_calendar=calendar, exclusive_capacity=True, efficiency_factor='1.0000')
    template = MaintenanceTaskTemplate.objects.create(code=f'PM-{code}', name='Lubricate spindle', estimated_duration_hours='2.000')
    MaintenanceTaskChecklistItem.objects.create(template=template, sequence=1, label='Lockout complete', mandatory=True)
    profile = MachineMaintenanceProfile.objects.create(machine=refs['machine'], criticality='CRITICAL', operational_status='AVAILABLE', maintenance_location='Line 1')
    return template, profile


def released_execution_order(code='P-P13-EXEC'):
    admin, diagram, _nodes, _edges, refs = released_source(code)
    order = generate_order_baseline(create_order(admin, diagram, refs), actor=admin, expected_version=0)
    order = release_production_order(order, actor=admin, expected_version=1)
    ensure_executions_for_order(order, actor=admin)
    return admin, order, refs


def test_asset_profile_meter_append_only_pm_generation_and_downtime_calendar():
    admin, refs, template, profile = maintenance_refs('ASSET')
    meter = AssetMeter.objects.create(machine=refs['machine'], code='HOURS', name='Runtime hours', meter_type=AssetMeter.TYPE_RUNTIME_HOURS, unit='H')
    first = record_meter_reading(actor=admin, meter=meter, reading_value='100.000000', reading_timestamp=aware(datetime(2026, 9, 1, 8)), idempotency_key='hours-100')
    retry = record_meter_reading(actor=admin, meter=meter, reading_value='100.000000', reading_timestamp=aware(datetime(2026, 9, 1, 8)), idempotency_key='hours-100')
    assert retry.pk == first.pk
    with pytest.raises(Exception):
        record_meter_reading(actor=admin, meter=meter, reading_value='90.000000')
    with pytest.raises(ValidationError):
        first.notes = 'mutate'
        first.save()

    plan = PreventiveMaintenancePlan.objects.create(plan_number='PMPLAN-ASSET', name='Weekly lubrication', machine=refs['machine'], task_template=template, trigger_type=PreventiveMaintenancePlan.TRIGGER_CALENDAR, interval_days=7, next_due_date=date(2026, 9, 14), status=PreventiveMaintenancePlan.STATUS_ACTIVE, created_by=admin)
    created = generate_preventive_work_orders(actor=admin, horizon_date=date(2026, 9, 1))
    assert len(created) == 1
    work_order = created[0]
    work_order.refresh_from_db()
    assert work_order.checklist_results.count() == 1
    planned = transition_work_order(work_order, actor=admin, expected_version=work_order.work_order_version, action='plan')
    planned.refresh_from_db()
    assert planned.downtime_exception
    assert planned.downtime_exception.exception_type == CalendarException.TYPE_PLANNED_DOWNTIME
    plan.refresh_from_db()
    assert plan.next_due_date == date(2026, 9, 21)
    assert profile.criticality == 'CRITICAL'


def test_work_order_lifecycle_events_checklist_execution_block_and_reliability_metrics():
    admin, order, refs = released_execution_order('P-P13-LIFE')
    template, _profile = configure_machine_for_maintenance(refs, 'LIFE')
    material_execution, cut_execution, _output_execution = order.operation_executions.select_related('manufacturing_operation').order_by('manufacturing_operation__sequence')
    complete_execution(material_execution, admin)
    cut_execution.refresh_from_db()
    cut_execution, _event = command_execution(cut_execution, actor=admin, expected_version=cut_execution.execution_version, command='dispatch')
    cut_execution, _event = command_execution(cut_execution, actor=admin, expected_version=cut_execution.execution_version, command='assign_machine', machine=refs['machine'])

    request = create_maintenance_request(actor=admin, title='Spindle noise', machine=refs['machine'], priority=MaintenanceRequest.PRIORITY_HIGH, operation_execution=cut_execution)
    work_order = create_work_order(actor=admin, title='Inspect spindle', request=request, task_template=template, planned_start=aware(datetime(2026, 9, 14, 8)), planned_end=aware(datetime(2026, 9, 14, 10)))
    released = transition_work_order(work_order, actor=admin, expected_version=work_order.work_order_version, action='release')
    started = transition_work_order(released, actor=admin, expected_version=released.work_order_version, action='start')
    assert started.status == MaintenanceWorkOrder.STATUS_IN_PROGRESS
    assert MachineDowntimeEvent.objects.filter(machine=refs['machine'], end_datetime__isnull=True).exists()
    with pytest.raises(Exception):
        command_execution(cut_execution, actor=admin, expected_version=cut_execution.execution_version, command='start')

    checklist = started.checklist_results.get()
    record_checklist_result(checklist, actor=admin, status=MaintenanceChecklistResult.STATUS_PASS, expected_work_order_version=started.work_order_version)
    started.refresh_from_db()
    completed = transition_work_order(started, actor=admin, expected_version=started.work_order_version, action='complete')
    assert completed.status == MaintenanceWorkOrder.STATUS_COMPLETED
    assert MaintenanceEvent.objects.filter(work_order=completed, event_type=MaintenanceEvent.TYPE_COMPLETED).exists()
    metrics = reliability_metrics(refs['machine'])
    assert metrics['failure_count'] == 1
    assert Decimal(metrics['mttr_hours']) >= Decimal('0')


def test_spare_part_issue_return_and_api_conflict_payload():
    admin, refs, template, _profile = maintenance_refs('SPARE')
    warehouse, location = make_location('P13-SPARE')
    tx, balance = receive_inventory(actor=admin, item_revision=refs['component'], quantity='5.000000', warehouse=warehouse, location=location, idempotency_key='p13-spare-receipt')
    work_order = create_work_order(actor=admin, title='Replace belt', machine=refs['machine'], task_template=template)
    work_order = transition_work_order(work_order, actor=admin, expected_version=work_order.work_order_version, action='release')
    requirement = MaintenanceSparePartRequirement.objects.create(work_order=work_order, item_revision=refs['component'], quantity='2.000000', unit='EA')
    issue = issue_spare_part(actor=admin, requirement=requirement, balance=balance, quantity='1.000000', expected_balance_version=balance.balance_version, expected_requirement_version=0, idempotency_key='p13-issue')
    balance.refresh_from_db()
    assert issue.issue_transaction.transaction_type == 'ADJUSTMENT_OUT'
    assert balance.on_hand_quantity == Decimal('4.000000')
    returned = return_spare_part(actor=admin, issue=issue, quantity='0.500000', destination_warehouse=warehouse, destination_location=location, idempotency_key='p13-return')
    assert returned.return_transaction is not None

    client = api(admin)
    created = client.post(reverse('opc-maintenance-request-list'), {'title': 'API vibration', 'machine': str(refs['machine'].pk), 'priority': 'URGENT'}, format='json')
    assert created.status_code == status.HTTP_201_CREATED, created.data
    converted = client.post(reverse('opc-maintenance-request-convert', kwargs={'pk': created.data['id']}), {'task_template': str(template.pk), 'planned_start': aware(datetime(2026, 9, 15, 8)).isoformat(), 'planned_end': aware(datetime(2026, 9, 15, 10)).isoformat()}, format='json')
    assert converted.status_code == status.HTTP_201_CREATED, converted.data
    stale = client.post(reverse('opc-maintenance-work-order-release', kwargs={'pk': converted.data['id']}), {'expected_version': 99}, format='json')
    assert stale.status_code == status.HTTP_409_CONFLICT


@pytest.mark.django_db(transaction=True)
def test_postgresql_concurrent_work_order_completion_is_single_writer():
    if connection.vendor != 'postgresql':
        pytest.skip('PostgreSQL row-lock concurrency test.')
    admin, refs, template, _profile = maintenance_refs('CONC')
    work_order = create_work_order(actor=admin, title='Concurrent repair', machine=refs['machine'], task_template=template)
    work_order = transition_work_order(work_order, actor=admin, expected_version=work_order.work_order_version, action='release')
    work_order = transition_work_order(work_order, actor=admin, expected_version=work_order.work_order_version, action='start')
    checklist = work_order.checklist_results.get()
    record_checklist_result(checklist, actor=admin, status=MaintenanceChecklistResult.STATUS_PASS, expected_work_order_version=work_order.work_order_version)
    work_order.refresh_from_db()
    barrier = Barrier(2)
    results: list[str] = []

    def worker():
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            transition_work_order(MaintenanceWorkOrder.objects.get(pk=work_order.pk), actor=admin.__class__.objects.get(pk=admin.pk), expected_version=work_order.work_order_version, action='complete')
            results.append('ok')
        except Exception:
            results.append('blocked')
        finally:
            close_old_connections()

    threads = [Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    assert sorted(results) == ['blocked', 'ok']
