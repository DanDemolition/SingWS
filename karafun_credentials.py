"""The KaraFun account's email and password, kept in the macOS Keychain (never in settings.json, never in a log).

`security -i` reads its commands from stdin, so the password is not in any process's argument list; it is passed as hex so no quoting can break it.
"""
import subprocess
import sys
from typing import Optional, Tuple

SERVICE = "SingWS KaraFun account"


def _run(commands: str, *, timeout: float = 10.0) -> Tuple[int, str]:
    result = subprocess.run(["/usr/bin/security", "-i"], input=commands, capture_output=True, text=True, timeout=timeout)
    return result.returncode, result.stdout


def available() -> bool:
    return sys.platform == "darwin"


def save(email: str, password: str) -> bool:
    email, password = str(email or "").strip(), str(password or "")
    if not available() or not email or not password or any(c in email for c in "\"\\\n\r"):
        return False
    delete(email)          # replace THIS account only: never touch another saved login (a no-argument delete wiped them all)
    # Stored as "h:<hex of the password>" (plain ASCII), because `security ... -w` prints anything non-ASCII back as hex, which would be ambiguous.
    hexed = ("h:" + password.encode("utf-8").hex()).encode("ascii").hex()
    code, out = _run(f'add-generic-password -a "{email}" -s "{SERVICE}" -X {hexed}\n')
    return code == 0 and "error" not in out.lower()


def load(email: str) -> Optional[str]:
    email = str(email or "").strip()
    if not available() or not email or any(c in email for c in "\"\\\n\r"):
        return None
    try:
        result = subprocess.run(["/usr/bin/security", "find-generic-password", "-a", email, "-s", SERVICE, "-w"],
                                capture_output=True, text=True, timeout=10)
    except Exception:
        return None
    if result.returncode != 0:
        return None
    stored = result.stdout.rstrip("\n")
    if not stored.startswith("h:"):
        return None
    try:
        return bytes.fromhex(stored[2:]).decode("utf-8") or None
    except ValueError:
        return None


def delete(email: str = "") -> None:
    """Remove the saved account (every one saved under this service when no email is given)."""
    if not available():
        return
    for _ in range(5):
        cmd = ["/usr/bin/security", "delete-generic-password", "-s", SERVICE]
        if email:
            cmd += ["-a", email]
        try:
            if subprocess.run(cmd, capture_output=True, timeout=10).returncode != 0:
                return
        except Exception:
            return
        if email:
            return
