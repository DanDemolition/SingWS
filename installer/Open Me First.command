#!/bin/bash
# SingWS — Open Me First
#
# SingWS isn't signed with a paid Apple Developer ID, so after downloading,
# macOS flags it with a "quarantine" attribute and shows a "damaged" or
# "unidentified developer" warning. This helper removes that flag from the
# copy you dragged into Applications, then launches SingWS. You only need to
# run it once.
#
# (The first time you open THIS helper, macOS may also warn you — right-click
#  it and choose Open, or allow it in System Settings > Privacy & Security.)

APP="/Applications/SingWS.app"

clear
echo "============================================"
echo "  SingWS — first-run setup"
echo "============================================"
echo

if [ ! -d "$APP" ]; then
  echo "  SingWS is not in your Applications folder yet."
  echo
  echo "  1) Drag the SingWS icon onto the Applications shortcut"
  echo "     in this window."
  echo "  2) Then run 'Open Me First' again."
  echo
  read -n 1 -s -r -p "  Press any key to close this window…"
  echo
  exit 1
fi

echo "  Removing the macOS quarantine flag from SingWS…"
if xattr -dr com.apple.quarantine "$APP" 2>/dev/null; then
  echo "  Done."
else
  echo "  (Nothing to remove — SingWS is already cleared.)"
fi
echo
echo "  Launching SingWS…"
open "$APP"
sleep 1
echo
echo "  All set! You can close this window. You won't need to run"
echo "  this again — SingWS will open normally from now on."
echo
exit 0
