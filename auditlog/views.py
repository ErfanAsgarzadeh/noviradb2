from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied

from ktcPlanning.permissions import is_system_admin
from .models import AuditEvent
from .serializers import AuditEventSerializer


class AuditEventPagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = 'page_size'
    max_page_size = 200


class AuditEventViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """
    Read system audit log. Only system administrators can access this endpoint.
    Optional filters:
      - actor=<user-id>
      - action=<str>
      - category=<auth|data|business|permission|other>
      - target_model=<ModelName>
      - target_id=<id>
      - from=<iso-datetime>
      - to=<iso-datetime>
      - success=<true|false>
      - search=<text> across action/target/path/error/actor
    """
    queryset = AuditEvent.objects.all().select_related('actor')
    serializer_class = AuditEventSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = AuditEventPagination

    def get_queryset(self):
        if not is_system_admin(self.request.user):
            raise PermissionDenied("System audit log is only available to system administrators.")

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

    @action(detail=False, methods=['get'])
    def facets(self, request):
        if not is_system_admin(request.user):
            raise PermissionDenied("System audit log is only available to system administrators.")

        qs = AuditEvent.objects.all()
        return Response({
            'categories': [
                {'value': value, 'label': label}
                for value, label in AuditEvent.CATEGORY_CHOICES
            ],
            'actions': list(
                qs.exclude(action='')
                .values_list('action', flat=True)
                .distinct()
                .order_by('action')[:250]
            ),
            'targetModels': list(
                qs.exclude(target_model='')
                .values_list('target_model', flat=True)
                .distinct()
                .order_by('target_model')[:250]
            ),
        })
