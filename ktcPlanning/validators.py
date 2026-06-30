"""
File Upload Validators
-----------------------
اعتبارسنجی فایل‌های آپلودشده در TaskChatMessage.

- حداکثر حجم: 10 مگابایت
- پسوندهای مجاز: تصاویر، PDF، فایل‌های Office، متن ساده، فشرده
- Content-type چک می‌شود تا کاربر نتواند فایل مخرب را با پسوند عوض‌کرده آپلود کند

نکته: از callable class استفاده شده تا Django بتواند آن را در migrations serialize کند.
"""

import os
from django.core.exceptions import ValidationError

MAX_UPLOAD_SIZE_MB    = 10
MAX_UPLOAD_SIZE_BYTES = MAX_UPLOAD_SIZE_MB * 1024 * 1024

ALLOWED_EXTENSIONS = {
    'jpg', 'jpeg', 'png', 'gif', 'webp', 'svg',
    'pdf',
    'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx',
    'txt', 'csv', 'md',
    'zip', 'rar', '7z',
    'xml',
}

ALLOWED_CONTENT_TYPES = {
    'image/jpeg', 'image/png', 'image/gif', 'image/webp', 'image/svg+xml',
    'application/pdf',
    'application/msword',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'application/vnd.ms-excel',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'application/vnd.ms-powerpoint',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    'text/plain', 'text/csv', 'text/markdown',
    'application/zip',
    'application/x-rar-compressed', 'application/vnd.rar',
    'application/x-7z-compressed',
    'application/xml', 'text/xml',
    'application/octet-stream',
}


class ChatFileValidator:
    """
    Callable class برای validation فایل چت.
    استفاده از class به جای function باعث می‌شود Django بتواند
    آن را در migration serialize کند (با deconstruct).
    """

    def __call__(self, file):
        # ۱. حجم
        if file.size > MAX_UPLOAD_SIZE_BYTES:
            raise ValidationError(
                f"حجم فایل نباید بیشتر از {MAX_UPLOAD_SIZE_MB} مگابایت باشد. "
                f"حجم فایل شما: {file.size / (1024 * 1024):.1f} مگابایت."
            )

        # ۲. پسوند
        ext = os.path.splitext(file.name)[1].lstrip('.').lower()
        if not ext:
            raise ValidationError("فایل باید دارای پسوند باشد.")
        if ext not in ALLOWED_EXTENSIONS:
            raise ValidationError(
                f"پسوند '{ext}' مجاز نیست. "
                f"پسوندهای مجاز: {', '.join(sorted(ALLOWED_EXTENSIONS))}."
            )

        # ۳. content-type
        content_type = getattr(file, 'content_type', None)
        if content_type and content_type not in ALLOWED_CONTENT_TYPES:
            raise ValidationError(f"نوع فایل '{content_type}' مجاز نیست.")

    def __eq__(self, other):
        return isinstance(other, ChatFileValidator)

    def deconstruct(self):
        """لازم است تا Django بتواند این validator را در migration ذخیره کند."""
        return ('ktcPlanning.validators.ChatFileValidator', [], {})


# instance آماده برای استفاده در models.py
validate_chat_file = ChatFileValidator()
