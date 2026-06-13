import hashlib
import secrets


def generate_key_pair(key_type: str = "live") -> dict:
    """
    Generate a public + secret key pair.

    The secret key is returned ONCE (never stored raw). Returns:
        public_key, secret_key       -- raw, return to user a single time
        secret_hash, secret_prefix   -- persisted in DB
    """
    pub_random = secrets.token_hex(16)  # 32 hex chars
    sec_random = secrets.token_hex(24)  # 48 hex chars

    public_key = f"pk_{key_type}_{pub_random}"
    secret_key = f"sk_{key_type}_{sec_random}"
    secret_hash = hashlib.sha256(secret_key.encode()).hexdigest()
    secret_prefix = secret_key[:16]  # e.g. "sk_live_x9y8z7w" — safe to show in UI

    return {
        "public_key": public_key,
        "secret_key": secret_key,  # RETURN ONCE ONLY
        "secret_hash": secret_hash,
        "secret_prefix": secret_prefix,
    }


def hash_secret(secret_key: str) -> str:
    return hashlib.sha256(secret_key.encode()).hexdigest()
