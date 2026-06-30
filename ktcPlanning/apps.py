from django.apps import AppConfig


class KtcplanningConfig(AppConfig):
    name = 'ktcPlanning'

    def ready(self):
        # این خط حیاتیه! سیگنال‌ها رو موقع ران شدن سرور لود می‌کنه
        import ktcPlanning.signals
