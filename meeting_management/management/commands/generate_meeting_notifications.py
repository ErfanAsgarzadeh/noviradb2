from django.core.management.base import BaseCommand

from meeting_management.services import generate_action_deadline_notifications


class Command(BaseCommand):
    help = "Generate idempotent meeting-management notifications."

    def handle(self, *args, **options):
        notifications = generate_action_deadline_notifications()
        self.stdout.write(self.style.SUCCESS(f"Generated or reused {len(notifications)} notifications."))
