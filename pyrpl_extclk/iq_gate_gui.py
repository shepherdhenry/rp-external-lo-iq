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

All Red Pitaya I/O runs in a worker thread (Worker), so the window stays responsive while
traces are read back over the network; the GUI thread only draws.

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

from qtpy import QtCore, QtGui, QtWidgets     # noqa: E402

# high-DPI scaling has to be set before the QApplication exists (importing pyrpl creates one)
if QtWidgets.QApplication.instance() is None:
    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True)
    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps, True)
    try:
        QtGui.QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            QtCore.Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    except (AttributeError, TypeError):
        pass

from pyrpl import Pyrpl                       # noqa: E402
import pyqtgraph as pg                        # noqa: E402

F_EXACT = 687194768 * 125e6 / 2**32           # 20 000 000.0186 Hz (exact with the external clock)
TS_OFFSET = 40                                # scope trigger timestamp lags the trig edge by ~40 cycles
CLK = 125e6
SAVE_DIR = os.path.join(_here, "iq_gate_data")

BLUE, ORANGE, INK, MUTED = "#2a78d6", "#eb6834", "#2b2a28", "#6b6964"
GREEN, RED, AMBER = "#1f9d55", "#d64545", "#d99a1e"


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


class Worker(QtCore.QObject):
    """Owns the Backend and does all Red Pitaya I/O in its own thread. One acquisition step per
    timer tick; requests from the GUI arrive as queued signals between ticks."""
    connected = QtCore.Signal(object)         # dict(backend, bandwidths, durations)
    failed = QtCore.Signal(str)
    applied = QtCore.Signal(object)           # settings read back from the FPGA
    data = QtCore.Signal(object)              # (t, I, Q, gate, trig)
    status = QtCore.Signal(str)

    def __init__(self):
        super().__init__()
        self.be = None
        self.state = "idle"
        self.trig = True
        self.thr, self.hyst, self.scale = 1.5, 0.2, 20.0
        self.n_acq = 0
        self.t_status = 0.0
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(5)
        self.timer.timeout.connect(self._tick)

    def _status(self, txt, throttle=False):
        """Repeated 'waiting ...' messages are sent at most 5 times per second."""
        now = time.time()
        if throttle and now - self.t_status < 0.2:
            return
        self.t_status = now
        self.status.emit(txt)

    @QtCore.Slot(str)
    def connect_rp(self, host):
        self.timer.stop()
        self.state = "idle"
        try:
            self.be = Backend(host)
            self.connected.emit(dict(backend=self.be, bandwidths=self.be.bandwidths(),
                                     durations=self.be.durations()))
        except Exception as e:                        # noqa: BLE001
            self.be = None
            self.failed.emit(str(e))

    @QtCore.Slot(object)
    def apply(self, c):
        if self.be is None:
            return
        be = self.be
        try:
            be.configure(c["f"], c["bw"], c["qf"], c["phase"], c["dac"])
            window = be.set_window(c["window"])
            be.setup_trigger(c["thr"] / c["scale"], c["hyst"] / c["scale"])
            iq = be.iq
            meta = dict(f_demod=iq.frequency, bandwidth=iq.bandwidth[0],
                        quadrature_factor=iq.quadrature_factor, phase_offset=iq.phase,
                        window=window)
        except Exception as e:                        # noqa: BLE001
            self._status(f"apply failed: {e}")
            return
        self.thr, self.hyst, self.scale = c["thr"], c["hyst"], c["scale"]
        self.applied.emit(meta)
        if self.state != "idle":
            self.state = "rearm"

    @QtCore.Slot(bool)
    def start(self, trig):
        if self.be is None:
            return
        self.trig = trig
        self.state = "rearm"
        self.timer.start()

    @QtCore.Slot()
    def stop(self):
        self.timer.stop()
        self.state = "idle"

    @QtCore.Slot(bool)
    def set_mode(self, trig):
        self.trig = trig
        if self.state != "idle":
            self.state = "rearm"

    @QtCore.Slot()          # decorated, or PyQt runs it in the thread that created the Worker
    def _tick(self):
        try:
            self._step()
        except Exception as e:                        # noqa: BLE001
            self._status(f"error: {e} (network?) - retrying")
            self.state = "rearm"
            self.timer.stop()
            QtCore.QTimer.singleShot(200, self._resume)

    @QtCore.Slot()
    def _resume(self):
        if self.state != "idle":
            self.timer.start()

    def _step(self):
        be, trig = self.be, self.trig
        if self.state == "rearm":
            if trig:
                v = be.in2() * self.scale
                if v > self.thr - self.hyst:
                    self._status(f"waiting for IN2 to go low (IN2 = {v:.2f} V) ...", throttle=True)
                    return
            be.arm(trig)
            self.state = "armed"
            if trig:
                self._status(f"armed, waiting for gate on IN2 ...  (acquisitions: {self.n_acq})")
            return
        if self.state == "armed":
            if trig:
                be.poll_edge()
            if not be.ready():
                return
            t, I, Q, gate = be.read(trig)
            self.n_acq += 1
            self.data.emit((t, I, Q, gate, trig))
            self.state = "rearm"


