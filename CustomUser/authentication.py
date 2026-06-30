"""
Cookie-based JWT Authentication Backend
-----------------------------------------
توکن access را از httpOnly Cookie می‌خواند به‌جای Authorization header.
به‌عنوان DEFAULT_AUTHENTICATION_CLASSES در settings استفاده می‌شود.

برای سازگاری با کلاینت‌هایی که هنوز Authorization header می‌فرستند
(مثل ابزارهای API مثل Postman یا curl)، اگر Cookie خالی بود به header
fallback می‌کند.
"""

from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken, AuthenticationFailed
from django.conf import settings


ACCESS_COOKIE_NAME = "access_token"


class CookieJWTAuthentication(JWTAuthentication):
    """
    ابتدا access_token را از Cookie می‌خواند.
    اگر Cookie خالی بود، به Authorization: Bearer header fallback می‌کند
    (برای سازگاری با Postman و ابزارهای توسعه).
    """

    def authenticate(self, request):
        # ۱. تلاش برای خواندن از Cookie
        raw_token = request.COOKIES.get(ACCESS_COOKIE_NAME)

        if raw_token:
            try:
                validated_token = self.get_validated_token(raw_token)
                return self.get_user(validated_token), validated_token
            except (InvalidToken, AuthenticationFailed):
                # توکن Cookie نامعتبر است — ادامه به fallback
                pass

        # ۲. Fallback: خواندن از Authorization header (رفتار پیش‌فرض simplejwt)
        header = self.get_header(request)
        if header is None:
            return None

        raw_token = self.get_raw_token(header)
        if raw_token is None:
            return None

        validated_token = self.get_validated_token(raw_token)
        return self.get_user(validated_token), validated_token
