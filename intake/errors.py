"""Errors the pipeline raises on purpose."""


class IntakeError(Exception):
    """Base error for expected failures."""


class AuthRequired(IntakeError):
    """The call did not carry a user."""


class AccessDenied(IntakeError):
    """The user, role, or matter on the call is not allowed."""


class ValidationError(IntakeError):
    """The input breaks a registration or review rule."""


class GateFailed(IntakeError):
    """Ready for attorney was blocked. Reasons are safe to show to staff."""

    def __init__(self, reasons: list[str]):
        self.reasons = list(reasons)
        super().__init__("; ".join(self.reasons))
