"""Validation and hashing for opaque application session credentials."""

import hashlib
import re
from collections.abc import Callable

_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")


class InvalidOpaqueSessionToken(ValueError):
    """The supplied value cannot be an application session credential."""


def validate_session_token(session_token: object) -> str:
    """Return a strictly validated 256-bit base64url token without padding."""
    if not isinstance(session_token, str) or _TOKEN.fullmatch(session_token) is None:
        raise InvalidOpaqueSessionToken()
    return session_token


def hash_session_token(session_token: object) -> bytes:
    """Hash a validated token for lookup; raw tokens must never be persisted."""
    validated = validate_session_token(session_token)
    return hashlib.sha256(validated.encode("ascii")).digest()


def session_token_hasher(
    error_factory: Callable[[], Exception], *, include_type_error: bool = False
) -> Callable[[object], bytes]:
    """Translate only the rejection contract selected by the existing caller."""
    rejected = (
        (InvalidOpaqueSessionToken, TypeError)
        if include_type_error
        else (InvalidOpaqueSessionToken,)
    )

    def token_hash(value: object) -> bytes:
        try:
            return hash_session_token(value)
        except rejected:
            raise error_factory() from None

    return token_hash
