"""Errors shared by transport and research services."""


class ApiConflictError(RuntimeError):
    """An HTTP conflict with a stable machine-readable code."""

    def __init__(self, message: str, api_code: str) -> None:
        super().__init__(message)
        self.api_code = api_code
