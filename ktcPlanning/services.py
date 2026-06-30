from django.db.models import Min, Max
from .models import WBSNode,Task

def calc_wbs_node_date(node_id):
    try:
        node=WBSNode.objects.get(pk=node_id)
    except WBSNode.DoesNotExist:
        return None

    task_date=Task.objects.filter(WBSNode=node , deleted_in_revision__isnull=True).aggregate(
       earliest_start=Min('planned_start'),latest_end=Max('planned_end'))

    node_start=task_date['earliest_start']
    node_end=task_date['latest_end']

    child_nodes=WBSNode.objects.filter(parent=node)
    for child in child_nodes:
        calc_wbs_node_date(child.id)

        child_task_dates=Task.objects.filter(wbs_node__wbs_code__startswith=child.wbs_code,deleted_in_revision__isnull=True).aggregate(
            earliest_start=Min('planned_start'),
            latest_end=Max('planned_end')
        )
        child_start=child_task_dates['earliest_start']
        child_end=child_task_dates['latest_end']

        if child_start:
            if not node_start or child_start <node_start:
                node_start=child_start
        if child_end:
            if not node_end or child_end >node_end:
                node_end=child_end

    return {
        "wbs_node_id": node.id,
        "wbs_code": node.wbs_code,
        "calculated_start": node_start,
        "calculated_end": node_end,
    }

def trigger_global_wbs_rollup(project_id):
    root_node=WBSNode.objects.filter(project_id=project_id,parent__isnull=True)
    project_summery=[]
    for root in root_node:
        node_data=calc_wbs_node_date(root.id)
        project_summery.append(node_data)

    return project_summery
