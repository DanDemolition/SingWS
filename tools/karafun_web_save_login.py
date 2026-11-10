#!/usr/bin/env python3
"""Save the KaraFun account's email and password in the macOS Keychain (the password is typed hidden, never shown or logged).

  python3 tools/karafun_web_save_login.py            # asks for the email and password
  python3 tools/karafun_web_save_login.py --forget   # removes it

The email is also written to SingWS's settings.json (key karafun_web_email) so the app knows which Keychain item to read.
"""
import getpass
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import karafun_credentials as creds


def settings_path() -> Path:
    home = os.environ.get("SINGWS_HOME")
    return (Path(home) if home else Path.home() / "SingWS") / "settings.json"


def update_email(email: str) -> None:
    path = settings_path()
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    data["karafun_web_email"] = email
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def main() -> int:
    if "--forget" in sys.argv:
        creds.delete()
        update_email("")
        print("Saved KaraFun login removed.")
        return 0
    email = input("KaraFun email: ").strip()
    password = getpass.getpass("KaraFun password (hidden): ")
    if not creds.save(email, password):
        print("The Keychain would not save it.")
        return 1
    update_email(email)
    print(f"Saved in the Keychain for {email}.")
    return 0


raise SystemExit(main())
