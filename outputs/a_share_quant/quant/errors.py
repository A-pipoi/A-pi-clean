"""Typed validation errors retaining legacy boundary compatibility."""


class InputTypeError(TypeError, ValueError):
    """Wrong input type; existing ValueError handlers remain compatible."""


class ProviderShapeError(RuntimeError):
    """Invalid provider result, not caller misuse; preserve HTTP 502 handling."""
