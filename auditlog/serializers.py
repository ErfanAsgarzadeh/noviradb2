from rest_framework import serializers
from .models import AuditEvent


class AuditEventSerializer(serializers.ModelSerializer):
    actorUsername = serializers.CharField(source='actor_username', read_only=True)
    targetModel = serializers.CharField(source='target_model', read_only=True)
    targetId = serializers.CharField(source='target_id', read_only=True)
    targetRepr = serializers.CharField(source='target_repr', read_only=True)
    ipAddress = serializers.IPAddressField(source='ip_address', read_only=True)
    userAgent = serializers.CharField(source='user_agent', read_only=True)
    requestMethod = serializers.CharField(source='request_method', read_only=True)
    requestPath = serializers.CharField(source='request_path', read_only=True)
    statusCode = serializers.IntegerField(source='status_code', read_only=True)
    errorMessage = serializers.CharField(source='error_message', read_only=True)

    class Meta:
        model = AuditEvent
        fields = [
            'id', 'timestamp',
            'actor', 'actorUsername',
            'category', 'action',
            'targetModel', 'targetId', 'targetRepr',
            'changes', 'extra',
            'ipAddress', 'userAgent',
            'requestMethod', 'requestPath', 'statusCode',
            'success', 'errorMessage',
        ]
        # لاگ immutable است
        read_only_fields = fields
