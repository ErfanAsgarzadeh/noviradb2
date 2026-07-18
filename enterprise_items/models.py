import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone


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


class ItemClassification(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(CodingOrganization, on_delete=models.CASCADE, related_name='item_classifications')
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT, related_name='children')
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=180)
    description = models.TextField(blank=True)
    path = models.CharField(max_length=700, blank=True, db_index=True)
    coding_prefix = models.CharField(max_length=40, blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='created_item_classifications')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['path', 'sort_order', 'code']
        unique_together = [('organization', 'parent', 'code')]
        indexes = [models.Index(fields=['organization', 'parent']), models.Index(fields=['organization', 'is_active'])]

    def clean(self):
        super().clean()
        if self.code:
            self.code = self.code.strip().upper()
        if self.coding_prefix:
            self.coding_prefix = self.coding_prefix.strip().upper()
        if self.parent_id:
            if self.pk and self.parent_id == self.pk:
                raise ValidationError({'parent': 'Classification cannot be its own parent.'})
            if self.parent.organization_id != self.organization_id:
                raise ValidationError({'parent': 'Parent classification must belong to the same organization.'})
            ancestor = self.parent
            seen = set()
            while ancestor:
                if ancestor.pk in seen or (self.pk and ancestor.pk == self.pk):
                    raise ValidationError({'parent': 'Circular classification hierarchy is not allowed.'})
                seen.add(ancestor.pk)
                ancestor = ancestor.parent

    def save(self, *args, **kwargs):
        self.full_clean()
        parent_path = self.parent.path if self.parent_id and self.parent.path else ''
        self.path = f'{parent_path}/{self.code}' if parent_path else self.code
        super().save(*args, **kwargs)

    def __str__(self):
        return self.path or self.code


