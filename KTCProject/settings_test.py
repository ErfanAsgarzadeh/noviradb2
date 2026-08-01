"""Deterministic settings for the Django test suite.

The application settings remain environment-driven for development and
production.  Tests deliberately use an in-memory SQLite database so they do
not depend on a developer's local PostgreSQL, MinIO, or production-like
environment file.
"""

from .settings import *  # noqa: F403


SECRET_KEY = 'test-secret-key-only-for-testing-never-use-in-production'
DEBUG = False
USE_S3_STORAGE = False
if os.environ.get('PHASE16_POSTGRES_TESTS', '0').lower() in ('1', 'true', 'yes'):
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': os.environ.get('POSTGRES_DB'),
            'USER': os.environ.get('POSTGRES_USER'),
            'PASSWORD': os.environ.get('POSTGRES_PASSWORD'),
            'HOST': os.environ.get('POSTGRES_HOST'),
            'PORT': os.environ.get('POSTGRES_PORT'),
            'CONN_MAX_AGE': 0,
            'OPTIONS': {
                'connect_timeout': int(os.environ.get('POSTGRES_CONNECT_TIMEOUT', '5')),
                'options': f"-c statement_timeout={int(os.environ.get('POSTGRES_STATEMENT_TIMEOUT_MS', '30000'))}",
            },
            'TEST': {
                'NAME': os.environ.get('POSTGRES_TEST_DB') or f"test_{os.environ.get('POSTGRES_DB')}",
            },
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': ':memory:',
        }
    }
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
