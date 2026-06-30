"""
Cookie-based JWT Authentication Views
--------------------------------------
جایگزین امن‌تر برای TokenObtainPairView و TokenRefreshView.

به‌جای برگرداندن توکن‌ها در body پاسخ (که فرانت‌اند آن‌ها را در localStorage ذخیره می‌کند
و در معرض XSS قرار می‌گیرند)، توکن‌ها را در httpOnly Cookie ذخیره می‌کند.

httpOnly Cookie:
  - توسط JavaScript قابل خواندن نیست (مقاومت در برابر XSS)
  - به‌صورت خودکار با هر درخواست مرورگر ارسال می‌شود
  - SameSite=Lax از CSRF جلوگیری می‌کند
"""

from django.conf import settings
from django.middleware.csrf import get_token
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.serializers import (
    TokenObtainPairSerializer,
    TokenRefreshSerializer,
)
from rest_framework_simplejwt.tokens import RefreshToken


# ── تنظیمات Cookie ──────────────────────────────────────────────────────────

def _cookie_settings(max_age: int) -> dict:
    """تنظیمات مشترک برای set_cookie."""
    return {
        "max_age": max_age,
        "httponly": True,
        "secure": not settings.DEBUG,   # در production حتماً True (HTTPS)
        "samesite": "Lax",
        "path": "/",
    }


ACCESS_COOKIE_NAME  = "access_token"
REFRESH_COOKIE_NAME = "refresh_token"
ACCESS_MAX_AGE      = int(
    getattr(settings, "SIMPLE_JWT", {})
    .get("ACCESS_TOKEN_LIFETIME", __import__("datetime").timedelta(days=1))
    .total_seconds()
)
REFRESH_MAX_AGE     = int(
    getattr(settings, "SIMPLE_JWT", {})
    .get("REFRESH_TOKEN_LIFETIME", __import__("datetime").timedelta(days=7))
    .total_seconds()
)


def _set_auth_cookies(response: Response, access: str, refresh: str) -> None:
    """هر دو توکن را در httpOnly Cookie ذخیره می‌کند."""
    response.set_cookie(ACCESS_COOKIE_NAME,  access,  **_cookie_settings(ACCESS_MAX_AGE))
    response.set_cookie(REFRESH_COOKIE_NAME, refresh, **_cookie_settings(REFRESH_MAX_AGE))


def _clear_auth_cookies(response: Response) -> None:
    """کوکی‌های توکن را حذف می‌کند (logout)."""
    response.delete_cookie(ACCESS_COOKIE_NAME,  path="/")
    response.delete_cookie(REFRESH_COOKIE_NAME, path="/")


# ── Views ────────────────────────────────────────────────────────────────────

class CookieTokenObtainPairView(APIView):
    """
    POST /api/auth/login/
    body: { "username": "...", "password": "..." }

    در صورت موفقیت:
      - access_token  → httpOnly Cookie
      - refresh_token → httpOnly Cookie
      - body          → { "detail": "ok", "user": { ... } }
    """
    permission_classes = [AllowAny]
    throttle_scope = "login"

    def post(self, request):
        serializer = TokenObtainPairSerializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
        except TokenError as e:
            raise InvalidToken(e.args[0])

        access  = serializer.validated_data["access"]
        refresh = serializer.validated_data["refresh"]

        # گرفتن یوزر از طریق توکن
        from rest_framework_simplejwt.tokens import AccessToken
        from django.contrib.auth import get_user_model
        User = get_user_model()
        decoded = AccessToken(access)
        user = User.objects.get(id=decoded["user_id"])

        from CustomUser.serializers import CustomUserSerializer
        user_data = CustomUserSerializer(user).data

        response = Response(
            {"detail": "Login successful.", "user": user_data},
            status=status.HTTP_200_OK,
        )
        _set_auth_cookies(response, access, refresh)
        response["X-CSRFToken"] = get_token(request)
        return response


class CookieTokenRefreshView(APIView):
    """
    POST /api/auth/token/refresh/

    refresh_token را از Cookie می‌خواند، access جدید می‌سازد و Cookie را به‌روز می‌کند.
    body لازم نیست — توکن از Cookie خوانده می‌شود.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        refresh_token = request.COOKIES.get(REFRESH_COOKIE_NAME)

        if not refresh_token:
            return Response(
                {"detail": "Refresh token not found in cookies."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        serializer = TokenRefreshSerializer(data={"refresh": refresh_token})
        try:
            serializer.is_valid(raise_exception=True)
        except TokenError as e:
            # توکن منقضی یا باطل‌شده — کوکی‌ها را پاک کن
            response = Response(
                {"detail": "Token is invalid or expired."},
                status=status.HTTP_401_UNAUTHORIZED,
            )
            _clear_auth_cookies(response)
            return response

        access = serializer.validated_data["access"]
        # اگر ROTATE_REFRESH_TOKENS فعال باشد، refresh جدید هم صادر می‌شود
        new_refresh = serializer.validated_data.get("refresh", refresh_token)

        response = Response({"detail": "Token refreshed."}, status=status.HTTP_200_OK)
        _set_auth_cookies(response, access, new_refresh)
        return response


class CookieLogoutView(APIView):
    """
    POST /api/auth/logout/

    refresh_token را از Cookie می‌خواند، blacklist می‌کند و هر دو Cookie را حذف می‌کند.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        refresh_token = request.COOKIES.get(REFRESH_COOKIE_NAME)

        response = Response({"detail": "Logged out successfully."}, status=status.HTTP_200_OK)
        _clear_auth_cookies(response)

        if refresh_token:
            try:
                token = RefreshToken(refresh_token)
                token.blacklist()
            except Exception:
                # حتی اگر blacklist ناموفق بود، Cookie را پاک می‌کنیم
                pass

        return response
