"""
ط³ط±ظˆغŒط³ظگ ظ…ط±ع©ط²غŒظگ ط«ط¨طھظگ ظ„ط§ع¯.

ط§ط³طھظپط§ط¯ظ‡ ط¯ط± ظˆغŒظˆظ‡ط§:
    from auditlog.services import log_event
    log_event('approve_report', target=report, category='business',
              extra={'progress': 75}, request=request)

ظ†ع©ط§طھ:
- request ط§ط®طھغŒط§ط±غŒ ط§ط³طھط› ط§ع¯ط± ط¯ط§ط¯ظ‡ ظ†ط´ظˆط¯طŒ ط§ط² thread-local ع¯ط±ظپطھظ‡ ظ…غŒâ€Œط´ظˆط¯ (ع©ظ‡ middleware
  ظ…غŒâ€Œع¯ط°ط§ط±ط¯).
- ط§غŒظ† طھط§ط¨ط¹ ظ‡ط±ع¯ط² exception ظ¾ط±طھط§ط¨ ظ†ظ…غŒâ€Œع©ظ†ط¯ â€” ط§ع¯ط± ظ†ظˆط´طھظ† ط¯ط± ط¯غŒطھط§ط¨غŒط³ fail ط´ظˆط¯طŒ
  ط­ط¯ط§ظ‚ظ„ ط¯ط± ظپط§غŒظ„ظگ log ظ…غŒâ€Œظ†ظˆغŒط³ط¯.
"""
import ipaddress
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
    """ط«ط¨طھظگ غŒع© ط±ظˆغŒط¯ط§ط¯. ظ‡ط±ع¯ط² exception ظ¾ط±طھط§ط¨ ظ†ظ…غŒâ€Œع©ظ†ط¯."""
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

    # طھظ„ط§ط´ ط¨ط±ط§غŒ ظ†ظˆط´طھظ† ط¯ط± DB
    try:
        AuditEvent.objects.create(**fields)
    except Exception as e:
        # ط§ع¯ط± migrate ظ†ط´ط¯ظ‡ غŒط§ DB ط¯ط± ط¯ط³طھط±ط³ ظ†غŒط³طھطŒ ظپظ‚ط· ط¯ط± ظپط§غŒظ„ ط¨ظ†ظˆغŒط³
        logger.warning('audit DB write failed: %s', e)

    # ظ‡ظ…غŒط´ظ‡ ط¯ط± ظپط§غŒظ„ظگ log ظ‡ظ… ظ…غŒâ€Œظ†ظˆغŒط³غŒظ… â€” ظ„ط§غŒظ‡ظ” ط¯ظˆظ… ط¨ط±ط§غŒ resilience
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
    """طھظپط§ظˆطھظگ ط¯ظˆ dict ط±ط§ ط¨ظ‡ ظپط±ظ…طھظگ {field: {old, new}} ط¨ط±ظ…غŒâ€Œع¯ط±ط¯ط§ظ†ط¯."""
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
            # ط³ط¹غŒ ظ…غŒâ€Œع©ظ†غŒظ… ظ…ظ‚ط§ط¯غŒط± ط±ط§ ط¨ظ‡ ط­ط§ظ„طھظگ JSON-serializable ط¯ط± ط¨غŒط§ظˆط±غŒظ…
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
    """ظ†ط³ط®ظ‡ظ” ط³ط§ط¯ظ‡â€ŒغŒ model_to_dict ع©ظ‡ FKظ‡ط§ ط±ط§ ط¨ظ‡ ID طھط¨ط¯غŒظ„ ظ…غŒâ€Œع©ظ†ط¯."""
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


