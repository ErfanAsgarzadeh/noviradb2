import uuid

from django.conf import settings
from django.db import models, transaction


class CodingOrganization(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, unique=True)
    code = models.CharField(max_length=30, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class ItemType(models.Model):
    ITEM_GROUPS = [
        ('MATERIAL', 'Material'),
        ('PART', 'Part'),
        ('WIP', 'Work in process'),
        ('ASSEMBLY', 'Assembly'),
        ('TOOLING', 'Tooling'),
        ('ASSET', 'Asset'),
        ('CONSUMABLE', 'Consumable'),
        ('SERVICE', 'Service'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(CodingOrganization, on_delete=models.CASCADE, related_name='item_types')
    name = models.CharField(max_length=120)
    code = models.CharField(max_length=20)
    group = models.CharField(max_length=20, choices=ITEM_GROUPS)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['group', 'code']
        unique_together = [('organization', 'code')]

    def __str__(self):
        return f'{self.code} - {self.name}'


class ItemCategory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(CodingOrganization, on_delete=models.CASCADE, related_name='item_categories')
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.CASCADE, related_name='children')
    name = models.CharField(max_length=160)
    code = models.CharField(max_length=30)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['code']
        unique_together = [('organization', 'parent', 'code')]

    def __str__(self):
        return self.name


class CodeScheme(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(CodingOrganization, on_delete=models.CASCADE, related_name='code_schemes')
    name = models.CharField(max_length=160)
    item_type = models.ForeignKey(ItemType, null=True, blank=True, on_delete=models.SET_NULL, related_name='code_schemes')
    prefix = models.CharField(max_length=30)
    separator = models.CharField(max_length=4, default='-')
    sequence_padding = models.PositiveSmallIntegerField(default=5)
    next_sequence = models.PositiveIntegerField(default=1)
    include_category = models.BooleanField(default=True)
    include_revision = models.BooleanField(default=False)
    include_stage = models.BooleanField(default=False)
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ['name']
        unique_together = [('organization', 'name')]

    def preview(self, category=None, revision='', stage=''):
        parts = [self.prefix]
        if self.include_category and category:
            parts.append(category.code)
        parts.append(str(self.next_sequence).zfill(self.sequence_padding))
        if self.include_revision and revision:
            parts.append(revision)
        if self.include_stage and stage:
            parts.append(stage)
        return self.separator.join([part for part in parts if part])

    @transaction.atomic
    def generate_code(self, category=None, revision='', stage=''):
        scheme = CodeScheme.objects.select_for_update().get(pk=self.pk)
        parts = [scheme.prefix]
        if scheme.include_category and category:
            parts.append(category.code)
        parts.append(str(scheme.next_sequence).zfill(scheme.sequence_padding))
        if scheme.include_revision and revision:
            parts.append(revision)
        if scheme.include_stage and stage:
            parts.append(stage)
        scheme.next_sequence += 1
        scheme.save(update_fields=['next_sequence'])
        return scheme.separator.join([part for part in parts if part])

    def __str__(self):
        return self.name


class Item(models.Model):
    STATUS_CHOICES = [
        ('DRAFT', 'Draft'),
        ('ACTIVE', 'Active'),
        ('OBSOLETE', 'Obsolete'),
        ('BLOCKED', 'Blocked'),
    ]
    TRACKING_CHOICES = [
        ('NONE', 'None'),
        ('LOT', 'Lot'),
        ('SERIAL', 'Serial'),
        ('LOT_SERIAL', 'Lot and serial'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(CodingOrganization, on_delete=models.CASCADE, related_name='items')
    item_code = models.CharField(max_length=160, db_index=True)
    name = models.CharField(max_length=255)
    item_type = models.ForeignKey(ItemType, on_delete=models.PROTECT, related_name='items')
    category = models.ForeignKey(ItemCategory, null=True, blank=True, on_delete=models.SET_NULL, related_name='items')
    base_unit = models.CharField(max_length=30, default='EA')
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='DRAFT')
    tracking_mode = models.CharField(max_length=16, choices=TRACKING_CHOICES, default='NONE')
    drawing_no = models.CharField(max_length=120, blank=True)
    specification = models.TextField(blank=True)
    default_revision = models.CharField(max_length=30, default='R0')
    weight = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='created_enterprise_items',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['item_code']
        unique_together = [('organization', 'item_code')]
        indexes = [
            models.Index(fields=['organization', 'status']),
            models.Index(fields=['item_code']),
        ]

    def __str__(self):
        return f'{self.item_code} - {self.name}'


class ItemRevision(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name='revisions')
    revision = models.CharField(max_length=30)
    title = models.CharField(max_length=255, blank=True)
    drawing_no = models.CharField(max_length=120, blank=True)
    specification = models.TextField(blank=True)
    effective_from = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        unique_together = [('item', 'revision')]

    def __str__(self):
        return f'{self.item.item_code} / {self.revision}'


class ManufacturingVariant(models.Model):
    STATUS_CHOICES = [
        ('DRAFT', 'Draft'),
        ('APPROVED', 'Approved'),
        ('RETIRED', 'Retired'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name='manufacturing_variants')
    variant_code = models.CharField(max_length=40)
    name = models.CharField(max_length=180)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='DRAFT')
    is_default = models.BooleanField(default=False)
    opc_diagram = models.ForeignKey('opc.OPCDiagram', null=True, blank=True, on_delete=models.SET_NULL, related_name='manufacturing_variants')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['item__item_code', 'variant_code']
        unique_together = [('item', 'variant_code')]

    def __str__(self):
        return f'{self.item.item_code} / {self.variant_code}'


class VariantInput(models.Model):
    SOURCE_TYPES = [
        ('CASTING', 'Casting'),
        ('SHEET', 'Sheet'),
        ('BAR', 'Bar'),
        ('PIPE', 'Pipe'),
        ('FORGING', 'Forging'),
        ('BILLET', 'Billet'),
        ('PURCHASED_SEMI_FINISHED', 'Purchased semi-finished'),
        ('EXISTING_ITEM', 'Existing item'),
        ('FREE_SPEC', 'Free specification'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    variant = models.ForeignKey(ManufacturingVariant, on_delete=models.CASCADE, related_name='inputs')
    source_type = models.CharField(max_length=32, choices=SOURCE_TYPES)
    linked_item = models.ForeignKey(Item, null=True, blank=True, on_delete=models.PROTECT, related_name='used_as_variant_input')
    material_grade = models.CharField(max_length=120, blank=True)
    shape = models.CharField(max_length=120, blank=True)
    dimensions = models.CharField(max_length=180, blank=True)
    quantity = models.DecimalField(max_digits=14, decimal_places=3, default=1)
    unit = models.CharField(max_length=30, default='EA')
    description = models.TextField(blank=True)

    class Meta:
        ordering = ['source_type', 'material_grade']

    def __str__(self):
        return f'{self.variant} / {self.source_type}'


class ProcessStageCode(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    variant = models.ForeignKey(ManufacturingVariant, on_delete=models.CASCADE, related_name='stage_codes')
    stage_code = models.CharField(max_length=40)
    stage_name = models.CharField(max_length=180)
    output_item_code = models.CharField(max_length=180)
    sequence = models.PositiveIntegerField(default=1)
    creates_inventory_identity = models.BooleanField(default=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ['sequence']
        unique_together = [('variant', 'stage_code'), ('variant', 'output_item_code')]

    def __str__(self):
        return f'{self.variant} / {self.stage_code}'
