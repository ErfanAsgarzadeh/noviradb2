from django.core.exceptions import ValidationError


class EngineeringLifecycleError(ValidationError):
    """Raised when an engineering item revision lifecycle operation is invalid."""


class EngineeringPermissionError(PermissionError):
    """Raised when a user is not allowed to perform an engineering lifecycle action."""
