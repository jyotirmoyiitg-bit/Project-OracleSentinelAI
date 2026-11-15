"""Password hashes for local operator accounts. The hash is not reversible."""

import hashlib
import hmac
import secrets


def hash_password(password: str, iterations: int) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, rounds, salt_hex, digest_hex = stored.split("$", 3)
        iterations = int(rounds)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256" or iterations < 1000 or iterations > 1_000_000:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(digest, expected)


MIN_PASSWORD_LENGTH = 10


def password_problem(password: str, username: str = "") -> str | None:
    """Why a new local password is refused, or None when it is acceptable."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"The password must be at least {MIN_PASSWORD_LENGTH} characters."
    if len(password) > 200:
        return "The password must be at most 200 characters."
    if not any(char.isalpha() for char in password) or not any(char.isdigit() for char in password):
        return "The password must contain a letter and a digit."
    if username and username.strip().lower() in password.lower():
        return "The password must not contain the username."
    return None
