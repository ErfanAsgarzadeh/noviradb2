"""
Management Command: seed_iran_1405_calendar
============================================
Creates a full Iranian (Jalali) 1405 calendar for a given project.

  Year span : 1405/01/01 → 1405/12/29
              2026-03-21 → 2027-03-20  (Gregorian)

Working schedule (Iran standard):
  Working days : Saturday → Wednesday  (Django weekdays: 5, 6, 0, 1, 2)
  Off days     : Thursday + Friday     (Django weekdays: 3, 4)
  Working hours: 08:30 → 17:00  (8.5 hours/day)

Public holidays:
  • Fixed Iranian civil holidays
  • Islamic holidays for Hijri 1447–1448
    (moon-sighting dependent; dates are official estimates — adjust if needed)

Django weekday reference:
  0=Mon  1=Tue  2=Wed  3=Thu  4=Fri  5=Sat  6=Sun

Usage:
  python manage.py seed_iran_1405_calendar --project-id <uuid>
  python manage.py seed_iran_1405_calendar --project-id <uuid> --calendar-name "Iran 1405"
  python manage.py seed_iran_1405_calendar --project-id <uuid> --no-set-default
"""

from datetime import date, time
from django.core.management.base import BaseCommand, CommandError
from  ktcPlanning.models import Calendar, WorkingInterval, CalendarException, Project


# ─────────────────────────────────────────────────────────────────────────────
# 1405 PUBLIC HOLIDAYS  (Gregorian dates)
# ─────────────────────────────────────────────────────────────────────────────
IRAN_1405_HOLIDAYS: list[tuple[date, str]] = [

    # ── Nowruz block (1–4 Farvardin) ─────────────────────────────────────────
    (date(2026, 3, 21), "نوروز — ۱ فروردین ۱۴۰۵"),
    (date(2026, 3, 22), "نوروز — ۲ فروردین ۱۴۰۵"),
    (date(2026, 3, 23), "نوروز — ۳ فروردین ۱۴۰۵"),
    (date(2026, 3, 24), "نوروز — ۴ فروردین ۱۴۰۵"),

    # ── Eid al-Fitr 1447  (falls inside early 1405) ──────────────────────────
    # Ramadan 1447 ends ~late March 2026; Eid ~30 March 2026 (estimate)
    (date(2026, 3, 30), "عید فطر — ۱ شوال ۱۴۴۷"),
    (date(2026, 3, 31), "تعطیل پس از عید فطر"),

    # ── Islamic Republic Day — 12 Farvardin ──────────────────────────────────
    (date(2026, 4,  1), "روز جمهوری اسلامی ایران — ۱۲ فروردین"),

    # ── Sizdah-Bedar (Nature Day) — 13 Farvardin ─────────────────────────────
    (date(2026, 4,  2), "روز طبیعت — سیزده‌به‌در"),

    # ── Death of Imam Khomeini — 14 Khordad ──────────────────────────────────
    (date(2026, 6,  3), "رحلت حضرت امام خمینی — ۱۴ خرداد"),

    # ── Uprising of 15 Khordad ───────────────────────────────────────────────
    (date(2026, 6,  4), "قیام ۱۵ خرداد"),

    # ── Eid al-Adha 1447 — 10 Dhul Hijja ────────────────────────────────────
    # Estimate: ~16 June 2026
    (date(2026, 6, 16), "عید قربان — ۱۰ ذی‌الحجه ۱۴۴۷"),
    (date(2026, 6, 17), "تعطیل پس از عید قربان"),

    # ── Eid al-Ghadir — 18 Dhul Hijja 1447 ──────────────────────────────────
    (date(2026, 6, 24), "عید غدیر خم — ۱۸ ذی‌الحجه ۱۴۴۷"),

    # ── Tasua & Ashura — 9 & 10 Muharram 1448 ────────────────────────────────
    # Estimate: ~24–25 July 2026
    (date(2026, 7, 24), "تاسوعای حسینی — ۹ محرم ۱۴۴۸"),
    (date(2026, 7, 25), "عاشورای حسینی — ۱۰ محرم ۱۴۴۸"),

    # ── Arba'een — 20 Safar 1448 ─────────────────────────────────────────────
    (date(2026, 9,  1), "اربعین حسینی — ۲۰ صفر ۱۴۴۸"),

    # ── Death of Prophet Muhammad & Martyrdom of Imam Hassan — 28 Safar 1448 ─
    (date(2026, 9,  9), "رحلت رسول اکرم (ص) و شهادت امام حسن مجتبی (ع)"),

    # ── Martyrdom of Imam Reza — 1 Dhul Qa'da 1447 / 30 Safar 1448 ──────────
    (date(2026, 9, 11), "شهادت امام رضا (ع)"),

    # ── Martyrdom of Imam Hassan Askari — 8 Rabi al-Awwal 1448 ──────────────
    (date(2026, 9, 19), "شهادت امام حسن عسکری (ع)"),

    # ── Birthday of Prophet Muhammad & Imam Sadeq — 17 Rabi al-Awwal 1448 ───
    (date(2026, 9, 28), "میلاد رسول اکرم (ص) و امام صادق (ع)"),

    # ── Victory of the Islamic Revolution — 22 Bahman ────────────────────────
    (date(2027, 2, 11), "پیروزی انقلاب اسلامی — ۲۲ بهمن ۱۴۰۵"),

    # ── Nationalization of Iranian Oil Industry — 29 Esfand ──────────────────
    (date(2027, 3, 19), "روز ملی شدن صنعت نفت ایران — ۲۹ اسفند ۱۴۰۵"),
]


