from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from .models import TaskRole, Resource, Assignment, ResourcePool

@receiver(post_save, sender=TaskRole)
def sync_executor_to_assignment(sender, instance, created, **kwargs):
    """
    وقتی کاربری به عنوان مجری (executor) روی یک تسک تنظیم می‌شود،
    سیستم برای او یک Resource ساخته (یا پیدا می‌کند) و او را در جدول Assignment ثبت می‌کند
    تا موتور تسطیح (cpmLeveling) بتواند ظرفیت او را محاسبه کند.
    """
    if instance.role == 'executor':
        user = instance.user
        task = instance.task
        
        # ۱. پیدا کردن یا ساختن Pool مرتبط با پروژه 
        # (در cpmLeveling خط 93 با pool__project فیلتر کرده‌اید، پس منبع باید در Pool پروژه باشد)
        pool_name = "مخزن یکپارچه منابع انسانی (Global Labor Pool)"
        pool, _ = ResourcePool.objects.get_or_create(
            name=pool_name,
            defaults={
                'description': 'تمام کاربران سیستم به صورت خودکار در این مخزن سراسری قرار می‌گیرند.'
            }
        )

        # ۲. ایجاد یا پیدا کردن منبع
        display_name = f"{user.username} ({user.job_title})" if getattr(user, 'job_title', None) else user.username
        resource_code = user.id

        resource, _ = Resource.objects.get_or_create(
            code=resource_code,
            defaults={
                'name': display_name,
                'resource_type': Resource.LABOR,
                'pool': pool,  # همه به این مخزن سراسری وصل می‌شوند
                'max_units': 100.00,
                'is_active': True
            }
        )

        # ۳. تزریق تخصیص به جدول Assignment (خوراکِ اصلی موتور cpmLeveling)
        Assignment.objects.update_or_create(
            revision=instance.revision,
            task=instance.task,
            resource=resource,
            defaults={
                'units_percent': 100.00, # یعنی این مجری تمام وقت روی این تسک است
                'planned_hours': 0       # موتور شما روی units_percent کار می‌کند
            }
        )

@receiver(post_delete, sender=TaskRole)
def remove_executor_assignment(sender, instance, **kwargs):
    """
    اگر نقش مجری از کاربر گرفته شد، تخصیص او از جدول Assignment پاک می‌شود
    تا در تسطیح‌های بعدی، ظرفیت او اشغال نماند.
    """
    if instance.role == 'executor':
        resource_code = getattr(instance.user, 'employee_code', None) or f"USR-{instance.user.id}"
        
        # پاک کردن Assignment مرتبط در همین ریویژن
        Assignment.objects.filter(
            revision=instance.revision,
            task=instance.task,
            resource__code=resource_code
        ).delete()