QSS = f"""
#root, #panel {{ background: #f4f3f0; }}
QWidget {{ font-family: "Segoe UI"; font-size: 10pt; color: {INK}; }}
QScrollArea {{ border: none; background: transparent; }}
QGroupBox {{ background: #ffffff; border: 1px solid #e2e0db; border-radius: 8px;
             margin-top: 12px; padding: 8px 10px 6px 10px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px;
                    color: {MUTED}; font-weight: 600; }}
QLineEdit, QDoubleSpinBox, QSpinBox, QComboBox {{
    background: #ffffff; border: 1px solid #d6d3cc; border-radius: 5px;
    padding: 2px 6px; min-height: 20px; }}
QLineEdit:focus, QDoubleSpinBox:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {BLUE}; }}
QPushButton {{ background: #ffffff; border: 1px solid #d6d3cc; border-radius: 6px;
               padding: 4px 12px; min-height: 20px; }}
QPushButton:hover {{ background: #f0efeb; }}
QPushButton:disabled {{ color: #a9a7a1; background: #f4f3f0; border-color: #e2e0db; }}
QPushButton:checked {{ background: {BLUE}; color: #ffffff; border-color: {BLUE}; }}
QPushButton#seg_left {{ border-top-right-radius: 0; border-bottom-right-radius: 0; }}
QPushButton#seg_right {{ border-top-left-radius: 0; border-bottom-left-radius: 0; border-left: none; }}
QPushButton#run {{ background: {GREEN}; color: #ffffff; border: none; font-size: 12pt;
                   font-weight: 600; min-height: 38px; }}
QPushButton#run:hover {{ background: #188a4a; }}
QPushButton#run[running="true"] {{ background: {RED}; }}
QPushButton#run[running="true"]:hover {{ background: #bf3a3a; }}
QPushButton#run:disabled {{ background: #cfe6d8; color: #ffffff; }}
QLabel#chip {{ background: #ffffff; border: 1px solid #e2e0db; border-radius: 8px;
               padding: 6px 12px; }}
QLabel#chip[alert="true"] {{ background: #fdecec; border-color: {RED}; }}
QLabel#status {{ color: {MUTED}; font-size: 9pt; }}
"""


