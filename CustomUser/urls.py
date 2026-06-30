from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    RegisterView, UserProfileView, UserListView, logout_view,
    OrgUnitViewSet, UserManagementViewSet, UsersInMyUnitView
)
from .cookie_auth import (
    CookieTokenObtainPairView,
    CookieTokenRefreshView,
    CookieLogoutView,
)

router = DefaultRouter()
router.register(r'org-units', OrgUnitViewSet, basename='org-unit')
router.register(r'manage-users', UserManagementViewSet, basename='manage-user')

urlpatterns = [
    path('register/', RegisterView.as_view(), name='auth_register'),
    path('profile/', UserProfileView.as_view(), name='user_profile'),
    path('users/', UserListView.as_view(), name='user-list'),
    path('users/in-my-unit/', UsersInMyUnitView.as_view(), name='users-in-my-unit'),

    # ── Cookie-based auth endpoints (جایگزین امن‌تر) ──
    path('login/', CookieTokenObtainPairView.as_view(), name='cookie_login'),
    path('token/refresh/', CookieTokenRefreshView.as_view(), name='cookie_token_refresh'),
    path('logout/', CookieLogoutView.as_view(), name='cookie_logout'),

    # legacy logout (نگه‌داشته شده برای سازگاری موقت)
    path('logout/legacy/', logout_view, name='logout_legacy'),

    path('', include(router.urls)),
]
