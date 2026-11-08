"""Secret resolver for encrypted environment variables.

Uses Fernet symmetric encryption so that sensitive values (e.g., Oracle passwords)
can be stored encrypted in the .env file and decrypted at runtime.

The SECRET_KEY in .env is used as the Fernet key.  Use the helper script
`scripts/encrypt-secret.py` to generate the key and encrypt values.

Workflow:
1. Generate a Fernet key:  python scripts/encrypt-secret.py key
2. Set SECRET_KEY=<generated_key> in .env
3. Encrypt a password:     python scripts/encrypt-secret.py encrypt "MyP@ssw0rd"
4. Set ORACLE_PASSWORD=<encrypted_blob> in .env
5. The app decrypts it at startup — no plain-text password in .env.
"""

import base64
import logging

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)


class SecretResolver:
    """Decrypts Fernet-encrypted secrets from environment variables.

    A Fernet-encrypted token always starts with `gAAAAA`.  If a value does not
    match this pattern, it is returned as-is (plain-text fallback for backwards
    compatibility during migration).
    """

    FERNET_PREFIX = "gAAAAA"

    def __init__(self, secret_key: str):
        """Initialize with a raw Fernet key (base64-encoded 32-byte key).

        Args:
            secret_key: Base64-encoded Fernet key from SECRET_KEY env var.
        """
        try:
            self._fernet = Fernet(secret_key.encode("ascii"))
        except Exception as e:
            logger.warning("Invalid SECRET_KEY — secrets will be returned as-is: %s", e)
            self._fernet = None

    def resolve(self, secret_value: str | None) -> str | None:
        """Resolve a secret value, decrypting if it looks like a Fernet token.

        Args:
            secret_value: Either a Fernet-encrypted token or a plain-text value.

        Returns:
            Decrypted string, or the original value if decryption is not applicable.
        """
        if secret_value is None:
            return None

        # Fast path: not encrypted
        if not secret_value.startswith(self.FERNET_PREFIX):
            logger.debug("Secret value is plain-text (not Fernet-encrypted)")
            return secret_value

        if self._fernet is None:
            logger.error(
                "Cannot decrypt: SECRET_KEY is invalid. "
                "Run `python scripts/encrypt-secret.py key` and set it in .env."
            )
            return secret_value

        try:
            decrypted = self._fernet.decrypt(secret_value.encode("ascii")).decode("utf-8")
            logger.debug("Secret decrypted successfully")
            return decrypted
        except InvalidToken:
            logger.error(
                "Decryption failed — SECRET_KEY may not match the key used to encrypt. "
                "Re-encrypt with the current SECRET_KEY."
            )
            return None
        except Exception as e:
            logger.error("Unexpected decryption error: %s", e)
            return None

    def encrypt(self, plain_text: str) -> str:
        """Encrypt a plain-text value for storage in .env.

        Primarily used by the helper script, but exposed here for programmatic use.

        Args:
            plain_text: The value to encrypt.

        Returns:
            Base64-encoded Fernet token (starts with gAAAAA).
        """
        if self._fernet is None:
            raise RuntimeError("Cannot encrypt: SECRET_KEY is invalid")
        token = self._fernet.encrypt(plain_text.encode("utf-8"))
        return token.decode("ascii")


# Module-level singleton — initialized once at app startup.
_resolver: SecretResolver | None = None


def init_resolver(secret_key: str) -> SecretResolver:
    """Initialize the global secret resolver.

    Call this once during app startup (e.g., in FastAPI lifespan).
    """
    global _resolver
    _resolver = SecretResolver(secret_key)
    return _resolver


def get_resolver() -> SecretResolver:
    """Get the global secret resolver instance.

    Returns a no-op resolver if not yet initialized.
    """
    if _resolver is None:
        return SecretResolver("")  # no-op fallback
    return _resolver
