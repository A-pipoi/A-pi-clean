"""Offline credential policy; never send passwords to external services."""
from __future__ import annotations

import re

_COMMON = frozenset({
    'password', 'password123', 'password1234', 'password12345', 'password123!',
    '1234567890', '12345678901', '123456789012', 'qwerty12345',
    'qwertyuiop', 'qwertyuiop123', 'administrator', 'admin12345',
    'admin123456', 'letmein1234', 'welcome123', 'welcome1234',
    'iloveyou123', 'changeme123', 'abc123456789',
})


def validate_password(password: str) -> None:
    if not isinstance(password, str) or not 10 <= len(password) <= 1024:
        raise ValueError('password_length')
    normalized = password.casefold().strip()
    if normalized in _COMMON or len(set(normalized)) <= 2 or not normalized:
        raise ValueError('password_weak')
    # Block common stems with trivial digit/punctuation suffixes.
    if re.fullmatch(r'(password|admin|welcome|changeme|qwerty|letmein)[\d\W]*', normalized):
        raise ValueError('password_weak')