class AttributeDefinition(models.Model):
    TYPE_TEXT = 'TEXT'
    TYPE_DECIMAL = 'DECIMAL'
    TYPE_INTEGER = 'INTEGER'
    TYPE_BOOLEAN = 'BOOLEAN'
    TYPE_DATE = 'DATE'
    TYPE_CHOICE = 'CHOICE'
    DATA_TYPE_CHOICES = [
        (TYPE_TEXT, 'Text'), (TYPE_DECIMAL, 'Decimal'), (TYPE_INTEGER, 'Integer'),
        (TYPE_BOOLEAN, 'Boolean'), (TYPE_DATE, 'Date'), (TYPE_CHOICE, 'Choice'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(CodingOrganization, on_delete=models.CASCADE, related_name='attribute_definitions')
    code = models.CharField(max_length=60)
    name = models.CharField(max_length=180)
    description = models.TextField(blank=True)
    data_type = models.CharField(max_length=20, choices=DATA_TYPE_CHOICES)
    unit_dimension = models.CharField(max_length=40, blank=True)
    default_unit = models.CharField(max_length=30, blank=True)
    is_required = models.BooleanField(default=False)
    is_searchable = models.BooleanField(default=True)
    is_identity_defining = models.BooleanField(default=False)
    is_code_bearing = models.BooleanField(default=False)
    is_duplicate_key = models.BooleanField(default=False)
    is_revision_controlled = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    validation_metadata = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='created_attribute_definitions')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['sort_order', 'code']
        unique_together = [('organization', 'code')]
        indexes = [models.Index(fields=['organization', 'is_active']), models.Index(fields=['organization', 'data_type'])]

    def clean(self):
        super().clean()
        if self.code:
            self.code = self.code.strip().upper()
        if self.default_unit:
            self.default_unit = self.default_unit.strip().upper()
        if self.data_type == self.TYPE_CHOICE:
            choices = self.validation_metadata.get('choices') if isinstance(self.validation_metadata, dict) else None
            if not isinstance(choices, list) or not choices:
                raise ValidationError({'validation_metadata': 'Choice attributes require a non-empty choices list.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.code} - {self.name}'


class ClassificationAttribute(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    classification = models.ForeignKey(ItemClassification, on_delete=models.CASCADE, related_name='attribute_assignments')
    attribute_definition = models.ForeignKey(AttributeDefinition, on_delete=models.PROTECT, related_name='classification_assignments')
    required_override = models.BooleanField(null=True, blank=True)
    code_bearing_override = models.BooleanField(null=True, blank=True)
    identity_defining_override = models.BooleanField(null=True, blank=True)
    duplicate_key_override = models.BooleanField(null=True, blank=True)
    default_unit = models.CharField(max_length=30, blank=True)
    display_order = models.PositiveIntegerField(default=0)
    inherited = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='created_classification_attributes')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['display_order', 'attribute_definition__code']
        unique_together = [('classification', 'attribute_definition')]
        indexes = [models.Index(fields=['classification', 'is_active'])]

    def clean(self):
        super().clean()
        if self.classification_id and self.attribute_definition_id:
            if self.classification.organization_id != self.attribute_definition.organization_id:
                raise ValidationError({'attribute_definition': 'Attribute must belong to the same organization as classification.'})
        if self.default_unit:
            self.default_unit = self.default_unit.strip().upper()

    @property
    def effective_required(self):
        return self.attribute_definition.is_required if self.required_override is None else self.required_override

    @property
    def effective_code_bearing(self):
        return self.attribute_definition.is_code_bearing if self.code_bearing_override is None else self.code_bearing_override

    @property
    def effective_identity_defining(self):
        return self.attribute_definition.is_identity_defining if self.identity_defining_override is None else self.identity_defining_override

    @property
    def effective_duplicate_key(self):
        return self.attribute_definition.is_duplicate_key if self.duplicate_key_override is None else self.duplicate_key_override

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.classification} / {self.attribute_definition.code}'


class Item(models.Model):
    STATUS_CHOICES = [
        ('DRAFT', 'Draft'),
        ('ACTIVE', 'Active'),
        ('OBSOLETE', 'Obsolete'),
        ('BLOCKED', 'Blocked'),
        ('PHASE_OUT', 'Phase out'),
        ('MERGED', 'Merged'),
    ]
    TRACKING_CHOICES = [
        ('NONE', 'None'),
        ('LOT', 'Lot / batch'),
        ('BATCH', 'Batch'),
        ('SERIAL', 'Serial'),
        ('LOT_SERIAL', 'Lot and serial'),
        ('BATCH_AND_SERIAL', 'Batch and serial'),
    ]
    MAKE_OR_BUY_CHOICES = [
        ('MAKE', 'Make'),
        ('BUY', 'Buy'),
        ('MAKE_OR_BUY', 'Make or buy'),
        ('OUTSOURCE', 'Outsource'),
        ('PHANTOM', 'Phantom'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(CodingOrganization, on_delete=models.CASCADE, related_name='items')
    item_code = models.CharField(max_length=160, db_index=True)
    name = models.CharField(max_length=255)
    item_type = models.ForeignKey(ItemType, on_delete=models.PROTECT, related_name='items')
    category = models.ForeignKey(ItemCategory, null=True, blank=True, on_delete=models.SET_NULL, related_name='items')
    classification = models.ForeignKey(ItemClassification, null=True, blank=True, on_delete=models.PROTECT, related_name='items')
    base_unit = models.CharField(max_length=30, default='EA')
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='DRAFT')
    tracking_mode = models.CharField(max_length=16, choices=TRACKING_CHOICES, default='NONE')
    make_or_buy = models.CharField(max_length=16, choices=MAKE_OR_BUY_CHOICES, default='MAKE')
    is_active = models.BooleanField(default=True)
    drawing_no = models.CharField(max_length=120, blank=True)
    specification = models.TextField(blank=True)
    default_revision = models.CharField(max_length=30, default='R0')
    weight = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    semantic_identity_payload = models.JSONField(default=dict, blank=True)
    semantic_identity_hash = models.CharField(max_length=64, blank=True, db_index=True)
    coding_scheme = models.ForeignKey('ItemCodingScheme', null=True, blank=True, on_delete=models.SET_NULL, related_name='items')
    coding_template = models.ForeignKey('ItemCodingTemplate', null=True, blank=True, on_delete=models.SET_NULL, related_name='items')
    code_generation_strategy = models.CharField(max_length=24, blank=True, default='')
    generated_code_locked = models.BooleanField(default=False)
    replaced_by_item = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='replaces_items')
    phase_out_date = models.DateField(null=True, blank=True)
    block_reason = models.TextField(blank=True)
    status_reason = models.TextField(blank=True)
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
            models.Index(fields=['organization', 'semantic_identity_hash']),
            models.Index(fields=['organization', 'classification']),
        ]

    def clean(self):
        super().clean()
        if self.item_code:
            normalized = self.item_code.strip()
            if not normalized:
                raise ValidationError({'item_code': 'Item code is required.'})
            queryset = Item.objects.filter(organization_id=self.organization_id, item_code__iexact=normalized)
            if self.pk:
                queryset = queryset.exclude(pk=self.pk)
            if queryset.exists():
                raise ValidationError({'item_code': 'Item code must be unique within the organization.'})
            self.item_code = normalized
        if self.base_unit:
            self.base_unit = self.base_unit.strip().upper()
        if self.classification_id and self.classification.organization_id != self.organization_id:
            raise ValidationError({'classification': 'Classification must belong to the same organization as item.'})
        if self.status == 'MERGED' and not self.replaced_by_item_id:
            raise ValidationError({'replaced_by_item': 'Merged items require a replacement item.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.item_code} - {self.name}'


class ItemRevision(models.Model):
    STATUS_DRAFT = 'DRAFT'
    STATUS_UNDER_REVIEW = 'UNDER_REVIEW'
    STATUS_APPROVED = 'APPROVED'
    STATUS_RELEASED = 'RELEASED'
    STATUS_SUPERSEDED = 'SUPERSEDED'
    STATUS_OBSOLETE = 'OBSOLETE'
    LOCKED_STATUSES = {STATUS_RELEASED, STATUS_SUPERSEDED, STATUS_OBSOLETE}
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Draft'),
        (STATUS_UNDER_REVIEW, 'Under review'),
        (STATUS_APPROVED, 'Approved'),
        (STATUS_RELEASED, 'Released'),
        (STATUS_SUPERSEDED, 'Superseded'),
        (STATUS_OBSOLETE, 'Obsolete'),
    ]
    TECHNICAL_FIELDS = {
        'item', 'revision', 'title', 'description', 'drawing_no', 'specification',
        'material_specification', 'technical_notes', 'effective_from', 'effective_to',
        'weight', 'weight_unit', 'dimensions', 'is_active',
    }

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name='revisions')
    revision = models.CharField(max_length=30)
    title = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)
    drawing_no = models.CharField(max_length=120, blank=True)
    specification = models.TextField(blank=True)
    material_specification = models.TextField(blank=True)
    technical_notes = models.TextField(blank=True)
    effective_from = models.DateField(null=True, blank=True)
    effective_to = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    is_active = models.BooleanField(default=True)
    weight = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    weight_unit = models.CharField(max_length=16, blank=True, default='')
    dimensions = models.CharField(max_length=180, blank=True, default='')
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='submitted_item_revisions',
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='approved_item_revisions',
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    released_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='released_item_revisions',
    )
    released_at = models.DateTimeField(null=True, blank=True)
    superseded_at = models.DateTimeField(null=True, blank=True)
    superseded_by = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL, related_name='supersedes',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        unique_together = [('item', 'revision')]
        indexes = [
            models.Index(fields=['item', 'status']),
            models.Index(fields=['effective_from', 'effective_to']),
        ]

    def clean(self):
        super().clean()
        if self.revision:
            normalized = self.revision.strip()
            if not normalized:
                raise ValidationError({'revision': 'Revision code is required.'})
            queryset = ItemRevision.objects.filter(item_id=self.item_id, revision__iexact=normalized)
            if self.pk:
                queryset = queryset.exclude(pk=self.pk)
            if queryset.exists():
                raise ValidationError({'revision': 'Revision code must be unique for this item.'})
            self.revision = normalized
        if self.effective_from and self.effective_to and self.effective_to < self.effective_from:
            raise ValidationError({'effective_to': 'Effective end date cannot precede effective start date.'})
        if self.weight is not None and self.weight < 0:
            raise ValidationError({'weight': 'Weight cannot be negative.'})
        if self.weight_unit:
            self.weight_unit = self.weight_unit.strip().upper()

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.item.item_code} / {self.revision}'


