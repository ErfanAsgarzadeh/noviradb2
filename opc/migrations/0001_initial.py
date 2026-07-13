import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='OPCDiagram',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('title', models.CharField(max_length=255)),
                ('part_code', models.CharField(db_index=True, max_length=120)),
                ('part_name', models.CharField(blank=True, max_length=255)),
                ('revision', models.CharField(blank=True, max_length=50)),
                ('description', models.TextField(blank=True)),
                ('status', models.CharField(choices=[('DRAFT', 'Draft'), ('ACTIVE', 'Active'), ('ARCHIVED', 'Archived')], default='DRAFT', max_length=16)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_opc_diagrams', to=settings.AUTH_USER_MODEL)),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='updated_opc_diagrams', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-updated_at'],
            },
        ),
        migrations.CreateModel(
            name='OPCNode',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('node_type', models.CharField(choices=[('MATERIAL', 'Raw material'), ('OPERATION', 'Operation'), ('INSPECTION', 'Inspection'), ('STORAGE', 'Storage'), ('TRANSPORT', 'Transport'), ('DELAY', 'Delay'), ('OUTPUT', 'Final part')], max_length=24)),
                ('label', models.CharField(max_length=255)),
                ('part_code', models.CharField(blank=True, max_length=120)),
                ('process_code', models.CharField(blank=True, max_length=120)),
                ('station', models.CharField(blank=True, max_length=120)),
                ('description', models.TextField(blank=True)),
                ('sequence', models.PositiveIntegerField(default=1)),
                ('x', models.FloatField(default=120)),
                ('y', models.FloatField(default=120)),
                ('meta', models.JSONField(blank=True, default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('diagram', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='nodes', to='opc.opcdiagram')),
            ],
            options={
                'ordering': ['sequence', 'created_at'],
            },
        ),
        migrations.CreateModel(
            name='OPCEdge',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('edge_type', models.CharField(choices=[('FLOW', 'Process flow'), ('OPTIONAL', 'Optional flow'), ('REWORK', 'Rework')], default='FLOW', max_length=20)),
                ('label', models.CharField(blank=True, max_length=120)),
                ('sequence', models.PositiveIntegerField(default=1)),
                ('meta', models.JSONField(blank=True, default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('diagram', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='edges', to='opc.opcdiagram')),
                ('source', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='outgoing_edges', to='opc.opcnode')),
                ('target', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='incoming_edges', to='opc.opcnode')),
            ],
            options={
                'ordering': ['sequence', 'created_at'],
                'unique_together': {('diagram', 'source', 'target')},
            },
        ),
        migrations.AddIndex(
            model_name='opcdiagram',
            index=models.Index(fields=['part_code', 'status'], name='opc_opcdiag_part_co_dca32e_idx'),
        ),
        migrations.AddIndex(
            model_name='opcdiagram',
            index=models.Index(fields=['updated_at'], name='opc_opcdiag_updated_95a29f_idx'),
        ),
        migrations.AddIndex(
            model_name='opcnode',
            index=models.Index(fields=['diagram', 'sequence'], name='opc_opcnode_diagram_8193d1_idx'),
        ),
        migrations.AddIndex(
            model_name='opcnode',
            index=models.Index(fields=['node_type'], name='opc_opcnode_node_ty_1f8d92_idx'),
        ),
        migrations.AddIndex(
            model_name='opcedge',
            index=models.Index(fields=['diagram', 'sequence'], name='opc_opcedge_diagram_83770d_idx'),
        ),
    ]
