"""Password hashing with the standard library only (so the CLI works anywhere, e.g. Cloud Shell):
salted PBKDF2-HMAC-SHA256, 600,000 iterations.   Generate a hash:  python3 -m core.passwords"""
from __future__ import annotations

import base64
import getpass
import hashlib
import hmac
import secrets
import sys
from typing import Optional

ITERATIONS = 600_000


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(ITERATIONS, base64.b64encode(salt).decode(), base64.b64encode(dk).decode())


def verify_password(pw: str, stored: Optional[str]) -> bool:
    if not stored or not pw:
        return False
    try:
        parts = stored.split("$")
        if parts[0] == "pbkdf2_sha256":
            _, it, salt, dk = parts
            calc = hashlib.pbkdf2_hmac("sha256", pw.encode(), base64.b64decode(salt), int(it))
        elif parts[0] == "scrypt" and hasattr(hashlib, "scrypt"):
            _, n, r, p, salt, dk = parts
            calc = hashlib.scrypt(pw.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                                  dklen=len(base64.b64decode(dk)))
        else:
            return False
        return hmac.compare_digest(calc, base64.b64decode(dk))
    except Exception:
        return False


def main() -> None:
    pw = getpass.getpass("New admin password (min 12 chars): ")
    if len(pw) < 12:
        sys.exit("Password too short.")
    if getpass.getpass("Repeat: ") != pw:
        sys.exit("Passwords do not match.")
    print(hash_password(pw))


if __name__ == "__main__":
    main()