class Window(QtWidgets.QWidget):
    connected = QtCore.Signal()               # emitted once the RP is connected and configured
    _req_connect = QtCore.Signal(str)
    _req_apply = QtCore.Signal(object)
    _req_start = QtCore.Signal(bool)
    _req_stop = QtCore.Signal()
    _req_mode = QtCore.Signal(bool)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Red Pitaya IQ gate viewer")
        self.be = None
        self.state = "idle"
        self.last = None
        self.n_acq = 0
        self.window = 0.0336
        self.meta = {}
        self.t_last, self.rate = None, np.nan
        self._build()

        self.worker = Worker()
        self.io_thread = QtCore.QThread()
        self.worker.moveToThread(self.io_thread)
        self._req_connect.connect(self.worker.connect_rp)
        self._req_apply.connect(self.worker.apply)
        self._req_start.connect(self.worker.start)
        self._req_stop.connect(self.worker.stop)
        self._req_mode.connect(self.worker.set_mode)
        self.worker.connected.connect(self._on_connected)
        self.worker.failed.connect(self._on_failed)
        self.worker.applied.connect(self._on_applied)
        self.worker.data.connect(self._on_data)
        self.worker.status.connect(self._on_status)
        self.io_thread.start()

    # ---------------- UI ----------------
    def _build(self):
        QtWidgets.QApplication.instance().setStyle("Fusion")
        self.setObjectName("root")
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        self.setStyleSheet(QSS)
        pg.setConfigOptions(antialias=False, background="#ffffff", foreground="#52514e")

        panel = QtWidgets.QWidget(objectName="panel")
        pl = QtWidgets.QVBoxLayout(panel)
        pl.setContentsMargins(12, 10, 12, 12)
        pl.setSpacing(6)

        def group(title):
            gb = QtWidgets.QGroupBox(title)
            f = QtWidgets.QFormLayout(gb)
            f.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
            f.setLabelAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
            f.setHorizontalSpacing(12)
            f.setVerticalSpacing(5)
            pl.addWidget(gb)
            return f

        def row(*widgets, spacing=6):
            h = QtWidgets.QHBoxLayout()
            h.setSpacing(spacing)
            for w_ in widgets:
                h.addWidget(w_)
            return h

        # connection
        f = group("Connection")
        self.host = QtWidgets.QLineEdit("192.168.1.186")
        self.btn_connect = QtWidgets.QPushButton("Connect")
        self.btn_connect.clicked.connect(self._connect)
        f.addRow("Host", row(self.host, self.btn_connect))
        self.conn = QtWidgets.QLabel()
        f.addRow(self.conn)
        self._set_conn("off", "not connected")

        # acquisition
        f = group("Acquisition")
        self.mode_trig = QtWidgets.QPushButton("Trigger (IN2)", objectName="seg_left", checkable=True)
        self.mode_cont = QtWidgets.QPushButton("Continuous", objectName="seg_right", checkable=True)
        self.mode_group = QtWidgets.QButtonGroup(self)
        self.mode_group.setExclusive(True)
        for b in (self.mode_trig, self.mode_cont):
            self.mode_group.addButton(b)
        self.mode_trig.setChecked(True)
        self.mode_trig.toggled.connect(self._mode_changed)
        f.addRow("Mode", row(self.mode_trig, self.mode_cont, spacing=0))
        self.win = QtWidgets.QComboBox()
        f.addRow("Window", self.win)

        # demodulation
        f = group("Demodulation")
        self.f = QtWidgets.QDoubleSpinBox(decimals=4, maximum=62.5e6, singleStep=1.0)
        self.f.setValue(F_EXACT)
        self.f.setSuffix(" Hz")
        f.addRow("f_demod", self.f)
        self.bw = QtWidgets.QComboBox()
        f.addRow("IQ low-pass", self.bw)
        self.qf = QtWidgets.QDoubleSpinBox(decimals=1, minimum=0.1, maximum=10000, value=1.5)
        f.addRow("Quadrature factor", self.qf)
        self.phase = QtWidgets.QDoubleSpinBox(decimals=1, minimum=-180, maximum=180)
        self.phase.setSuffix(" deg")
        f.addRow("Phase offset", self.phase)
        self.dac = QtWidgets.QCheckBox("I -> OUT1, Q -> OUT2")
        self.dac.setChecked(True)
        f.addRow("DAC", self.dac)

        # trigger
        f = group("Trigger (IN2)")
        self.in2range = QtWidgets.QComboBox()
        self.in2range.addItems(["HV (+-20 V)", "LV (+-1 V)"])
        f.addRow("Range (jumper)", self.in2range)
        self.thr = QtWidgets.QDoubleSpinBox(decimals=2, minimum=-19, maximum=19, value=1.5)
        self.thr.setSuffix(" V")
        f.addRow("Threshold", self.thr)
        self.hyst = QtWidgets.QDoubleSpinBox(decimals=2, minimum=0.0, maximum=5, value=0.2)
        self.hyst.setSuffix(" V")
        f.addRow("Hysteresis", self.hyst)

        # display
        f = group("Display")
        self.yfs = QtWidgets.QSpinBox(minimum=100, maximum=2000, singleStep=100, value=1000)
        self.yfs.setSuffix(" mV")
        self.yfs.setToolTip("fixed range: I, Q in +-value, |A| in 0..1.5 x value")
        self.btn_yrange = QtWidgets.QPushButton("Fixed", checkable=True)
        self.btn_yrange.setChecked(True)
        self.btn_yrange.setToolTip("fixed Y range or autoscale")
        self.btn_yrange.toggled.connect(self._set_yrange)
        self.yfs.valueChanged.connect(lambda _: self._set_yrange(self.btn_yrange.isChecked()))
        f.addRow("Y range", row(self.yfs, self.btn_yrange))
        self.detrend = QtWidgets.QCheckBox("Remove mean frequency from phase")
        f.addRow(self.detrend)

        # actions
        pl.addSpacing(4)
        self.btn_apply = QtWidgets.QPushButton("Apply settings")
        self.btn_run = QtWidgets.QPushButton("Start", objectName="run")
        self.btn_save = QtWidgets.QPushButton("Save last trace")
        for b in (self.btn_apply, self.btn_run, self.btn_save):
            b.setEnabled(False)
        self.btn_apply.clicked.connect(self._apply)
        self.btn_run.clicked.connect(self._toggle)
        self.btn_save.clicked.connect(self._save)
        pl.addWidget(self.btn_run)
        pl.addLayout(row(self.btn_apply, self.btn_save))
        self.status = QtWidgets.QLabel("", objectName="status")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        pl.addStretch(1)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidget(panel)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        scroll.setFixedWidth(max(panel.sizeHint().width(), 360) + 16)

        # readouts
        self.chips = {}
        chips = QtWidgets.QHBoxLayout()
        chips.setSpacing(8)
        for key, cap in (("gate", "Gate"), ("amean", "|A| mean"), ("amax", "|A| max"),
                         ("rot", "Mean rotation"), ("nacq", "Acquisitions"), ("rate", "Updates")):
            lab = QtWidgets.QLabel(objectName="chip")
            lab.setMinimumWidth(110)
            chips.addWidget(lab, stretch=1)
            self.chips[key] = (lab, cap)
            self._set_chip(key, "-")

        # plots
        self.glw = pg.GraphicsLayoutWidget()
        self.glw.ci.setSpacing(4)
        tick_font = QtGui.QFont("Segoe UI", 9)
        self.p_amp = self.glw.addPlot(row=0, col=0)
        self.p_ph = self.glw.addPlot(row=1, col=0)
        self.p_iq = self.glw.addPlot(row=2, col=0)
        for p, title, lab in ((self.p_amp, "Amplitude |I + iQ|", "|A| (mV)"),
                              (self.p_ph, "Phase", "phase (rad)"),
                              (self.p_iq, "I and Q", "I, Q (mV)")):
            p.setTitle(title, size="11pt", color=INK)
            p.showGrid(x=True, y=True)
            # grid alpha 0.25; set as int since pyqtgraph 0.11 passes a float to the slider
            p.ctrl.gridAlphaSlider.setValue(int(0.25 * p.ctrl.gridAlphaSlider.maximum()))
            p.setLabel("left", lab, **{"font-size": "10pt"})
            p.setDownsampling(auto=True, mode="peak")      # draw ~1 point per pixel, keep spikes
            p.setClipToView(True)
            p.setMenuEnabled(False)
            for name in ("left", "bottom"):
                ax = p.getAxis(name)
                ax.setStyle(tickFont=tick_font)
                ax.enableAutoSIPrefix(False)               # keep plain mV, no "(x0.001)"
            p.getAxis("left").setWidth(64)                 # aligns the three plots
        for p in (self.p_amp, self.p_ph):
            p.getAxis("bottom").setStyle(showValues=False)
            p.setXLink(self.p_iq)
        self.p_iq.setLabel("bottom", "time since gate start (ms)", **{"font-size": "10pt"})
        self.c_amp = self.p_amp.plot(pen=pg.mkPen(BLUE, width=1))
        self.c_ph = self.p_ph.plot(pen=pg.mkPen(INK, width=1))
        self.p_iq.addLegend(offset=(10, 5))
        self.c_i = self.p_iq.plot(pen=pg.mkPen(BLUE, width=1), name="I")
        self.c_q = self.p_iq.plot(pen=pg.mkPen(ORANGE, width=1), name="Q")

        plots = QtWidgets.QFrame()
        plots.setStyleSheet("QFrame { background: #ffffff; border: 1px solid #e2e0db; border-radius: 8px; }")
        pv = QtWidgets.QVBoxLayout(plots)
        pv.setContentsMargins(6, 6, 6, 6)
        pv.addWidget(self.glw)

        right = QtWidgets.QVBoxLayout()
        right.setContentsMargins(0, 12, 12, 12)
        right.setSpacing(8)
        right.addLayout(chips)
        right.addWidget(plots, stretch=1)
        right.addWidget(self.status)

        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(scroll)
        lay.addLayout(right, stretch=1)
        for sb in self.findChildren(QtWidgets.QAbstractSpinBox):
            # type the value or use the mouse wheel / arrow keys (styled arrows render badly)
            sb.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        self.resize(1440, 900)
        self._set_yrange(True)

    @staticmethod
    def _repolish(w):
        w.style().unpolish(w)
        w.style().polish(w)

    def _set_conn(self, state, txt):
        color = {"off": "#a9a7a1", "busy": AMBER, "ok": GREEN, "err": RED}[state]
        self.conn.setText(f"<span style='color:{color}; font-size:12pt'>&#9679;</span>&nbsp; {txt}")

    def _set_chip(self, key, value, alert=False, caption=None):
        lab, cap = self.chips[key]
        lab.setText(f"<span style='color:{RED if alert else MUTED}; font-size:9pt'>{caption or cap}</span><br>"
                    f"<span style='font-size:14pt; font-weight:600'>{value}</span>")
        if lab.property("alert") != alert:
            lab.setProperty("alert", alert)
            self._repolish(lab)

    def _set_running(self, running):
        self.btn_run.setText("Stop" if running else "Start")
        self.btn_run.setProperty("running", running)
        self._repolish(self.btn_run)

    def _set_yrange(self, fixed):
        """Fixed: I, Q in +-yfs mV and |A| in 0..1.5*yfs mV; else autoscale."""
        self.btn_yrange.setText("Fixed" if fixed else "Auto")
        fs = self.yfs.value()
        for p, lo, hi in ((self.p_iq, -fs, fs), (self.p_amp, 0, 1.5 * fs)):
            ax = p.getAxis("left")
            if fixed:
                p.enableAutoRange(axis="y", enable=False)
                p.setYRange(lo, hi, padding=0.03)    # small margin so the end tick labels are not cut
                major = 500 if fs >= 1000 else 100
                ax.setTickSpacing(major=major, minor=100)
            else:
                ax.setTickSpacing()
                p.enableAutoRange(axis="y", enable=True)

    def _say(self, txt):
        self.status.setText(txt)

    # ---------------- actions ----------------
    def _connect(self):
        host = self.host.text().strip()
        self._req_stop.emit()
        self.state = "idle"
        self._set_running(False)
        for b in (self.btn_connect, self.btn_apply, self.btn_run, self.btn_save):
            b.setEnabled(False)
        self._set_conn("busy", f"connecting to {host} ...")
        self._say("connecting (re-flashes the FPGA, ~20 s) ...")
        self._req_connect.emit(host)

    def _on_connected(self, info):
        self.be = info["backend"]
        for combo, items, target, fmt in (
                (self.bw, info["bandwidths"], 1e5, lambda b: f"{b:,.0f} Hz"),
                (self.win, info["durations"], 0.0336,
                 lambda d: f"{d*1e3:.3f} ms  (dt {d/16384*1e6:.2f} us)")):
            combo.blockSignals(True)
            combo.clear()
            for v in items:
                combo.addItem(fmt(v), v)
            combo.setCurrentIndex(int(np.argmin([abs(v - target) for v in items])))
            combo.blockSignals(False)
        for b in (self.btn_connect, self.btn_apply, self.btn_run, self.btn_save):
            b.setEnabled(True)
        self._set_conn("ok", f"connected to {self.host.text().strip()}")
        self._apply()
        self.connected.emit()

    def _on_failed(self, msg):
        self.be = None
        self.btn_connect.setEnabled(True)
        self._set_conn("err", "not connected")
        self._say(f"connect failed: {msg}")

    def _scale(self):
        return 20.0 if self.in2range.currentIndex() == 0 else 1.0

    def _apply(self):
        if self.be is None:
            return
        sc = self._scale()
        if abs(self.thr.value() / sc) > 0.95:
            self._say("threshold outside the IN2 range: check 'Range (jumper)'")
            return
        self._req_apply.emit(dict(f=self.f.value(), bw=self.bw.currentData(), qf=self.qf.value(),
                                  phase=self.phase.value(), dac=self.dac.isChecked(),
                                  window=self.win.currentData(), thr=self.thr.value(),
                                  hyst=self.hyst.value(), scale=sc))
        self._say("applying settings ...")

    def _on_applied(self, meta):
        self.meta = meta
        self.window = meta["window"]
        self._say(f"applied: f = {meta['f_demod']:.4f} Hz, low-pass {meta['bandwidth']:,.0f} Hz, "
                  f"qf = {meta['quadrature_factor']:g}, window {self.window*1e3:.3f} ms")

    def _toggle(self):
        if self.state == "idle":
            self.state = "running"
            self.t_last, self.rate = None, np.nan
            self._req_start.emit(self.mode_trig.isChecked())
            self._set_running(True)
        else:
            self._req_stop.emit()
            self.state = "idle"
            self._set_running(False)
            self._say("stopped")

    def _mode_changed(self, _):
        if self.state != "idle":
            self.t_last, self.rate = None, np.nan
            self._req_mode.emit(self.mode_trig.isChecked())

    def _on_status(self, txt):
        self._say(txt)

    def _on_data(self, d):
        if self.state == "idle":                      # trace that arrived after Stop
            return
        self._show(*d)

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

        now = time.time()
        if self.t_last is not None:
            r = 1.0 / max(now - self.t_last, 1e-3)
            self.rate = r if np.isnan(self.rate) else 0.7 * self.rate + 0.3 * r
        self.t_last = now
        self._set_chip("gate", "continuous" if not trig else
                       (f">= {self.window*1e3:.1f} ms" if note else f"{gate*1e3:.3f} ms"))
        self._set_chip("amean", f"{A.mean()*1e3:.0f} mV")
        self._set_chip("amax", f"{A.max()*1e3:.0f} mV", alert=sat,
                       caption="|A| max  SATURATED" if sat else None)
        self._set_chip("rot", f"{rate:+.1f} Hz")
        self._set_chip("nacq", f"{self.n_acq}")
        self._set_chip("rate", "-" if np.isnan(self.rate) else f"{self.rate:.1f} /s")

        m = self.meta
        self.last = dict(t=t, I=I, Q=Q, gate=gate_txt, mode="trigger" if trig else "continuous",
                         f_demod=m.get("f_demod", np.nan), bandwidth=m.get("bandwidth", np.nan),
                         quadrature_factor=m.get("quadrature_factor", np.nan),
                         phase_offset=m.get("phase_offset", np.nan), wallclock=now)
        self._say(f"#{self.n_acq}  gate: {gate_txt}   |A| mean {A.mean()*1e3:.0f} mV, "
                  f"max {A.max()*1e3:.0f} mV   rotation {rate:+.1f} Hz" + ("   SATURATED: lower the quadrature factor" if sat else "")
                  + (f"   [{note}]" if note else ""))

    def _save(self):
        if self.last is None:
            self._say("nothing to save yet")
            return
        os.makedirs(SAVE_DIR, exist_ok=True)
        fn = os.path.join(SAVE_DIR, time.strftime("iq_%Y%m%d_%H%M%S.npz"))
        np.savez(fn, **self.last)
        self._say(f"saved {fn}")

    def closeEvent(self, ev):
        self._req_stop.emit()
        self.io_thread.quit()
        self.io_thread.wait(3000)
        super().closeEvent(ev)


if __name__ == "__main__":
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    w = Window()
    w.show()
    app.exec_()
