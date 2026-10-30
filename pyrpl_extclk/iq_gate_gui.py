r"""
IQ gate viewer for the Red Pitaya (PyRPL): amplitude and phase of the 20 MHz signal on IN1
versus time, either inside a gate on IN2 (trigger mode) or continuously.

Signal chain on the FPGA
  IN1 -> iq2 (NCO at f_demod, 2-pole low-pass) -> I = iq2, Q = iq2_2 -> scope (16384 points)
  IN2 -> trig module (threshold + hysteresis, both edges, auto re-arm)
         rising edge  = scope trigger (trace starts at the edge)
         falling edge = trig.trigger_timestamp -> gate length (same 125 MHz counter, ~0.3 us)
  optional: I -> OUT1, Q -> OUT2 (real time)

Trigger mode: the scope is armed only while IN2 is low, so the trace always starts at a rising
edge; only the part inside the gate (rising -> falling edge) is shown. If the gate is longer
than the window, the whole window is shown and the status says so. Gates must be at least
(window + ~0.3 s) apart, because the trace is read back over the network between gates.

IN2 limits (Gen 2 PRO): LV +-1 V full scale (abs. max +-6 V DC, only +-1 V above 1 kHz),
HV +-20 V (abs. max +-30 V). Use HV for TTL gates. Set "IN2 range" to match the jumper.

Run:  python iq_gate_gui.py          (PyRPL 0.9.x, PyQt5, pyqtgraph)
Login uses ~/.ssh/id_ed25519. Connecting re-flashes / resets the FPGA modules.
"""
import os
import sys
import time

_here = os.path.dirname(os.path.abspath(__file__))
sys.path = [q for q in sys.path if os.path.abspath(q or ".") != _here]   # a local "pyrpl" folder would shadow the package

import numpy as np
import paramiko

KEY_FILE = os.path.expanduser("~/.ssh/id_ed25519")
_connect = paramiko.SSHClient.connect


def _connect_with_key(self, hostname, *args, **kw):
    kw.pop("password", None)
    kw.update(key_filename=KEY_FILE, look_for_keys=False, allow_agent=False)
    return _connect(self, hostname, *args, **kw)


paramiko.SSHClient.connect = _connect_with_key

from pyrpl import Pyrpl                       # noqa: E402
from qtpy import QtCore, QtWidgets            # noqa: E402
import pyqtgraph as pg                        # noqa: E402

F_EXACT = 687194768 * 125e6 / 2**32           # 20 000 000.0186 Hz (exact with the external clock)
TS_OFFSET = 40                                # scope trigger timestamp lags the trig edge by ~40 cycles
CLK = 125e6
SAVE_DIR = os.path.join(_here, "iq_gate_data")


class Backend:
    """Thin wrapper around the PyRPL modules used here."""

    def __init__(self, host):
        self.p = Pyrpl(config="iq_gate_gui", hostname=host, gui=False)
        self.rp = self.p.rp
        self.iq, self.s, self.tr = self.rp.iq2, self.rp.scope, self.rp.trig

    def bandwidths(self):
        return [b for b in self.iq.bandwidths if b > 0]

    def durations(self):
        return [d for d in self.s.durations if 1e-4 < d < 20]

    def configure(self, f, bw, qf, phase, dac_out):
        self.iq.setup(frequency=f, bandwidth=[bw, bw], gain=0.0, phase=phase, acbandwidth=50e3,
                      amplitude=0.0, input="in1", output_direct="off",
                      output_signal="quadrature", quadrature_factor=qf)
        for pid, src, out in ((self.rp.pid0, "iq2", "out1"), (self.rp.pid1, "iq2_2", "out2")):
            pid.setup(input=src, output_direct=out if dac_out else "off", setpoint=0, p=1.0, i=0,
                      inputfilter=[])
            pid.ival = 0
        self.s.input1, self.s.input2 = "iq2", "iq2_2"
        self.s.average = True

    def set_window(self, duration):
        self.s.duration = duration
        return self.s.duration

    def setup_trigger(self, thr, hyst):
        self.tr.setup(input="in2", trigger_source="both_edge", threshold=thr, hysteresis=hyst,
                      auto_rearm=True, output_direct="off")

    def in2(self):
        return self.rp.sampler.in2

    def arm(self, triggered):
        s = self.s
        self.edges = set()                      # trig edge timestamps seen while armed
        if triggered:
            s.trigger_source = "dsp"
            s.trigger_delay = s.duration / 2      # trace runs from the edge to +duration
            self.tr.armed = True
            self.ts_arm = self.tr.trigger_timestamp
        else:
            s.trigger_source = "immediately"
        s._start_trace_acquisition()

    def poll_edge(self):
        """Record the latest trig edge; called every tick while armed (keeps the falling edge
        even if the next gate starts before the trace has been read back)."""
        ts = self.tr.trigger_timestamp
        if ts != self.ts_arm:
            self.edges.add(ts)

    def ready(self):
        return self.s.curve_ready()

    def read(self, triggered):
        s = self.s
        I, Q = s._get_trace()
        t = np.linspace(0, s.duration, s.data_length, endpoint=False)
        if not triggered:
            return t, I, Q, None
        self.poll_edge()
        rise = s.trigger_timestamp
        later = [e for e in self.edges if e > rise + 200]     # skip the rising edge itself
        gate = (min(later) - rise + TS_OFFSET) / CLK if later else None
        return t, I, Q, gate


