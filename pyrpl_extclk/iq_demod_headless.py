r"""
Headless 20 MHz IQ demodulation on the Red Pitaya with PyRPL (no GUI).

Configures the FPGA once, checks the I/Q levels with the scope, then exits.
The FPGA keeps demodulating after the script ends (and if the network drops):
  IN1  -> iq2 (NCO at F_DEMOD) -> I on OUT1, Q on OUT2   (real time, latency < 1 us)

Run:  python iq_demod_headless.py

Login uses the SSH key ~/.ssh/id_ed25519 (PyRPL itself only supports passwords).
Connecting re-flashes the FPGA: anything else running on this RP (PID, apps) is reset.
"""
import os
import sys

# a local "pyrpl" folder would shadow the installed package
_here = os.path.dirname(os.path.abspath(__file__))
sys.path = [q for q in sys.path if os.path.abspath(q or ".") != _here]

import numpy as np
import paramiko

HOST          = "192.168.1.186"   # rp-f0f9ad over WiFi; 192.168.137.2 over the direct cable
F_DEMOD       = 687194768 * 125e6 / 2**32   # 20 000 000.0186 Hz, exact with the external clock
BANDWIDTH     = 100e3     # Hz, I/Q low-pass (2 poles), snaps to allowed values
ACBANDWIDTH   = 50e3      # Hz, input high-pass (removes DC offset)
QUAD_FACTOR   = 1.5       # digital I/Q gain; lower it if |I + iQ| approaches 1 V (saturation)
PHASE         = 0.0       # deg, rotates the I/Q frame
KEY_FILE      = os.path.expanduser("~/.ssh/id_ed25519")

# --- make PyRPL's paramiko login use the SSH key instead of a password ----------------
_connect = paramiko.SSHClient.connect


def _connect_with_key(self, hostname, *args, **kw):
    kw.pop("password", None)
    kw.update(key_filename=KEY_FILE, look_for_keys=False, allow_agent=False)
    return _connect(self, hostname, *args, **kw)


paramiko.SSHClient.connect = _connect_with_key

from pyrpl import Pyrpl  # noqa: E402  (after the patch)

# RP OS 2.x overlay.sh always loads /opt/pyrpl/fpga.bit.bin, whatever path it is given
p = Pyrpl(config="iq_demod_headless", hostname=HOST, gui=False, serverbinfilename="fpga.bit.bin")
rp = p.rp

iq = rp.iq2   # only iq2 exposes both quadratures (iq2 = I, iq2_2 = Q)
iq.setup(frequency=F_DEMOD, bandwidth=[BANDWIDTH, BANDWIDTH], gain=0.0, phase=PHASE,
         acbandwidth=ACBANDWIDTH, amplitude=0.0, input="in1", output_direct="off",
         output_signal="quadrature", quadrature_factor=QUAD_FACTOR)

# unity-gain PIDs as pass-through to the DACs
for pid, src, out in ((rp.pid0, "iq2", "out1"), (rp.pid1, "iq2_2", "out2")):
    pid.setup(input=src, output_direct=out, setpoint=0, p=1.0, i=0, inputfilter=[])
    pid.ival = 0

print(f"iq2: f={iq.frequency:.4f} Hz  bandwidth={iq.bandwidth}  acbw={iq.acbandwidth}  "
      f"qf={iq.quadrature_factor}  phase={iq.phase}")

# --- level check: I and Q (and IN1) with the scope -----------------------------------
s = rp.scope
s.trigger_source = "immediately"
s.rolling_mode = False
s.average = True
for inputs, dur in ((("iq2", "iq2_2"), 0.0084), (("iq2", "iq2_2"), 0.134), (("in1", "iq2"), 8.4e-6)):
    s.input1, s.input2 = inputs
    s.duration = dur
    d = np.array(s.single(timeout=10))
    for name, ch in zip(inputs, d):
        print(f"  {name:6s} over {s.duration*1e3:8.3f} ms: mean {ch.mean():+.3f} V  "
              f"rms {ch.std():.3f} V  min {ch.min():+.3f}  max {ch.max():+.3f}")
    if inputs[0] == "iq2" and dur > 0.1:
        z = d[0] + 1j * d[1]
        ph = np.unwrap(np.angle(z))
        t = s.times
        rate = np.polyfit(t, ph, 1)[0] / (2 * np.pi)
        print(f"  |I+iQ|: mean {np.abs(z).mean():.3f} V, std {np.abs(z).std():.3f} V; "
              f"mean phasor rotation {rate:+.1f} Hz")

print("FPGA configured: OUT1 = I, OUT2 = Q. Leaving it running.")
sys.exit(0)
