from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class Phase1FMigrationTests(TransactionTestCase):
    available_apps = None

    def setUp(self):
        self.executor = MigrationExecutor(connection)
        self.app = 'enterprise_items'
        self.before = [(self.app, '0005_alter_itemidentifier_normalized_value')]
        self.after = [(self.app, '0008_remove_sequencedefinition_uniq_sequence_scope_per_version_and_more')]

    def migrate_to(self, targets):
        self.executor.migrate(targets)
        self.executor.loader.build_graph()
        return self.executor.loader.project_state(targets).apps

    def test_0005_to_0007_preserves_legacy_items_and_is_reversible(self):
        apps = self.migrate_to(self.before)
        CodingOrganization = apps.get_model(self.app, 'CodingOrganization')
        ItemType = apps.get_model(self.app, 'ItemType')
        Item = apps.get_model(self.app, 'Item')
        AttributeDefinition = apps.get_model(self.app, 'AttributeDefinition')
        ItemCodingScheme = apps.get_model(self.app, 'ItemCodingScheme')
        ItemClassification = apps.get_model(self.app, 'ItemClassification')
        ItemCodingTemplate = apps.get_model(self.app, 'ItemCodingTemplate')

        org = CodingOrganization.objects.create(name='Migration Org', code='MIG')
        item_type = ItemType.objects.create(organization=org, name='Part', code='PART', group='PART')
        classification = ItemClassification.objects.create(organization=org, code='MIGCLASS', name='Migration Class')
        attr = AttributeDefinition.objects.create(organization=org, code='LENGTH', name='Length', data_type='INTEGER')
        scheme = ItemCodingScheme.objects.create(organization=org, code='MIGSCHEME', name='Migration Scheme', strategy='HYBRID')
        template = ItemCodingTemplate.objects.create(coding_scheme=scheme, classification=classification, version=1, status='ACTIVE')
        item = Item.objects.create(organization=org, item_type=item_type, classification=classification, item_code='MIG-001', name='Migration Item', semantic_identity_hash='hash-before', semantic_identity_payload={'LENGTH': 10}, coding_scheme=scheme, coding_template=template)

        before_counts = {
            'items': Item.objects.count(),
            'attributes': AttributeDefinition.objects.count(),
            'schemes': ItemCodingScheme.objects.count(),
            'templates': ItemCodingTemplate.objects.count(),
        }
        before_summary = list(Item.objects.values_list('id', 'item_code', 'semantic_identity_hash', 'semantic_identity_payload'))

        apps = self.migrate_to(self.after)
        ItemAfter = apps.get_model(self.app, 'Item')
        AttributeAfter = apps.get_model(self.app, 'AttributeDefinition')
        SchemeAfter = apps.get_model(self.app, 'ItemCodingScheme')
        TemplateAfter = apps.get_model(self.app, 'ItemCodingTemplate')
        migrated = ItemAfter.objects.get(pk=item.pk)
        self.assertEqual(migrated.item_code, 'MIG-001')
        self.assertEqual(migrated.semantic_identity_hash, 'hash-before')
        self.assertEqual(migrated.semantic_identity_payload, {'LENGTH': 10})
        self.assertIsNone(migrated.structure_id)
        self.assertIsNone(migrated.code_definition_id)
        self.assertIsNone(migrated.code_definition_version_id)
        self.assertEqual(migrated.coding_snapshot, {})
        self.assertEqual(ItemAfter.objects.count(), before_counts['items'])
        self.assertEqual(AttributeAfter.objects.count(), before_counts['attributes'])
        self.assertEqual(SchemeAfter.objects.count(), before_counts['schemes'])
        self.assertEqual(TemplateAfter.objects.count(), before_counts['templates'])
        self.assertEqual(list(ItemAfter.objects.values_list('id', 'item_code', 'semantic_identity_hash', 'semantic_identity_payload')), before_summary)

        apps = self.migrate_to(self.before)
        ItemBeforeAgain = apps.get_model(self.app, 'Item')
        reversed_item = ItemBeforeAgain.objects.get(pk=item.pk)
        self.assertEqual(reversed_item.item_code, 'MIG-001')
        self.assertEqual(reversed_item.semantic_identity_hash, 'hash-before')
        self.assertFalse(hasattr(reversed_item, 'structure_id'))

        apps = self.migrate_to(self.after)
        ItemReapplied = apps.get_model(self.app, 'Item')
        reapplied_item = ItemReapplied.objects.get(pk=item.pk)
        self.assertEqual(reapplied_item.item_code, 'MIG-001')
        self.assertEqual(reapplied_item.semantic_identity_hash, 'hash-before')
        self.assertIsNone(reapplied_item.structure_id)