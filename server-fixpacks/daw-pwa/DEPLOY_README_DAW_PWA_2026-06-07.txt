SingWS DAW PWA overlay - 2026-06-07

Extract this zip over the SingWS webroot, preserving paths.

Included:
- daw-manifest.php
- daw-assets/daw-icon.svg
- daw-assets/daw-icon-16.png
- daw-assets/daw-icon-32.png
- daw-assets/daw-icon-48.png
- daw-assets/daw-icon-180.png
- daw-assets/daw-icon-192.png
- daw-assets/daw-icon-256.png
- daw-assets/daw-icon-512.png
- daw-assets/daw-favicon-16.png
- daw-assets/daw-favicon-32.png
- daw-assets/daw-favicon-48.png
- daw-assets/daw-favicon.ico
- host-controls/index.php

Expected behavior:
- /daw-controls.php?user=<tenant> uses title "SingWS DAW".
- The DAW page links /daw-manifest.php?user=<tenant>.
- The DAW manifest has name "SingWS DAW", short_name "DAW",
  display "standalone", and start_url "/daw-controls.php?user=<tenant>".
- The DAW page uses icons under /daw-assets/ and does not reuse the main
  SingWS favicon or app icons.
- /host-controls/ keeps its existing "SingWS Host Controls" title and does
  not receive the DAW manifest.
