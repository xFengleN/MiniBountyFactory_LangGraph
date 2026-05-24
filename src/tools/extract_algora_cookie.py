"""
Extract the Algora session cookie (_algora_key) from browser profiles.

Usage:
  python -m src.tools.extract_algora_cookie

Outputs the raw cookie value to stdout.
Exits with code 0 if found, 1 if not found.
"""
import sys
import os
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path


def try_firefox():
    profiles = Path.home() / 'Library' / 'Application Support' / 'Firefox' / 'Profiles'
    if not profiles.exists():
        return None
    for profile in sorted(profiles.iterdir()):
        if not profile.is_dir():
            continue
        db_path = profile / 'cookies.sqlite'
        if not db_path.exists():
            continue
        try:
            dst = tempfile.mktemp(suffix='.sqlite')
            shutil.copy2(str(db_path), dst)
            conn = sqlite3.connect(dst)
            row = conn.execute(
                "SELECT value FROM moz_cookies WHERE host LIKE '%algora%' AND name='_algora_key'"
            ).fetchone()
            conn.close()
            os.unlink(dst)
            if row:
                return ('firefox', row[0])
        except Exception:
            continue
    return None


def try_chrome(cookie_dir: Path, name: str):
    if not cookie_dir.exists():
        return None
    db_path = cookie_dir / 'Cookies'
    if not db_path.exists():
        return None
    try:
        dst = tempfile.mktemp(suffix='.sqlite')
        shutil.copy2(str(db_path), dst)
        conn = sqlite3.connect(dst)
        row = conn.execute(
            "SELECT encrypted_value FROM cookies WHERE host_key LIKE '%algora%' AND name='_algora_key'"
        ).fetchone()
        conn.close()
        if not row:
            os.unlink(dst)
            return None
        encrypted = row[0]
        os.unlink(dst)
        value = decrypt_chrome(encrypted)
        if value:
            return (name, value)
    except Exception:
        if os.path.exists(dst):
            os.unlink(dst)
    return None


def decrypt_chrome(encrypted: bytes) -> str | None:
    try:
        result = subprocess.run(
            ['security', 'find-generic-password', '-w', '-s', 'Chrome Safe Storage'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            return None
        key_b64 = result.stdout.strip()
        if not key_b64:
            return None
    except FileNotFoundError:
        return None

    import base64
    key = base64.b64decode(key_b64)
    if len(encrypted) > 3 and encrypted[:3] == b'v10':
        nonce = encrypted[3:15]
        ciphertext = encrypted[15:-16]
        tag = encrypted[-16:]
    else:
        return None

    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        aesgcm = AESGCM(key)
        plain = aesgcm.decrypt(nonce, ciphertext + tag, None)
        return plain.decode('utf-8')
    except ImportError:
        return None


def main():
    browsers = [
        ('Chrome', Path.home() / 'Library' / 'Application Support' / 'Google' / 'Chrome' / 'Default'),
        ('Brave', Path.home() / 'Library' / 'Application Support' / 'BraveSoftware' / 'Brave-Browser' / 'Default'),
        ('Edge', Path.home() / 'Library' / 'Application Support' / 'Microsoft Edge' / 'Default'),
        ('Chromium', Path.home() / 'Library' / 'Application Support' / 'Chromium' / 'Default'),
        ('Arc', Path.home() / 'Library' / 'Application Support' / 'Arc' / 'User Data' / 'Default'),
    ]

    for name, path in browsers:
        result = try_chrome(path, name.lower())
        if result:
            print(result[1])
            print(f'  (from {result[0]})', file=sys.stderr)
            return 0

    result = try_firefox()
    if result:
        print(result[1])
        print(f'  (from {result[0]})', file=sys.stderr)
        return 0

    print('Algora cookie not found in any browser profile.', file=sys.stderr)
    print(file=sys.stderr)
    print('To get the cookie manually:', file=sys.stderr)
    print('  1. Login at https://algora.io/bounties using GitHub OAuth', file=sys.stderr)
    print('  2. Open DevTools > Application > Cookies > algora.io', file=sys.stderr)
    print('  3. Copy the value of _algora_key', file=sys.stderr)
    print('  4. Set it in config.yaml: algora.session_cookie: "<value>"', file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
