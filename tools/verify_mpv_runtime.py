#!/usr/bin/env python3
"""Reject an unpatched, unpinned, or too-new SingWS libmpv runtime."""
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys


def output(*args: str) -> str:
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT)


parser = argparse.ArgumentParser()
parser.add_argument("library", type=pathlib.Path)
parser.add_argument("--arch", required=True, choices=("arm64", "x86_64"))
parser.add_argument("--maximum", default="12.3")
args = parser.parse_args()

if not args.library.is_file():
    sys.exit(f"missing libmpv runtime: {args.library}")

file_info = output("file", str(args.library))
if args.arch not in file_info:
    sys.exit(f"wrong architecture ({args.arch} required): {file_info.strip()}")

build_info = output("vtool", "-show-build", str(args.library))
versions = re.findall(r"^\s*minos\s+(\d+(?:\.\d+)*)", build_info, re.MULTILINE)
if not versions:
    sys.exit("libmpv has no macOS LC_BUILD_VERSION deployment target")

def version_tuple(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split("."))

if any(version_tuple(value) > version_tuple(args.maximum) for value in versions):
    sys.exit(f"libmpv requires macOS {max(versions, key=version_tuple)}; maximum is {args.maximum}")

dependencies = output("otool", "-L", str(args.library)).splitlines()[2:]
bad_dependencies = []
for line in dependencies:
    dependency = line.strip().split(" (", 1)[0]
    if not dependency.startswith(("/System/Library/", "/usr/lib/")):
        bad_dependencies.append(dependency)
if bad_dependencies:
    sys.exit("libmpv has non-system dependencies:\n  " + "\n  ".join(bad_dependencies))

strings = output("strings", str(args.library))
required = ("mpv v0.41.0", "coreaudio", "avfoundation", "rubberband")
missing = [marker for marker in required if marker not in strings]
if missing:
    sys.exit("libmpv is missing required features/version markers: " + ", ".join(missing))
forbidden = "unable to set the input channel layout on the audio unit"
if forbidden in strings:
    sys.exit("libmpv still contains the macOS 27 CoreAudio failure path")

print(
    f"Verified patched mpv 0.41.0 runtime: {args.arch}, macOS {versions[0]}+, "
    "CoreAudio + AVFoundation + Rubber Band, system-only dynamic dependencies"
)
