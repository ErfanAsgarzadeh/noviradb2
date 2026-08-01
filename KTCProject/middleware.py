"""
IP whitelist middleware for KTCProject.

Restricts access to the ENTIRE site to a set of allowed IPs / CIDR ranges
(e.g. a company's public IP range), configured via the ALLOWED_IP_RANGES
environment variable (see settings.py).

.env example:
    ALLOWED_IP_RANGES=203.0.113.0/24,198.51.100.5,2001:db8::/32

Leave ALLOWED_IP_RANGES empty to disable the restriction entirely
(recommended for local development).
"""

import ipaddress
import logging
import time
import uuid

from django.conf import settings
from django.http import HttpResponseForbidden

logger = logging.getLogger(__name__)


def _get_client_ip(request):
    """
    Returns the client's IP address.

    If TRUST_X_FORWARDED_FOR is enabled in settings (because the app sits
    behind a reverse proxy that sets this header), the first IP in
    X-Forwarded-For is used. Otherwise REMOTE_ADDR (the direct TCP peer)
    is used, which cannot be spoofed by the client.
    """
    if getattr(settings, 'TRUST_X_FORWARDED_FOR', False):
        xff = request.META.get('HTTP_X_FORWARDED_FOR')
        if xff:
            return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


class IPWhitelistMiddleware:
    """
    Blocks any request whose client IP is not inside one of the
    ALLOWED_IP_RANGES networks. Applied to the whole site because it sits
    near the top of MIDDLEWARE, before routing/auth/etc.
    """

    def __init__(self, get_response):
        self.get_response = get_response

        self.networks = []
        for entry in getattr(settings, 'ALLOWED_IP_RANGES', []):
            try:
                self.networks.append(ipaddress.ip_network(entry, strict=False))
            except ValueError:
                logger.warning('ALLOWED_IP_RANGES: ignoring invalid entry %r', entry)

        if not self.networks:
            logger.warning(
                'ALLOWED_IP_RANGES is empty — IP whitelist is DISABLED, '
                'all clients are allowed through.'
            )

    def __call__(self, request):
        # No ranges configured -> restriction disabled (e.g. local dev).
        if not self.networks:
            return self.get_response(request)

        client_ip = _get_client_ip(request)

        try:
            ip_obj = ipaddress.ip_address(client_ip)
        except (ValueError, TypeError):
            logger.warning('IPWhitelistMiddleware: could not parse client IP %r', client_ip)
            return HttpResponseForbidden('Access denied.')

        if any(ip_obj in network for network in self.networks):
            return self.get_response(request)

        logger.info('IPWhitelistMiddleware: blocked request from %s', client_ip)
        return HttpResponseForbidden('Access denied: your IP address is not authorized.')


class RequestCorrelationMiddleware:
    """Attach request, correlation and command IDs to every request/response."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        started = time.monotonic()
        request.request_id = (request.headers.get('X-Request-ID') or str(uuid.uuid4()))[:80]
        request.correlation_id = (request.headers.get('X-Correlation-ID') or request.request_id)[:80]
        request.command_id = (request.headers.get('X-Command-ID') or '')[:120]
        response = self.get_response(request)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        response['X-Request-ID'] = request.request_id
        response['X-Correlation-ID'] = request.correlation_id
        if request.command_id:
            response['X-Command-ID'] = request.command_id
        response['X-Response-Time-ms'] = str(elapsed_ms)
        return response