# ─────────────────────────────────────────────────────────────────────────────
# Working-day weekday codes (Django / Python convention)
# ─────────────────────────────────────────────────────────────────────────────
# Iran: Sat–Wed work, Thu–Fri off
WORKING_WEEKDAYS = [
    5,  # Saturday
    6,  # Sunday
    0,  # Monday
    1,  # Tuesday
    2,  # Wednesday
]
WORK_START = time(8, 30)
WORK_END   = time(17, 0)


class Command(BaseCommand):
    help = "Seed a full Iranian 1405 calendar (Sat–Wed, 08:30–17:00) for a project."

    def add_arguments(self, parser):
        parser.add_argument(
            "--project-id",
            required=True,
            help="UUID of the target Project",
        )
        parser.add_argument(
            "--calendar-name",
            default="تقویم استاندارد ایران ۱۴۰۵",
            help="Display name for the Calendar record (default: تقویم استاندارد ایران ۱۴۰۵)",
        )
        parser.add_argument(
            "--set-default",
            action="store_true",
            default=True,
            dest="set_default",
            help="Mark this calendar as the project default (default: True)",
        )
        parser.add_argument(
            "--no-set-default",
            action="store_false",
            dest="set_default",
        )

    # ── main ─────────────────────────────────────────────────────────────────
    def handle(self, *args, **options):
        project_id   = options["project_id"]
        calendar_name = options["calendar_name"]
        set_default  = options["set_default"]

        # 1. Resolve project
        try:
            project = Project.objects.get(pk=project_id)
        except Project.DoesNotExist:
            raise CommandError(f"Project with id='{project_id}' does not exist.")

        self.stdout.write(self.style.HTTP_INFO(
            f"\n  Project  : {project.name}  [{project.pk}]"
        ))
        self.stdout.write(self.style.HTTP_INFO(
            f"  Calendar : {calendar_name}\n"
        ))

        # 2. Create (or reset) Calendar
        calendar, created = Calendar.objects.get_or_create(
            project=project,
            name=calendar_name,
            defaults={"is_default": set_default},
        )

        if not created:
            self.stdout.write(self.style.WARNING(
                f"  ⚠  Calendar '{calendar_name}' already exists. "
                "Clearing old intervals & exceptions and reseeding…"
            ))
            calendar.intervals.all().delete()
            calendar.exceptions.all().delete()
        else:
            self.stdout.write(self.style.SUCCESS(
                f"  ✔  Calendar created  (id={calendar.pk})"
            ))

        # 3. WorkingInterval rows  — one per working weekday
        intervals = [
            WorkingInterval(
                calendar=calendar,
                weekday=day,
                start_time=WORK_START,
                end_time=WORK_END,
            )
            for day in WORKING_WEEKDAYS
        ]
        WorkingInterval.objects.bulk_create(intervals)
        self.stdout.write(self.style.SUCCESS(
            f"  ✔  {len(intervals)} working-day intervals created  "
            f"(Sat Sun Mon Tue Wed  |  08:30 → 17:00  |  8.5 h/day)"
        ))

        # 4. CalendarException rows — public holidays (non-working)
        exceptions = [
            CalendarException(
                calendar=calendar,
                date=holiday_date,
                is_working=False,
                description=description,
            )
            for holiday_date, description in IRAN_1405_HOLIDAYS
        ]
        CalendarException.objects.bulk_create(exceptions, ignore_conflicts=True)
        self.stdout.write(self.style.SUCCESS(
            f"  ✔  {len(exceptions)} public-holiday exceptions loaded  "
            f"(1405 / 2026–2027)"
        ))

        # 5. Summary table
        weekday_labels = {
            0: "Monday", 1: "Tuesday", 2: "Wednesday",
            3: "Thursday", 4: "Friday", 5: "Saturday", 6: "Sunday",
        }
        self.stdout.write("")
        self.stdout.write("  " + "─" * 51)
        self.stdout.write(f"  {'FIELD':<22} {'VALUE'}")
        self.stdout.write("  " + "─" * 51)
        self.stdout.write(f"  {'Calendar ID':<22} {calendar.pk}")
        self.stdout.write(f"  {'Project':<22} {project.name}")
        self.stdout.write(f"  {'Jalali year':<22} 1405  (2026-03-21 → 2027-03-20)")
        self.stdout.write(f"  {'Working days':<22} Saturday → Wednesday")
        self.stdout.write(
            f"  {'Off days':<22} Thursday, Friday"
        )
        self.stdout.write(f"  {'Work hours':<22} 08:30 → 17:00  (8.5 h/day)")
        self.stdout.write(f"  {'Public holidays':<22} {len(exceptions)} days")
        self.stdout.write(f"  {'Is default':<22} {calendar.is_default}")
        self.stdout.write("  " + "─" * 51)
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            "  Iranian 1405 Calendar seeded successfully ✓\n"
        ))
