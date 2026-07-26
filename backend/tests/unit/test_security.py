from datetime import timedelta

import jwt

from app.core.security import (
    create_access_token,
    create_token,
    decode_token,
    hash_password,
    token_fingerprint,
    verify_password,
)


def test_password_hash_is_verifiable_and_not_plaintext() -> None:
    password = "CorrectHorseBatteryStaple!"
    hashed = hash_password(password)

    assert hashed != password
    assert verify_password(password, hashed)
    assert not verify_password("wrong-password", hashed)


def test_access_token_round_trip() -> None:
    token = create_access_token("user-123", ["admin", "expert"])
    payload = decode_token(token, "access")

    assert payload["sub"] == "user-123"
    assert payload["roles"] == ["admin", "expert"]
    assert payload["type"] == "access"


def test_token_type_is_enforced() -> None:
    token = create_token("user-123", "refresh", timedelta(minutes=5))

    try:
        decode_token(token, "access")
    except jwt.InvalidTokenError:
        pass
    else:
        raise AssertionError("refresh token must not be accepted as an access token")


def test_token_fingerprint_is_stable_and_non_reversible() -> None:
    fingerprint = token_fingerprint("secret-token")
    assert len(fingerprint) == 64
    assert fingerprint == token_fingerprint("secret-token")
    assert fingerprint != "secret-token"
