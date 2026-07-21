class EngineeringDomainError(Exception):
    code = 'engineering_domain_error'
    status_code = 400

    def __init__(self, message='', *, fields=None, metadata=None, code=None):
        self.message = message or self.__class__.__name__
        self.fields = fields or {}
        self.metadata = metadata or {}
        if code:
            self.code = code
        super().__init__(self.message)

    def as_payload(self):
        return {'error': {'code': self.code, 'message': self.message, 'fields': self.fields, 'metadata': self.metadata}}


class EngineeringValidationError(EngineeringDomainError):
    code = 'engineering_validation_error'


class CrossOrganizationError(EngineeringValidationError):
    code = 'cross_organization_reference'


class ImmutableVersionError(EngineeringValidationError):
    code = 'immutable_version'


class InvalidVersionTransitionError(EngineeringValidationError):
    code = 'invalid_version_transition'


class CodeDefinitionNotReadyError(EngineeringValidationError):
    code = 'code_definition_not_ready'


class CodeGenerationError(EngineeringValidationError):
    code = 'code_generation_error'


class CodeDecodeError(EngineeringValidationError):
    code = 'code_decode_error'


class AmbiguousDecodeError(CodeDecodeError):
    code = 'ambiguous_decode'


class SequenceAllocationError(EngineeringValidationError):
    code = 'sequence_allocation_error'


class SequenceExhaustedError(SequenceAllocationError):
    code = 'sequence_exhausted'
    status_code = 409


class DuplicatePartError(EngineeringValidationError):
    code = 'duplicate_part'
    status_code = 409


class IdentityConflictError(EngineeringValidationError):
    code = 'identity_conflict'


class TechnicalDataValidationError(EngineeringValidationError):
    code = 'technical_data_validation_error'


class PermissionDeniedDomainError(EngineeringDomainError):
    code = 'permission_denied'
    status_code = 403