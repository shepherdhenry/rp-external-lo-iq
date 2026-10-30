"""Open iq_gate_gui.py, connect and start, without clicking.

  python launch_gui.py HOST [continuous|trigger] [quadrature_factor] [window_s]
trigger mode also logs every gate and saves every trace to iq_gate_data/trigtest_<time>/
"""
import importlib.util
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
HOST = sys.argv[1] if len(sys.argv) > 1 else "192.168.1.186"
MODE = sys.argv[2] if len(sys.argv) > 2 else "continuous"
QF = float(sys.argv[3]) if len(sys.argv) > 3 else 1.5
WIN = float(sys.argv[4]) if len(sys.argv) > 4 else (0.134 if MODE == "trigger" else 0.0336)

spec = importlib.util.spec_from_file_location("iq_gate_gui", os.path.join(HERE, "iq_gate_gui.py"))
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)

from qtpy import QtCore, QtWidgets  # noqa: E402

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
w = g.Window()
w.host.setText(HOST)
(w.mode_trig if MODE == "trigger" else w.mode_cont).setChecked(True)
w.qf.setValue(QF)

if MODE == "trigger":
    OUT = os.path.join(g.SAVE_DIR, time.strftime("trigtest_%Y%m%d_%H%M%S"))
    os.makedirs(OUT, exist_ok=True)
    LOG = open(os.path.join(OUT, "log.txt"), "a", buffering=1)
    say0, show0, last = w._say, w._show, [""]

    def say(txt):
        if txt.split("(")[0] != last[0]:              # skip repeated "waiting ..." lines
            LOG.write(f"{time.strftime('%H:%M:%S')}  {txt}\n")
            last[0] = txt.split("(")[0]
        say0(txt)

    def show(t, I, Q, gate, trig):
        show0(t, I, Q, gate, trig)
        np.savez(os.path.join(OUT, f"trig_{w.n_acq:04d}.npz"), **w.last, I_full=I, Q_full=Q,
                 t_full=t, gate_s=np.nan if gate is None else gate)

    w._say, w._show = say, show

w.show()


def autostart():
    w._connect()
    if w.be is None:
        return
    w.win.setCurrentIndex(int(np.argmin([abs(w.win.itemData(k) - WIN) for k in range(w.win.count())])))
    w._apply()
    w._toggle()          # Start


QtCore.QTimer.singleShot(300, autostart)
app.exec_()
