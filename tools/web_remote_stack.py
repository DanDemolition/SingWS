#!/usr/bin/env python3
"""Start the loopback stack (real PHP serving SingWS-Server, a relay, the app's real command code on a sample rotation) and keep it up so a browser can
open the real web remote against it:   SINGWS_NODE_RELAY_DIR=<dir with server.js + node_modules> python tools/web_remote_stack.py [apply|dry_run|off]
Prints the URL and the TEST PIN (a value private to this throwaway stack). Ctrl-C / SIGTERM tears everything down."""
import os
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import control_loopback as cl  # noqa: E402

mode = sys.argv[1] if len(sys.argv) > 1 else "apply"
stop = False
signal.signal(signal.SIGTERM, lambda *a: globals().__setitem__("stop", True))
signal.signal(signal.SIGINT, lambda *a: globals().__setitem__("stop", True))
with cl.Stack(mode) as st:
    print(f"READY http://127.0.0.1:{st.php_port}/v2/daw-controls.php?user={cl.TENANT}  tenant={cl.TENANT} pin={cl.PIN} mode={mode}", flush=True)
    while not stop:
        time.sleep(0.5)