class ItemRevisionAttributeValue(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    item_revision = models.ForeignKey(ItemRevision, on_delete=models.CASCADE, related_name='attribute_values')
    attribute_definition = models.ForeignKey(AttributeDefinition, on_delete=models.PROTECT, related_name='revision_values')
    value_text = models.TextField(blank=True)
    value_decimal = models.DecimalField(max_digits=24, decimal_places=8, null=True, blank=True)
    value_integer = models.BigIntegerField(null=True, blank=True)
    value_boolean = models.BooleanField(null=True, blank=True)
    value_date = models.DateField(null=True, blank=True)
    value_choice = models.CharField(max_length=120, blank=True)
    unit = models.CharField(max_length=30, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='created_item_revision_attribute_values')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['attribute_definition__sort_order', 'attribute_definition__code']
        unique_together = [('item_revision', 'attribute_definition')]
        indexes = [models.Index(fields=['item_revision', 'attribute_definition'])]

    def _populated_value_fields(self):
        fields = []
        for field in ('value_text', 'value_decimal', 'value_integer', 'value_boolean', 'value_date', 'value_choice'):
            value = getattr(self, field)
            if value not in (None, ''):
                fields.append(field)
        return fields

    def clean(self):
        super().clean()
        if self.item_revision_id and self.item_revision.status in ItemRevision.LOCKED_STATUSES:
            raise ValidationError({'item_revision': 'Released engineering revisions are immutable.'})
        if self.item_revision_id and self.attribute_definition_id:
            item = self.item_revision.item
            if item.classification_id:
                allowed = get_effective_classification_attributes(item.classification).filter(attribute_definition=self.attribute_definition, is_active=True).exists()
                if not allowed:
                    raise ValidationError({'attribute_definition': 'Attribute is not assigned to this item classification.'})
            if item.organization_id != self.attribute_definition.organization_id:
                raise ValidationError({'attribute_definition': 'Attribute must belong to the same organization as the item.'})
        populated = self._populated_value_fields()
        if len(populated) != 1:
            raise ValidationError({'value': 'Exactly one typed value field must be populated.'})
        expected = {
            AttributeDefinition.TYPE_TEXT: 'value_text', AttributeDefinition.TYPE_DECIMAL: 'value_decimal',
            AttributeDefinition.TYPE_INTEGER: 'value_integer', AttributeDefinition.TYPE_BOOLEAN: 'value_boolean',
            AttributeDefinition.TYPE_DATE: 'value_date', AttributeDefinition.TYPE_CHOICE: 'value_choice',
        }.get(self.attribute_definition.data_type if self.attribute_definition_id else None)
        if expected and populated[0] != expected:
            raise ValidationError({populated[0]: f'Use {expected} for {self.attribute_definition.data_type} attributes.'})
        if self.attribute_definition_id and self.attribute_definition.data_type == AttributeDefinition.TYPE_CHOICE:
            choices = [str(choice).strip().upper() for choice in self.attribute_definition.validation_metadata.get('choices', [])]
            normalized = self.value_choice.strip().upper()
            if normalized not in choices:
                raise ValidationError({'value_choice': 'Value is not one of the controlled choices.'})
            self.value_choice = normalized
        if self.unit:
            self.unit = self.unit.strip().upper()
        elif self.attribute_definition_id and self.attribute_definition.default_unit:
            self.unit = self.attribute_definition.default_unit

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def typed_value(self):
        field = self._populated_value_fields()[0]
        return getattr(self, field)

    def __str__(self):
        return f'{self.item_revision} / {self.attribute_definition.code}'


class ItemCodingScheme(models.Model):
    STRATEGY_SEMANTIC = 'SEMANTIC'
    STRATEGY_HYBRID = 'HYBRID'
    STRATEGY_SEQUENTIAL = 'SEQUENTIAL'
    STRATEGY_MANUAL_CONTROLLED = 'MANUAL_CONTROLLED'
    STRATEGY_LEGACY = 'LEGACY'
    STRATEGY_CHOICES = [
        (STRATEGY_SEMANTIC, 'Semantic'), (STRATEGY_HYBRID, 'Hybrid'), (STRATEGY_SEQUENTIAL, 'Sequential'),
        (STRATEGY_MANUAL_CONTROLLED, 'Manual controlled'), (STRATEGY_LEGACY, 'Legacy'),
    ]
    CASE_UPPER = 'UPPER'
    CASE_LOWER = 'LOWER'
    CASE_PRESERVE = 'PRESERVE'
    CASE_CHOICES = [(CASE_UPPER, 'Uppercase'), (CASE_LOWER, 'Lowercase'), (CASE_PRESERVE, 'Preserve')]
    SCOPE_GLOBAL = 'GLOBAL'
    SCOPE_COMPANY = 'COMPANY'
    SCOPE_DOMAIN = 'DOMAIN'
    SCOPE_CLASSIFICATION = 'CLASSIFICATION'
    SEQUENCE_SCOPE_CHOICES = [(SCOPE_GLOBAL, 'Global'), (SCOPE_COMPANY, 'Company'), (SCOPE_DOMAIN, 'Domain'), (SCOPE_CLASSIFICATION, 'Classification')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(CodingOrganization, on_delete=models.CASCADE, related_name='item_coding_schemes')
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=180)
    strategy = models.CharField(max_length=24, choices=STRATEGY_CHOICES)
    separator = models.CharField(max_length=4, default='-')
    maximum_length = models.PositiveSmallIntegerField(default=80)
    case_policy = models.CharField(max_length=12, choices=CASE_CHOICES, default=CASE_UPPER)
    sequence_scope = models.CharField(max_length=24, choices=SEQUENCE_SCOPE_CHOICES, default=SCOPE_GLOBAL)
    sequence_length = models.PositiveSmallIntegerField(default=6)
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    description = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='created_item_coding_schemes')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['code']
        unique_together = [('organization', 'code')]
        indexes = [models.Index(fields=['organization', 'strategy']), models.Index(fields=['organization', 'is_default'])]

    def clean(self):
        super().clean()
        if self.code:
            self.code = self.code.strip().upper()
        if not self.separator or any(ch.isspace() for ch in self.separator):
            raise ValidationError({'separator': 'Separator must be a non-space safe character.'})
        if self.sequence_length < 1 or self.sequence_length > 12:
            raise ValidationError({'sequence_length': 'Sequence length must be between 1 and 12.'})
        if self.maximum_length < 8:
            raise ValidationError({'maximum_length': 'Maximum length is too short for industrial part numbers.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.code} - {self.name}'


class ItemCodingTemplate(models.Model):
    STATUS_DRAFT = 'DRAFT'
    STATUS_ACTIVE = 'ACTIVE'
    STATUS_RETIRED = 'RETIRED'
    STATUS_CHOICES = [(STATUS_DRAFT, 'Draft'), (STATUS_ACTIVE, 'Active'), (STATUS_RETIRED, 'Retired')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    coding_scheme = models.ForeignKey(ItemCodingScheme, on_delete=models.PROTECT, related_name='templates')
    classification = models.ForeignKey(ItemClassification, on_delete=models.PROTECT, related_name='coding_templates')
    version = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    effective_from = models.DateField(null=True, blank=True)
    effective_to = models.DateField(null=True, blank=True)
    fallback_sequence_enabled = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    description = models.TextField(blank=True)
    activated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='activated_item_coding_templates')
    activated_at = models.DateTimeField(null=True, blank=True)
    retired_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='retired_item_coding_templates')
    retired_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='created_item_coding_templates')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['classification__path', '-version']
        unique_together = [('coding_scheme', 'classification', 'version')]
        indexes = [models.Index(fields=['classification', 'status']), models.Index(fields=['effective_from', 'effective_to'])]

    def clean(self):
        super().clean()
        if self.coding_scheme_id and self.classification_id:
            if self.coding_scheme.organization_id != self.classification.organization_id:
                raise ValidationError({'classification': 'Classification must belong to the same organization as the coding scheme.'})
        if self.effective_from and self.effective_to and self.effective_to < self.effective_from:
            raise ValidationError({'effective_to': 'Effective end date cannot precede effective start date.'})

    def save(self, *args, **kwargs):
        if self.pk:
            old = ItemCodingTemplate.objects.filter(pk=self.pk).first()
            if old and old.status == self.STATUS_ACTIVE:
                protected = {'coding_scheme_id', 'classification_id', 'version', 'effective_from', 'effective_to', 'fallback_sequence_enabled'}
                if any(getattr(old, field) != getattr(self, field) for field in protected):
                    raise ValidationError('Active coding templates cannot be edited in ways that change generated output. Clone a new version.')
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.classification} / {self.coding_scheme.code} v{self.version}'


