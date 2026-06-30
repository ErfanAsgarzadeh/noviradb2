from rest_framework import mixins, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied

from ktcPlanning.permissions import is_system_admin
from .models import AuditEvent
from .serializers import AuditEventSerializer


class AuditEventViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """
    خواندن لاگ سیستم. فقط برای مدیر سیستم.
    فیلترها (همه اختیاری):
      - actor=<user-id>
      - action=<str>
      - category=<auth|data|business|permission|other>
      - target_model=<ModelName>
      - target_id=<id>
      - from=<iso-datetime>     محدودیتِ زمانِ شروع
      - to=<iso-datetime>       محدودیتِ زمانِ پایان
      - success=<true|false>
      - search=<text>           جستجو در action/target_repr/path/error
    """
    queryset = AuditEvent.objects.all().select_related('actor')
    serializer_class = AuditEventSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        if not is_system_admin(self.request.user):
            raise PermissionDenied("لاگ سیستم فقط برای مدیر سیستم در دسترس است.")

        qs = super().get_queryset()
        p = self.request.query_params

        if p.get('actor'):
            qs = qs.filter(actor_id=p['actor'])
        if p.get('action'):
            qs = qs.filter(action=p['action'])
        if p.get('category'):
            qs = qs.filter(category=p['category'])
        if p.get('target_model'):
            qs = qs.filter(target_model=p['target_model'])
        if p.get('target_id'):
            qs = qs.filter(target_id=p['target_id'])
        if p.get('from'):
            qs = qs.filter(timestamp__gte=p['from'])
        if p.get('to'):
            qs = qs.filter(timestamp__lte=p['to'])
        if p.get('success') in ('true', 'false'):
            qs = qs.filter(success=p['success'] == 'true')
        if p.get('search'):
            from django.db.models import Q
            s = p['search']
            qs = qs.filter(
                Q(action__icontains=s)
                | Q(target_repr__icontains=s)
                | Q(request_path__icontains=s)
                | Q(error_message__icontains=s)
                | Q(actor_username__icontains=s)
            )
        return qs
