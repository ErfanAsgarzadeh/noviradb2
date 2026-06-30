"""
Middleware که:
1) requestِ جاری را در thread-local می‌گذارد تا log_event بدون پاس‌دادنِ request
   بتواند کاربر/IP/path را پیدا کند.
2) همهٔ درخواست‌های state-changing (POST/PUT/PATCH/DELETE) را به‌صورتِ خودکار
   لاگ می‌کند — این یک شبکهٔ ایمنی است؛ ویوهای مهم خودشان log_event دقیق‌تر
   می‌فرستند.
"""
from .services import set_current_request, clear_current_request, log_event


class AuditMiddleware:
    LOGGED_METHODS = {'POST', 'PUT', 'PATCH', 'DELETE'}
    SKIP_PATH_PREFIXES = (
        '/static/', '/media/', '/favicon',
        '/admin/jsi18n/',
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        set_current_request(request)
        try:
            response = self.get_response(request)
            try:
                self._maybe_log(request, response)
            except Exception:
                # نباید middleware درخواست را خراب کند
                pass
            return response
        finally:
            clear_current_request()

    def _maybe_log(self, request, response):
        if request.method not in self.LOGGED_METHODS:
            return
        path = request.path or ''
        for p in self.SKIP_PATH_PREFIXES:
            if path.startswith(p):
                return

        log_event(
            action=f'http_{request.method.lower()}',
            category='data',
            extra={
                'path': path,
                'query_string': (request.META.get('QUERY_STRING') or '')[:500],
            },
            status_code=response.status_code,
            success=200 <= response.status_code < 400,
            request=request,
        )
