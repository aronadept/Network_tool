"""Password and local SNMP credential protection. Never expose stored secrets in APIs."""
import base64
import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path
from cryptography.fernet import Fernet


def password_hash(password):
    if not isinstance(password, str) or not 12 <= len(password) <= 256:
        raise ValueError('Password must contain 12 to 256 characters')
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    return base64.b64encode(salt + digest).decode()


def password_matches(password, stored):
    if not isinstance(password, str) or len(password) > 256:
        return False
    data = base64.b64decode(stored)
    return hmac.compare_digest(data[16:], hashlib.scrypt(password.encode(), salt=data[:16], n=16384, r=8, p=1))


def credential_cipher(db_path):
    key_path = Path(db_path).parent / '.credential-key'
    try:
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(Fernet.generate_key())
    return Fernet(key_path.read_bytes())


def encrypt_credentials(db_path, data):
    return credential_cipher(db_path).encrypt(json.dumps(data).encode()).decode()


def decrypt_credentials(db_path, value):
    return json.loads(credential_cipher(db_path).decrypt(value.encode()))
