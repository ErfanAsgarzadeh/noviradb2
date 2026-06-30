"""
مدلِ AuditEvent — هر رویدادِ مهمی که در سیستم اتفاق می‌افتد اینجا ثبت می‌شود.
رکوردها فقط افزوده می‌شوند (immutable). هرگز ویرایش/حذف نمی‌شوند.
"""
import uuid
from django.db import models
from django.conf import settings


class AuditEvent(models.Model):
    CATEGORY_AUTH = 'auth'
    CATEGORY_DATA = 'data'
    CATEGORY_BUSINESS = 'business'
    CATEGORY_PERMISSION = 'permission'
    CATEGORY_OTHER = 'other'

    CATEGORY_CHOICES = [
        (CATEGORY_AUTH, 'احراز هویت'),
        (CATEGORY_DATA, 'تغییر داده'),
        (CATEGORY_BUSINESS, 'عملیات کاری'),
        (CATEGORY_PERMISSION, 'دسترسی'),
        (CATEGORY_OTHER, 'سایر'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)

    # کاربری که عمل را انجام داده
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='audit_events',
        verbose_name='کاربر'
    )
    # کپیِ نام کاربری در زمان رویداد — اگر کاربر بعداً حذف شود این باقی می‌ماند
    actor_username = models.CharField(max_length=150, blank=True, default='')

    # چه اتفاقی افتاد
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default=CATEGORY_OTHER, db_index=True)
    action = models.CharField(max_length=64, db_index=True,
                              help_text='نمونه: create, update, delete, login, approve_report, lock_revision, ...')

    # هدفِ عمل (مثلاً پروژهٔ X، گزارش Y)
    target_model = models.CharField(max_length=100, blank=True, default='', db_index=True)
    target_id = models.CharField(max_length=64, blank=True, default='', db_index=True)
    target_repr = models.CharField(max_length=255, blank=True, default='',
                                   help_text='نمایشِ خوانا از هدف در زمان رویداد')

    # تغییرات (برای updateها): {field: {old: ..., new: ...}}
    changes = models.JSONField(blank=True, null=True)
    # داده‌های اضافی (مثلاً reasonِ rejection)
    extra = models.JSONField(blank=True, null=True)

    # context درخواست
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=512, blank=True, default='')
    request_method = models.CharField(max_length=8, blank=True, default='')
    request_path = models.CharField(max_length=512, blank=True, default='')
    status_code = models.PositiveSmallIntegerField(null=True, blank=True)

    # نتیجه
    success = models.BooleanField(default=True, db_index=True)
    error_message = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['actor', '-timestamp']),
            models.Index(fields=['target_model', 'target_id']),
            models.Index(fields=['action', '-timestamp']),
            models.Index(fields=['category', '-timestamp']),
        ]
        verbose_name = 'رویداد لاگ'
        verbose_name_plural = 'رویدادهای لاگ'

    def __str__(self):
        who = self.actor_username or '—'
        return f'[{self.timestamp:%Y-%m-%d %H:%M}] {who} → {self.action} ({self.target_model}:{self.target_id})'
