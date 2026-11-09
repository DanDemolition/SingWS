#!/usr/bin/env python3
"""Dev-Mac helper for the live KaraFun runner: post genuine mouse clicks at a screen point, exactly like KaraokeApp._macos_native_mouse_click (same CGEvent calls).
usage: native_click.py X Y [CLICKS]"""
import ctypes
import sys
import time


class CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


def main(x, y, clicks):
    cg = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
    cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    cg.CGEventCreateMouseEvent.restype = ctypes.c_void_p
    cg.CGEventCreateMouseEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint32, CGPoint, ctypes.c_uint32]
    cg.CGEventSetIntegerValueField.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int64]
    cg.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    point = CGPoint(float(x), float(y))
    for n in range(1, max(1, clicks) + 1):
        down = cg.CGEventCreateMouseEvent(None, 1, point, 0)
        up = cg.CGEventCreateMouseEvent(None, 2, point, 0)
        if not down or not up:
            return 1
        cg.CGEventSetIntegerValueField(down, 1, n)
        cg.CGEventSetIntegerValueField(up, 1, n)
        cg.CGEventPost(0, down)
        cg.CGEventPost(0, up)
        cf.CFRelease(down)
        cf.CFRelease(up)
        time.sleep(0.08)
    return 0


if __name__ == "__main__":
    sys.exit(main(int(float(sys.argv[1])), int(float(sys.argv[2])), int(sys.argv[3]) if len(sys.argv) > 3 else 1))
