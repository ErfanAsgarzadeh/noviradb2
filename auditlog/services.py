"""
سرویسِ مرکزیِ ثبتِ لاگ.

استفاده در ویوها:
    from auditlog.services import log_event
    log_event('approve_report', target=report, category='business',
              extra={'progress': 75}, request=request)

نکات:
- request اختیاری است؛ اگر داده نشود، از thread-local گرفته می‌شود (که middleware
  می‌گذارد).
- این تابع هرگز exception پرتاب نمی‌کند — اگر نوشتن در دیتابیس fail شود،
  حداقل در فایلِ log می‌نویسد.
"""
import logging
import threading

logger = logging.getLogger('audit')

_local = threading.local()


def get_current_request():
    return getattr(_local, 'request', None)


def set_current_request(request):
    _local.request = request


def clear_current_request():
    if hasattr(_local, 'request'):
        del _local.request


def _client_ip(request):
    if not request:
        return None
    fwd = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if fwd:
        return fwd.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def _request_context(request):
    if not request:
        return {}
    actor = getattr(request, 'user', None)
    if actor and not getattr(actor, 'is_authenticated', False):
        actor = None
    return {
        'actor': actor,
        'actor_username': (getattr(actor, 'username', '') or '')[:150],
        'ip_address': _client_ip(request),
        'user_agent': (request.META.get('HTTP_USER_AGENT', '') or '')[:512],
        'request_method': (getattr(request, 'method', '') or '')[:8],
        'request_path': (getattr(request, 'path', '') or '')[:512],
    }


def _target_fields(target):
    if target is None:
        return {'target_model': '', 'target_id': '', 'target_repr': ''}
    try:
        repr_ = str(target)[:255]
    except Exception:
        repr_ = ''
    return {
        'target_model': target.__class__.__name__[:100],
        'target_id': str(getattr(target, 'pk', '') or '')[:64],
        'target_repr': repr_,
    }


def log_event(
    action,
    *,
    target=None,
    changes=None,
    extra=None,
    category='other',
    success=True,
    error_message='',
    status_code=None,
    request=None,
):
    """ثبتِ یک رویداد. هرگز exception پرتاب نمی‌کند."""
    from .models import AuditEvent

    request = request or get_current_request()
    ctx = _request_context(request)
    if status_code is not None:
        ctx['status_code'] = int(status_code)

    fields = dict(
        category=str(category)[:20],
        action=str(action)[:64],
        changes=changes,
        extra=extra,
        success=bool(success),
        error_message=(error_message or '')[:2000],
        **_target_fields(target),
        **ctx,
    )

    # تلاش برای نوشتن در DB
    try:
        AuditEvent.objects.create(**fields)
    except Exception as e:
        # اگر migrate نشده یا DB در دسترس نیست، فقط در فایل بنویس
        logger.warning('audit DB write failed: %s', e)

    # همیشه در فایلِ log هم می‌نویسیم — لایهٔ دوم برای resilience
    logger.info(
        'audit | actor=%s | action=%s | target=%s:%s (%s) | category=%s | '
        'success=%s | path=%s | ip=%s',
        ctx.get('actor_username') or 'anonymous',
        action,
        fields.get('target_model'),
        fields.get('target_id'),
        fields.get('target_repr'),
        category,
        success,
        ctx.get('request_path'),
        ctx.get('ip_address'),
    )


def diff_dicts(old, new, fields=None):
    """تفاوتِ دو dict را به فرمتِ {field: {old, new}} برمی‌گرداند."""
    if old is None and new is None:
        return None
    old = old or {}
    new = new or {}
    keys = set(fields) if fields else set(old.keys()) | set(new.keys())
    out = {}
    for k in keys:
        a = old.get(k)
        b = new.get(k)
        if a != b:
            # سعی می‌کنیم مقادیر را به حالتِ JSON-serializable در بیاوریم
            out[k] = {'old': _safe(a), 'new': _safe(b)}
    return out or None


def _safe(v):
    if v is None or isinstance(v, (str, int, float, bool, list, dict)):
        return v
    try:
        return str(v)
    except Exception:
        return None


def model_to_dict_safe(instance, fields=None):
    """نسخهٔ ساده‌ی model_to_dict که FKها را به ID تبدیل می‌کند."""
    if instance is None:
        return None
    out = {}
    for f in instance._meta.concrete_fields:
        if fields and f.name not in fields:
            continue
        try:
            val = getattr(instance, f.attname, None)
            out[f.name] = _safe(val)
        except Exception:
            out[f.name] = None
    return out
