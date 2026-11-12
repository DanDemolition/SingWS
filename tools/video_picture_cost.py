#!/usr/bin/env python3
"""Measure the CPU cost of the picture path (Rust bridge, real CDG + optional background clip, two windows). Scratch SINGWS_HOME; see docs/design/metal-video-bridge.md.
usage: video_picture_cost.py DYLIB BGFILE|none SECONDS WIDTH HEIGHT TAG  (env as in the doc)"""
import sys, ctypes, time, threading, tempfile, zipfile, subprocess, os, json
from pathlib import Path
sys.path.insert(0, "tools")
from video_bridge_parity import DEFAULT_ZIP, bind
import psutil
from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication, QWidget
dylib, bgfile, secs, W, H, tag = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5]), sys.argv[6]
lib, CB = bind(dylib)
sink = CB(lambda b: None); lib.singws_bridge_set_log_callback(sink)
tmp = Path(tempfile.mkdtemp())
with zipfile.ZipFile(DEFAULT_ZIP) as z: z.extractall(tmp)
fs = sorted(tmp.rglob("*"))
cdg = str(next(f for f in fs if f.suffix.lower()==".cdg")); mp3 = str(next(f for f in fs if f.suffix.lower()==".mp3"))
app = QApplication([])
ow, pw = QWidget(), QWidget()
for w in (ow,pw): w.setWindowFlags(Qt.WindowType.Window|Qt.WindowType.FramelessWindowHint|Qt.WindowType.WindowStaysOnTopHint)
ow.setGeometry(40,60,W,H); pw.setGeometry(40,60+H+20,426,240); ow.show(); pw.show(); app.processEvents()
class M(QObject):
    call = pyqtSignal(object); fin = pyqtSignal()
    def __init__(s):
        super().__init__(); s.call.connect(lambda b: b.__setitem__("r", b["f"](*b["a"])), Qt.ConnectionType.BlockingQueuedConnection); s.fin.connect(app.quit)
m = M()
class P:
    def __getattr__(s,n):
        fn=getattr(lib,n)
        def go(*a):
            b={"f":fn,"a":a}; m.call.emit(b); return b.get("r")
        return go
B = P(); res={}
def gpu():
    o=subprocess.run(["ioreg","-r","-d","1","-c","IOAccelerator"],capture_output=True,text=True).stdout
    import re; mm=re.findall(r'"Device Utilization %"=(\d+)',o); return int(mm[0]) if mm else None
def run():
    try:
        h=B.singws_bridge_create(int(ow.winId()),int(pw.winId()),cdg.encode(),mp3.encode())
        B.singws_bridge_set_volume(h,0.0); B.singws_bridge_play(h)
        if bgfile!="none": B.singws_bridge_load_background(h,bgfile.encode(),0.6)
        time.sleep(4)
        p=psutil.Process(); t0=time.time(); c0=p.cpu_times(); th0={t.id:(t.user_time+t.system_time) for t in p.threads()}
        g=[]
        for _ in range(secs//2): time.sleep(2); g.append(gpu())
        el=time.time()-t0; c1=p.cpu_times(); th1={t.id:(t.user_time+t.system_time) for t in p.threads()}
        res.update(tag=tag,size=f"{W}x{H}",cpu_pct=round(100*((c1.user+c1.system)-(c0.user+c0.system))/el,1),
          top_threads=sorted(((round(100*(th1[i]-th0.get(i,0))/el,1),i) for i in th1),reverse=True)[:5],gpu_util=g,present=int(B.singws_bridge_present_count(h)), seconds=round(el,1))
    except Exception as e: res["err"]=repr(e)
    finally: m.fin.emit()
threading.Thread(target=run,daemon=True).start(); QTimer.singleShot(90000,app.quit); app.exec(); print(json.dumps(res))