class AttributeEncodingRule(models.Model):
    TYPE_LOOKUP = 'LOOKUP'
    TYPE_NUMERIC = 'NUMERIC'
    TYPE_NORMALIZED_TEXT = 'NORMALIZED_TEXT'
    TYPE_BOOLEAN_TOKEN = 'BOOLEAN_TOKEN'
    TYPE_DATE_PATTERN = 'DATE_PATTERN'
    TYPE_REFERENCE_CODE = 'REFERENCE_CODE'
    ENCODING_TYPE_CHOICES = [
        (TYPE_LOOKUP, 'Lookup'), (TYPE_NUMERIC, 'Numeric'), (TYPE_NORMALIZED_TEXT, 'Normalized text'),
        (TYPE_BOOLEAN_TOKEN, 'Boolean token'), (TYPE_DATE_PATTERN, 'Date pattern'), (TYPE_REFERENCE_CODE, 'Reference code'),
    ]
    ROUND_REJECT = 'REJECT'
    ROUND_HALF_UP = 'HALF_UP'
    ROUND_DOWN = 'DOWN'
    ROUNDING_CHOICES = [(ROUND_REJECT, 'Reject non-exact'), (ROUND_HALF_UP, 'Half up'), (ROUND_DOWN, 'Down')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(CodingOrganization, on_delete=models.CASCADE, related_name='attribute_encoding_rules')
    code = models.CharField(max_length=60)
    name = models.CharField(max_length=180)
    encoding_type = models.CharField(max_length=24, choices=ENCODING_TYPE_CHOICES)
    canonical_unit = models.CharField(max_length=30, blank=True)
    multiplier = models.DecimalField(max_digits=18, decimal_places=6, default=1)
    precision = models.PositiveSmallIntegerField(default=0)
    rounding_policy = models.CharField(max_length=16, choices=ROUNDING_CHOICES, default=ROUND_REJECT)
    zero_pad_width = models.PositiveSmallIntegerField(default=0)
    text_case = models.CharField(max_length=12, choices=ItemCodingScheme.CASE_CHOICES, default=ItemCodingScheme.CASE_UPPER)
    max_length = models.PositiveSmallIntegerField(default=30)
    true_token = models.CharField(max_length=20, default='Y')
    false_token = models.CharField(max_length=20, default='N')
    date_pattern = models.CharField(max_length=20, blank=True)
    prefix = models.CharField(max_length=20, blank=True)
    suffix = models.CharField(max_length=20, blank=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='created_attribute_encoding_rules')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['code']
        unique_together = [('organization', 'code')]
        indexes = [models.Index(fields=['organization', 'encoding_type'])]

    def clean(self):
        super().clean()
        if self.code:
            self.code = self.code.strip().upper()
        if self.canonical_unit:
            self.canonical_unit = self.canonical_unit.strip().upper()
        if self.multiplier <= 0:
            raise ValidationError({'multiplier': 'Multiplier must be greater than zero.'})
        if self.encoding_type == self.TYPE_DATE_PATTERN and self.date_pattern not in {'YYYYMMDD', 'YYYYMM', 'YYMM'}:
            raise ValidationError({'date_pattern': 'Unsupported date pattern.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.code} - {self.name}'


class AttributeEncodingOption(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    rule = models.ForeignKey(AttributeEncodingRule, on_delete=models.CASCADE, related_name='options')
    source_value = models.CharField(max_length=180)
    encoded_value = models.CharField(max_length=60)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['sort_order', 'source_value']
        unique_together = [('rule', 'source_value')]

    def clean(self):
        super().clean()
        self.source_value = self.source_value.strip().upper()
        self.encoded_value = self.encoded_value.strip().upper()
        if self.rule_id and self.rule.encoding_type != AttributeEncodingRule.TYPE_LOOKUP:
            raise ValidationError({'rule': 'Encoding options are only valid for lookup rules.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class ItemCodingTemplateSegment(models.Model):
    TYPE_LITERAL = 'LITERAL'
    TYPE_CLASSIFICATION_CODE = 'CLASSIFICATION_CODE'
    TYPE_ATTRIBUTE = 'ATTRIBUTE'
    TYPE_SEQUENCE = 'SEQUENCE'
    TYPE_CHECK_DIGIT = 'CHECK_DIGIT'
    SEGMENT_TYPE_CHOICES = [
        (TYPE_LITERAL, 'Literal'), (TYPE_CLASSIFICATION_CODE, 'Classification code'), (TYPE_ATTRIBUTE, 'Attribute'),
        (TYPE_SEQUENCE, 'Sequence'), (TYPE_CHECK_DIGIT, 'Check digit'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    template = models.ForeignKey(ItemCodingTemplate, on_delete=models.CASCADE, related_name='segments')
    position = models.PositiveSmallIntegerField()
    segment_type = models.CharField(max_length=32, choices=SEGMENT_TYPE_CHOICES)
    literal_value = models.CharField(max_length=80, blank=True)
    classification_level = models.PositiveSmallIntegerField(null=True, blank=True)
    attribute_definition = models.ForeignKey(AttributeDefinition, null=True, blank=True, on_delete=models.PROTECT, related_name='coding_segments')
    encoding_rule = models.ForeignKey(AttributeEncodingRule, null=True, blank=True, on_delete=models.PROTECT, related_name='coding_segments')
    width = models.PositiveSmallIntegerField(null=True, blank=True)
    required = models.BooleanField(default=True)
    fallback_value = models.CharField(max_length=80, blank=True)
    prefix = models.CharField(max_length=20, blank=True)
    suffix = models.CharField(max_length=20, blank=True)

    class Meta:
        ordering = ['position']
        unique_together = [('template', 'position')]
        indexes = [models.Index(fields=['template', 'segment_type'])]

    def clean(self):
        super().clean()
        if self.template_id and self.template.status == ItemCodingTemplate.STATUS_ACTIVE and self.pk:
            raise ValidationError('Active coding template segments cannot be edited. Clone a new template version.')
        if self.segment_type == self.TYPE_LITERAL and not self.literal_value.strip():
            raise ValidationError({'literal_value': 'Literal segments require a literal value.'})
        if self.segment_type == self.TYPE_ATTRIBUTE:
            if not self.attribute_definition_id:
                raise ValidationError({'attribute_definition': 'Attribute segments require an attribute definition.'})
            if self.attribute_definition.organization_id != self.template.coding_scheme.organization_id:
                raise ValidationError({'attribute_definition': 'Attribute belongs to a different organization.'})
            allowed = get_effective_classification_attributes(self.template.classification).filter(attribute_definition=self.attribute_definition, is_active=True).exists()
            if not allowed:
                raise ValidationError({'attribute_definition': 'Attribute is not effective for this template classification.'})
        if self.segment_type == self.TYPE_SEQUENCE and (not self.width or self.width < 1 or self.width > 12):
            raise ValidationError({'width': 'Sequence segments require a width between 1 and 12.'})
        if self.segment_type == self.TYPE_CHECK_DIGIT:
            raise ValidationError({'segment_type': 'CHECK_DIGIT is reserved but not supported yet.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.template} / {self.position}'


class ItemCodeSequence(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    coding_scheme = models.ForeignKey(ItemCodingScheme, on_delete=models.CASCADE, related_name='sequences')
    scope_key = models.CharField(max_length=180)
    next_sequence = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('coding_scheme', 'scope_key')]
        indexes = [models.Index(fields=['coding_scheme', 'scope_key'])]


class ItemIdentifier(models.Model):
    TYPE_LEGACY_CODE = 'LEGACY_CODE'
    TYPE_MANUFACTURER_PART_NUMBER = 'MANUFACTURER_PART_NUMBER'
    TYPE_SUPPLIER_PART_NUMBER = 'SUPPLIER_PART_NUMBER'
    TYPE_CUSTOMER_PART_NUMBER = 'CUSTOMER_PART_NUMBER'
    TYPE_DRAWING_NUMBER = 'DRAWING_NUMBER'
    TYPE_BARCODE = 'BARCODE'
    IDENTIFIER_TYPE_CHOICES = [
        (TYPE_LEGACY_CODE, 'Legacy code'), (TYPE_MANUFACTURER_PART_NUMBER, 'Manufacturer part number'),
        (TYPE_SUPPLIER_PART_NUMBER, 'Supplier part number'), (TYPE_CUSTOMER_PART_NUMBER, 'Customer part number'),
        (TYPE_DRAWING_NUMBER, 'Drawing number'), (TYPE_BARCODE, 'Barcode'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name='identifiers')
    identifier_type = models.CharField(max_length=40, choices=IDENTIFIER_TYPE_CHOICES)
    value = models.CharField(max_length=180)
    normalized_value = models.CharField(max_length=180, blank=True, default='', db_index=True)
    organization_name = models.CharField(max_length=180, blank=True)
    is_primary = models.BooleanField(default=False)
    is_verified = models.BooleanField(default=False)
    effective_from = models.DateField(null=True, blank=True)
    effective_to = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='created_item_identifiers')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['identifier_type', 'normalized_value']
        unique_together = [('item', 'identifier_type', 'normalized_value', 'organization_name')]
        indexes = [models.Index(fields=['identifier_type', 'normalized_value']), models.Index(fields=['item', 'is_primary'])]

    def clean(self):
        super().clean()
        self.normalized_value = normalize_identifier(self.value, self.identifier_type)
        if self.effective_from and self.effective_to and self.effective_to < self.effective_from:
            raise ValidationError({'effective_to': 'Effective end date cannot precede effective start date.'})
        if self.is_primary:
            qs = ItemIdentifier.objects.filter(item=self.item, identifier_type=self.identifier_type, is_primary=True)
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            if qs.exists():
                raise ValidationError({'is_primary': 'Only one primary identifier of a type is allowed per item.'})
        if self.identifier_type in {self.TYPE_MANUFACTURER_PART_NUMBER, self.TYPE_DRAWING_NUMBER, self.TYPE_BARCODE}:
            qs = ItemIdentifier.objects.filter(identifier_type=self.identifier_type, normalized_value=self.normalized_value, organization_name=self.organization_name)
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            if qs.exists():
                raise ValidationError({'value': 'This identifier is already registered.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


def normalize_identifier(value, identifier_type=''):
    return (value or '').strip().upper().replace(' ', '')


def get_effective_classification_attributes(classification):
    ids = []
    current = classification
    while current:
        ids.append(current.pk)
        current = current.parent
    return ClassificationAttribute.objects.filter(classification_id__in=ids, is_active=True).select_related('attribute_definition', 'classification')


class BOM(models.Model):
    TYPE_ENGINEERING = 'ENGINEERING'
    TYPE_MANUFACTURING = 'MANUFACTURING'
    TYPE_SERVICE = 'SERVICE'
    TYPE_PHANTOM = 'PHANTOM'
    BOM_TYPE_CHOICES = [(TYPE_ENGINEERING, 'Engineering'), (TYPE_MANUFACTURING, 'Manufacturing'), (TYPE_SERVICE, 'Service'), (TYPE_PHANTOM, 'Phantom')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    parent_item_revision = models.ForeignKey(ItemRevision, on_delete=models.PROTECT, related_name='boms')
    bom_code = models.CharField(max_length=80, blank=True)
    bom_type = models.CharField(max_length=24, choices=BOM_TYPE_CHOICES, default=TYPE_ENGINEERING)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='created_boms')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['parent_item_revision__item__item_code', 'bom_type', 'created_at']
        unique_together = [('parent_item_revision', 'bom_type', 'bom_code')]
        indexes = [models.Index(fields=['parent_item_revision', 'bom_type']), models.Index(fields=['is_active'])]

    def clean(self):
        super().clean()
        if not self.bom_code and self.parent_item_revision_id:
            self.bom_code = f'{self.parent_item_revision.item.item_code}-{self.parent_item_revision.revision}-{self.bom_type}'
        if self.bom_code:
            self.bom_code = self.bom_code.strip()

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.parent_item_revision} / {self.bom_type}'


class BOMRevision(models.Model):
    STATUS_DRAFT = 'DRAFT'
    STATUS_UNDER_REVIEW = 'UNDER_REVIEW'
    STATUS_APPROVED = 'APPROVED'
    STATUS_RELEASED = 'RELEASED'
    STATUS_SUPERSEDED = 'SUPERSEDED'
    STATUS_OBSOLETE = 'OBSOLETE'
    LOCKED_STATUSES = {STATUS_RELEASED, STATUS_SUPERSEDED, STATUS_OBSOLETE}
    STATUS_CHOICES = ItemRevision.STATUS_CHOICES
    TECHNICAL_FIELDS = {'bom', 'revision', 'description', 'effective_from', 'effective_to', 'is_active'}

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    bom = models.ForeignKey(BOM, on_delete=models.PROTECT, related_name='revisions')
    revision = models.CharField(max_length=30)
    description = models.TextField(blank=True)
    effective_from = models.DateField(null=True, blank=True)
    effective_to = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    is_active = models.BooleanField(default=True)
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='submitted_bom_revisions')
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='approved_bom_revisions')
    approved_at = models.DateTimeField(null=True, blank=True)
    released_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='released_bom_revisions')
    released_at = models.DateTimeField(null=True, blank=True)
    superseded_at = models.DateTimeField(null=True, blank=True)
    superseded_by = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='supersedes')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='created_bom_revisions')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        unique_together = [('bom', 'revision')]
        indexes = [models.Index(fields=['bom', 'status']), models.Index(fields=['effective_from', 'effective_to'])]

    def clean(self):
        super().clean()
        if self.revision:
            normalized = self.revision.strip()
            if not normalized:
                raise ValidationError({'revision': 'BOM revision code is required.'})
            queryset = BOMRevision.objects.filter(bom_id=self.bom_id, revision__iexact=normalized)
            if self.pk:
                queryset = queryset.exclude(pk=self.pk)
            if queryset.exists():
                raise ValidationError({'revision': 'BOM revision code must be unique for this BOM.'})
            self.revision = normalized
        if self.effective_from and self.effective_to and self.effective_to < self.effective_from:
            raise ValidationError({'effective_to': 'Effective end date cannot precede effective start date.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.bom} / {self.revision}'


class BOMLine(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    bom_revision = models.ForeignKey(BOMRevision, on_delete=models.CASCADE, related_name='lines')
    sequence = models.PositiveIntegerField(default=10)
    component_item_revision = models.ForeignKey(ItemRevision, on_delete=models.PROTECT, related_name='used_in_bom_lines')
    quantity = models.DecimalField(max_digits=18, decimal_places=6)
    unit = models.CharField(max_length=30, default='EA')
    scrap_percent = models.DecimalField(max_digits=7, decimal_places=3, default=0)
    is_phantom = models.BooleanField(default=False)
    is_optional = models.BooleanField(default=False)
    reference_designator = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['sequence', 'created_at']
        unique_together = [('bom_revision', 'sequence')]
        indexes = [models.Index(fields=['bom_revision', 'component_item_revision'])]

    def clean(self):
        super().clean()
        if self.quantity is not None and self.quantity <= 0:
            raise ValidationError({'quantity': 'Quantity must be greater than zero.'})
        if self.scrap_percent is not None and self.scrap_percent < 0:
            raise ValidationError({'scrap_percent': 'Scrap percentage cannot be negative.'})
        if self.unit:
            self.unit = self.unit.strip().upper()
        if self.bom_revision_id and self.component_item_revision_id:
            parent_id = self.bom_revision.bom.parent_item_revision_id
            if self.component_item_revision_id == parent_id:
                raise ValidationError({'component_item_revision': 'A BOM line cannot reference the parent item revision.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.bom_revision} / {self.sequence}'


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