class Window(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Red Pitaya IQ gate viewer")
        self.be = None
        self.state = "idle"
        self.last = None
        self.n_acq = 0
        self._build()
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._tick)

    # ---------------- UI ----------------
    def _build(self):
        pg.setConfigOptions(antialias=True, background="#fcfcfb", foreground="#52514e")
        form = QtWidgets.QFormLayout()
        self.host = QtWidgets.QLineEdit("192.168.1.186")
        self.btn_connect = QtWidgets.QPushButton("Connect")
        self.btn_connect.clicked.connect(self._connect)
        hb = QtWidgets.QHBoxLayout()
        hb.addWidget(self.host)
        hb.addWidget(self.btn_connect)
        form.addRow("Host", hb)

        self.mode_trig = QtWidgets.QRadioButton("Trigger (IN2 gate)")
        self.mode_cont = QtWidgets.QRadioButton("Continuous")
        self.mode_trig.setChecked(True)
        mb = QtWidgets.QHBoxLayout()
        mb.addWidget(self.mode_trig)
        mb.addWidget(self.mode_cont)
        form.addRow("Mode", mb)

        self.f = QtWidgets.QDoubleSpinBox(decimals=4, maximum=62.5e6, singleStep=1.0)
        self.f.setValue(F_EXACT)
        form.addRow("f_demod (Hz)", self.f)
        self.bw = QtWidgets.QComboBox()
        form.addRow("IQ low-pass (Hz)", self.bw)
        self.qf = QtWidgets.QDoubleSpinBox(decimals=1, minimum=0.1, maximum=10000, value=1.5)
        form.addRow("quadrature_factor", self.qf)
        self.phase = QtWidgets.QDoubleSpinBox(decimals=1, minimum=-180, maximum=180)
        form.addRow("phase offset (deg)", self.phase)
        self.win = QtWidgets.QComboBox()
        form.addRow("window", self.win)
        self.dac = QtWidgets.QCheckBox("I -> OUT1, Q -> OUT2")
        self.dac.setChecked(True)
        form.addRow("DAC", self.dac)

        self.in2range = QtWidgets.QComboBox()
        self.in2range.addItems(["HV (+-20 V)", "LV (+-1 V)"])
        form.addRow("IN2 range (jumper)", self.in2range)
        self.thr = QtWidgets.QDoubleSpinBox(decimals=2, minimum=-19, maximum=19, value=1.5)
        form.addRow("gate threshold (V)", self.thr)
        self.hyst = QtWidgets.QDoubleSpinBox(decimals=2, minimum=0.0, maximum=5, value=0.2)
        form.addRow("hysteresis (V)", self.hyst)

        self.detrend = QtWidgets.QCheckBox("remove mean frequency from phase")
        form.addRow("", self.detrend)
        self.yfs = QtWidgets.QSpinBox(minimum=100, maximum=2000, singleStep=100, value=1000)
        self.yfs.setSuffix(" mV")
        self.yfs.setToolTip("fixed range: I, Q in +-value, |A| in 0..1.5 x value; grid 100 mV")
        self.btn_yrange = QtWidgets.QPushButton("Y range: fixed")
        self.btn_yrange.setCheckable(True)
        self.btn_yrange.setChecked(True)
        self.btn_yrange.toggled.connect(self._set_yrange)
        self.yfs.valueChanged.connect(lambda _: self._set_yrange(self.btn_yrange.isChecked()))
        yb = QtWidgets.QHBoxLayout()
        yb.addWidget(self.yfs)
        yb.addWidget(self.btn_yrange)
        form.addRow("I, Q, |A| range", yb)
        self.btn_apply = QtWidgets.QPushButton("Apply settings")
        self.btn_run = QtWidgets.QPushButton("Start")
        self.btn_save = QtWidgets.QPushButton("Save last trace")
        for b in (self.btn_apply, self.btn_run, self.btn_save):
            b.setEnabled(False)
        self.btn_apply.clicked.connect(self._apply)
        self.btn_run.clicked.connect(self._toggle)
        self.btn_save.clicked.connect(self._save)
        form.addRow(self.btn_apply)
        form.addRow(self.btn_run)
        form.addRow(self.btn_save)
        self.status = QtWidgets.QLabel("not connected")
        self.status.setWordWrap(True)
        self.status.setMinimumWidth(300)
        form.addRow(self.status)

        left = QtWidgets.QWidget()
        left.setLayout(form)
        left.setMaximumWidth(380)

        self.glw = pg.GraphicsLayoutWidget()
        self.p_amp = self.glw.addPlot(row=0, col=0, title="amplitude |I + iQ|")
        self.p_ph = self.glw.addPlot(row=1, col=0, title="phase")
        self.p_iq = self.glw.addPlot(row=2, col=0, title="I and Q")
        for p, lab in ((self.p_amp, "|A| (mV, iq units)"), (self.p_ph, "phase (rad)"),
                       (self.p_iq, "I, Q (mV, iq units)")):
            p.showGrid(x=True, y=True, alpha=0.25)
            p.setLabel("left", lab)
            p.setLabel("bottom", "time since gate start (ms)")
            p.getAxis("left").enableAutoSIPrefix(False)    # keep plain mV, no "(x0.001)"
            p.getAxis("bottom").enableAutoSIPrefix(False)
        self.p_ph.setXLink(self.p_amp)
        self.p_iq.setXLink(self.p_amp)
        self.c_amp = self.p_amp.plot(pen=pg.mkPen("#2a78d6", width=2))
        self.c_ph = self.p_ph.plot(pen=pg.mkPen("#52514e", width=2))
        self.p_iq.addLegend(offset=(10, 5))
        self.c_i = self.p_iq.plot(pen=pg.mkPen("#2a78d6", width=1.5), name="I")
        self.c_q = self.p_iq.plot(pen=pg.mkPen("#eb6834", width=1.5), name="Q")

        lay = QtWidgets.QHBoxLayout(self)
        lay.addWidget(left)
        lay.addWidget(self.glw, stretch=1)
        self.resize(1300, 820)
        self._set_yrange(True)

    def _set_yrange(self, fixed):
        """Fixed: I, Q in +-yfs mV and |A| in 0..1.5*yfs mV with a 100 mV grid; else autoscale."""
        self.btn_yrange.setText("Y range: fixed" if fixed else "Y range: auto")
        fs = self.yfs.value()
        for p, lo, hi in ((self.p_iq, -fs, fs), (self.p_amp, 0, 1.5 * fs)):
            ax = p.getAxis("left")
            if fixed:
                p.enableAutoRange(axis="y", enable=False)
                p.setYRange(lo, hi, padding=0)
                major = 500 if fs >= 1000 else 100
                ax.setTickSpacing(major=major, minor=100)
            else:
                ax.setTickSpacing()
                p.enableAutoRange(axis="y", enable=True)

    def _say(self, txt):
        self.status.setText(txt)
        QtWidgets.QApplication.processEvents()

    # ---------------- actions ----------------
    def _connect(self):
        self.timer.stop()
        self.state = "idle"
        self._say(f"connecting to {self.host.text()} (re-flashes the FPGA, ~20 s) ...")
        try:
            self.be = Backend(self.host.text().strip())
        except Exception as e:                        # noqa: BLE001
            self._say(f"connect failed: {e}")
            return
        self.bw.clear()
        for b in self.be.bandwidths():
            self.bw.addItem(f"{b:,.0f}", b)
        self.bw.setCurrentIndex(int(np.argmin([abs(self.bw.itemData(i) - 1e5)
                                               for i in range(self.bw.count())])))
        self.win.clear()
        for d in self.be.durations():
            self.win.addItem(f"{d*1e3:.3f} ms  (dt {d/16384*1e6:.2f} us)", d)
        self.win.setCurrentIndex(int(np.argmin([abs(self.win.itemData(i) - 0.0336)
                                                for i in range(self.win.count())])))
        for b in (self.btn_apply, self.btn_run, self.btn_save):
            b.setEnabled(True)
        self._apply()

    def _scale(self):
        return 20.0 if self.in2range.currentIndex() == 0 else 1.0

    def _apply(self):
        if self.be is None:
            return
        try:
            self.be.configure(self.f.value(), self.bw.currentData(), self.qf.value(),
                              self.phase.value(), self.dac.isChecked())
            self.window = self.be.set_window(self.win.currentData())
            sc = self._scale()
            thr, hy = self.thr.value() / sc, self.hyst.value() / sc
            if abs(thr) > 0.95:
                self._say("threshold outside the IN2 range: check 'IN2 range' / jumper")
                return
            self.be.setup_trigger(thr, hy)
        except Exception as e:                        # noqa: BLE001
            self._say(f"apply failed: {e}")
            return
        bw = self.be.iq.bandwidth[0]
        self._say(f"applied: f={self.be.iq.frequency:.4f} Hz, low-pass {bw:,.0f} Hz, "
                  f"qf={self.be.iq.quadrature_factor:g}, window {self.window*1e3:.3f} ms")
        if self.state != "idle":
            self.state = "rearm"

    def _toggle(self):
        if self.state == "idle":
            self.state = "rearm"
            self.btn_run.setText("Stop")
            self.timer.start(30)
        else:
            self.timer.stop()
            self.state = "idle"
            self.btn_run.setText("Start")
            self._say("stopped")

    # ---------------- acquisition state machine ----------------
    def _tick(self):
        try:
            self._step()
        except Exception as e:                        # noqa: BLE001
            self._say(f"error: {e} (network?) - retrying")
            self.state = "rearm"
            time.sleep(0.2)

    def _step(self):
        trig = self.mode_trig.isChecked()
        if self.state == "rearm":
            if trig:
                v = self.be.in2() * self._scale()
                if v > self.thr.value() - self.hyst.value():
                    self._say(f"waiting for IN2 to go low (IN2 = {v:.2f} V) ...")
                    return
            self.be.arm(trig)
            self.t_arm = time.time()
            self.state = "armed"
            if trig:
                self._say(f"armed, waiting for gate on IN2 ...  (acquisitions: {self.n_acq})")
            return
        if self.state == "armed":
            if trig:
                self.be.poll_edge()
            if not self.be.ready():
                return
            t, I, Q, gate = self.be.read(trig)
            self._show(t, I, Q, gate, trig)
            self.state = "rearm"

    def _show(self, t, I, Q, gate, trig):
        self.n_acq += 1
        note = ""
        n = len(t)
        if trig:
            if gate is None or gate >= self.window:
                note = "gate longer than window (showing full window)"
                gate_txt = f">= {self.window*1e3:.3f} ms"
            else:
                n = max(int(np.searchsorted(t, gate)), 2)
                gate_txt = f"{gate*1e3:.4f} ms"
        else:
            gate_txt = "continuous"
        t, I, Q = t[:n], I[:n], Q[:n]
        z = I + 1j * Q
        A = np.abs(z)
        ph = np.unwrap(np.angle(z))
        rate = np.polyfit(t, ph, 1)[0] / (2 * np.pi) if n > 2 else np.nan
        if self.detrend.isChecked() and n > 2:
            ph = ph - np.polyval(np.polyfit(t, ph, 1), t)
        tm = t * 1e3
        self.c_amp.setData(tm, A * 1e3)      # plots in mV
        self.c_ph.setData(tm, ph)
        self.c_i.setData(tm, I * 1e3)
        self.c_q.setData(tm, Q * 1e3)
        sat = max(np.abs(I).max(), np.abs(Q).max()) > 0.98
        self.last = dict(t=t, I=I, Q=Q, gate=gate_txt, mode="trigger" if trig else "continuous",
                         f_demod=self.be.iq.frequency, bandwidth=self.be.iq.bandwidth[0],
                         quadrature_factor=self.be.iq.quadrature_factor,
                         phase_offset=self.be.iq.phase, wallclock=time.time())
        self._say(f"#{self.n_acq}  gate: {gate_txt}   |A| mean {A.mean()*1e3:.0f} mV, max {A.max()*1e3:.0f} mV   "
                  f"mean rotation {rate:+.1f} Hz" + ("   SATURATED: lower quadrature_factor" if sat else "")
                  + (f"   [{note}]" if note else ""))

    def _save(self):
        if self.last is None:
            self._say("nothing to save yet")
            return
        os.makedirs(SAVE_DIR, exist_ok=True)
        fn = os.path.join(SAVE_DIR, time.strftime("iq_%Y%m%d_%H%M%S.npz"))
        np.savez(fn, **self.last)
        self._say(f"saved {fn}")


if __name__ == "__main__":
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    w = Window()
    w.show()
    app.exec_()
