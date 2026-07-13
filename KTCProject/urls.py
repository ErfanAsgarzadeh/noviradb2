from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from KTCProject.settings import BASE_DIR

urlpatterns = [
    path('admin/', admin.site.urls),

    # غ±. ظ…ط³غŒط±ظ‡ط§غŒ ط¯ط±غŒط§ظپطھ ظˆ طھظ…ط¯غŒط¯ طھظˆع©ظ† ط§ط­ط±ط§ط² ظ‡ظˆغŒطھ (JWT)
    path('api/token/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),

    # غ². ظ…طھطµظ„ ع©ط±ط¯ظ† ظ…ط³غŒط±ظ‡ط§غŒ ط¨ط®ط´ ط¨ط±ظ†ط§ظ…ظ‡â€Œط±غŒط²غŒ ظˆ ع¯ط§ظ†طھ ع†ط§ط±طھ
    path('api/planning/', include('ktcPlanning.urls')),
    path('api/opc/', include('opc.urls')),
    path('api/items/', include('enterprise_items.urls')),
    path('api/auth/', include('CustomUser.urls')),
    path('api/reports/', include('management_reports.urls')),
    path('api/audit/', include('auditlog.urls')),
]

# Serve media files in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)



# â”€â”€â”€ Audit logging: persist to PostgreSQL (via auditlog.AuditEvent) AND to a
#     rotating file on disk for resilience. If the DB write fails (e.g. during
#     an outage), the file log is still complete.
LOGS_DIR = BASE_DIR / 'logs'
LOGS_DIR.mkdir(parents=True, exist_ok=True)

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'audit': {
            'format': '{asctime} | {levelname:7s} | {message}',
            'style': '{',
        },
    },
    'handlers': {
        'audit_file': {
            'level': 'INFO',
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': str(LOGS_DIR / 'audit.log'),
            'maxBytes': 10 * 1024 * 1024,   # 10 MB
            'backupCount': 30,              # ظ†ع¯ظ‡â€Œط¯ط§ط±غŒ غ³غ° ظپط§غŒظ„ظگ rotate ط´ط¯ظ‡ (~300 MB)
            'encoding': 'utf-8',
            'formatter': 'audit',
        },
    },
    'loggers': {
        'audit': {
            'handlers': ['audit_file'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}

