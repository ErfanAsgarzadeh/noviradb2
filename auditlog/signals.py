"""
ثبتِ رویدادهای auth از طریقِ Django signals.
نکته: simple-jwt مستقیماً user_logged_in را trigger نمی‌کند، پس برای ورود از
JWT، middleware روی POST /api/token/ یک رکوردِ http_post می‌گذارد و در صورتِ
نیاز می‌توان signalِ سفارشی هم اضافه کرد.
"""
from django.contrib.auth.signals import (
    user_logged_in, user_logged_out, user_login_failed
)
from django.dispatch import receiver

from .services import log_event


@receiver(user_logged_in)
def _on_login(sender, request, user, **kwargs):
    log_event('login', target=user, category='auth', request=request)


@receiver(user_logged_out)
def _on_logout(sender, request, user, **kwargs):
    log_event('logout', target=user, category='auth', request=request)


@receiver(user_login_failed)
def _on_login_failed(sender, credentials, request=None, **kwargs):
    log_event(
        'login_failed',
        category='auth',
        success=False,
        extra={'username_attempted': (credentials or {}).get('username', '')[:150]},
        request=request,
    )
