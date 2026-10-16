"""
PYNQ driver for the external-LO IQ demodulator overlay (Red Pitaya STEMlab 125-14, Zynq-7010).

Usage on the board (PYNQ image, run as root / with pynq available):

    from iq_demod_driver import IQDemod
    iq = IQDemod("iq_demod.bit")            # iq_demod.hwh must sit next to the .bit
    iq.set_frequency(20e6)                  # external LO frequency (also moves the image notch)
    iq.set_bandwidth(1e6, ref_fc=80e3)      # signal / reference channel per-stage cutoff
    iq.set_gain(128 / 4096)                 # output gain (I/Q full scale 8191)
    iq.select("iq")                         # "iq" | "magphase" | "ch1" | "ch2" | "adc"
    print(iq.status())

All register formulas are copied from model/iq_model.py (kept self-contained so this file can be
scp'ed to the board alone). Keep both in sync if the IP changes.
"""
import math
import time

FS = 125e6
PHASE_BITS = 32
IIR_COEF_BITS = 17
NOTCH_COEF_Q = 14
OUT_GAIN_Q = 12
ROT_Q = 14
DAC_FS = 8191

SEL = {"iq": 0, "magphase": 1, "ch1": 2, "ch2": 3, "adc": 4}

# AXI-Lite register offsets from the HLS driver header (hls/iq_demod_ip.zip: drivers/.../xiq_demod_hw.h).
# Used directly with MMIO read/write: PYNQ's register_map does not expose the HLS output (monitor) registers.
REG_OFFSET = {
    'ftw': 0x10,
    'lpf_m_sig': 0x18,
    'lpf_m_ref': 0x20,
    'notch_en': 0x28,
    'notch_coef': 0x30,
    'out_sel': 0x38,
    'out_gain': 0x40,
    'i_offset': 0x48,
    'q_offset': 0x50,
    'rot_cos': 0x58,
    'rot_sin': 0x60,
    'rst_state': 0x68,
    'mon_shift': 0x70,
    'mon_in1_max': 0x78,
    'mon_in1_min': 0x88,
    'mon_in2_max': 0x98,
    'mon_in2_min': 0xA8,
    'mon_o1_sum': 0xB8,
    'mon_o2_sum': 0xC8,
    'mon_count': 0xD8,
}


def ftw_for(f_hz):
    return int(round(f_hz / FS * (1 << PHASE_BITS))) & 0xFFFFFFFF


def lpf_coef(fc_hz):
    """m = round((1 - a^2) * 2^17), a = exp(-2 pi fc / fs)  (per-stage pole; 2 stages in hardware)."""
    a = math.exp(-2 * math.pi * fc_hz / FS)
    m = int(round((1 - a * a) * (1 << IIR_COEF_BITS)))
    return max(1, min(m, (1 << IIR_COEF_BITS) - 1))


def lpf_cutoff(m):
    """Approximate per-stage -3 dB frequency for a coefficient register value."""
    a = math.sqrt(max(0.0, 1 - m / (1 << IIR_COEF_BITS)))
    if a <= 0:
        return FS / 4
    c = (4 * a - 1 - a * a) / (2 * a)
    return FS / (2 * math.pi) * math.acos(max(-1.0, min(1.0, c)))


def notch_coef(f_lo_hz):
    """c = -2 cos(2 pi * 2 f_LO / fs) in Q14: zeros of the 3-tap FIR at the mixing image 2 f_LO."""
    c = -2.0 * math.cos(2 * math.pi * 2 * f_lo_hz / FS)
    return max(-(1 << 17), min(int(round(c * (1 << NOTCH_COEF_Q))), (1 << 17) - 1))


def notch_dc_gain(f_lo_hz, enabled=True):
    """DC gain of the image notch, (2 + c) / 2. 1.43 at 20 MHz, 1.99 at 30 MHz, 0.12 at 5 MHz."""
    if not enabled:
        return 1.0
    return (2.0 + notch_coef(f_lo_hz) / (1 << NOTCH_COEF_Q)) / 2.0


def _s(v, bits):
    """signed integer -> unsigned register encoding"""
    return int(v) & ((1 << bits) - 1)


class IQDemod:
    def __init__(self, bitfile="iq_demod.bit", ip_name="iq_demod_0", download=True):
        from pynq import Overlay  # imported here so the helpers above work off-board too
        self.overlay = Overlay(bitfile, download=download)
        self.ip = getattr(self.overlay, ip_name)
        self.rm = self.ip.register_map
        self._shadow = {}            # HLS s_axilite inputs are write-only: remember what we wrote
        self._f_lo = 20e6
        self._notch = True
        self._gain = 128 / 4096 * notch_dc_gain(20e6) ** 2   # reproduces out_gain = 128 at 20 MHz
        self.mon_shift = 16
        self.write("mon_shift", self.mon_shift)
        self.reset_state()

    # ---- low level -------------------------------------------------------------------------
    def write(self, name, value):
        self.ip.write(REG_OFFSET[name], int(value) & 0xFFFFFFFF)
        self._shadow[name] = int(value)

    def read(self, name):
        """Last value written to a register (the HLS registers cannot be read back)."""
        return self._shadow.get(name, 0)

    def reset_state(self):
        """Clear NCO phase and filter state (hold >= 2 samples; a Python round trip is ~us)."""
        self.write("rst_state", 1)
        time.sleep(1e-3)
        self.write("rst_state", 0)

    # ---- high level ------------------------------------------------------------------------
    def set_frequency(self, f_lo_hz, notch=True):
        """NCO frequency = external LO frequency. Also retunes the image notch to 2 f_LO."""
        self._f_lo = f_lo_hz
        self._notch = notch
        self.write("ftw", ftw_for(f_lo_hz))
        self.write("notch_coef", _s(notch_coef(f_lo_hz), 18))
        self.write("notch_en", 1 if notch else 0)
        self.set_gain(self._gain)   # keep the overall gain independent of the notch's DC gain

    def set_bandwidth(self, sig_fc_hz, ref_fc_hz=None):
        """Per-stage cutoff of the 2-stage IIR on the signal channel and (optionally) the reference channel.
        ref_fc=None -> same as signal (exactly cancels the reference filter's phase at the LO beat)."""
        self.write("lpf_m_sig", lpf_coef(sig_fc_hz))
        self.write("lpf_m_ref", lpf_coef(sig_fc_hz if ref_fc_hz is None else ref_fc_hz))

    def set_gain(self, gain):
        """Overall output gain, compensated for the frequency-dependent DC gain of the notch
        (both channels pass through it, so the product scales with its square).
        gain = 1.0 means unity on the 25-bit pre-scaled product; default ~0.064."""
        self._gain = gain
        g = int(round(gain / notch_dc_gain(self._f_lo, self._notch) ** 2 * (1 << OUT_GAIN_Q)))
        g = max(-(1 << 17), min(g, (1 << 17) - 1))
        self.write("out_gain", _s(g, 18))

    def set_offsets(self, i_off, q_off):
        """Offsets added after the gain, in output LSB (I/Q full scale 8191). Use to remove IN2->IN1 crosstalk."""
        self.write("i_offset", _s(i_off, 18))
        self.write("q_offset", _s(q_off, 18))

    def set_phase_offset(self, deg):
        """Rotate the output I/Q by `deg` degrees (positive = counter-clockwise)."""
        th = math.radians(deg)
        self.write("rot_cos", _s(int(round(math.cos(th) * (1 << ROT_Q))), 16))
        self.write("rot_sin", _s(int(round(math.sin(th) * (1 << ROT_Q))), 16))

    def select(self, what):
        """'iq': OUT1=I OUT2=Q; 'magphase': OUT1=|Z| OUT2=phi (8192 = pi);
        'ch1'/'ch2': filtered IN1/IN2 baseband (debug); 'adc': passthrough (DAC scale check)."""
        self.write("out_sel", SEL[what])

    # ---- monitor registers (read-only, updated once per 2^mon_shift samples) ----------------
    def set_monitor_window(self, shift):
        """Monitor window = 2^shift samples (<= 18). 16 -> 65536 samples = 0.52 ms."""
        self.mon_shift = int(shift)
        self.write("mon_shift", self.mon_shift)

    def _rd(self, name):
        v = self.ip.read(REG_OFFSET[name]) & 0xFFFFFFFF
        return v - (1 << 32) if v & 0x80000000 else v

    def monitor(self):
        """Snapshot of the monitor registers, in physical-ish units.
        in*_pk: ADC peak amplitude in volts (LV range, 8192 LSB = 1 V); clip flag if |code| >= 8190.
        out1/out2: mean DAC code over the window; mag/phase computed from them (meaningful in 'iq' mode)."""
        n = 1 << self.mon_shift
        d = {
            "count": self._rd("mon_count") & 0xFFFFFFFF,
            "in1_max": self._rd("mon_in1_max"), "in1_min": self._rd("mon_in1_min"),
            "in2_max": self._rd("mon_in2_max"), "in2_min": self._rd("mon_in2_min"),
            "out1": self._rd("mon_o1_sum") / n, "out2": self._rd("mon_o2_sum") / n,
        }
        d["in1_pk_V"] = (d["in1_max"] - d["in1_min"]) / 2 / 8192
        d["in2_pk_V"] = (d["in2_max"] - d["in2_min"]) / 2 / 8192
        d["in1_clip"] = d["in1_max"] >= 8190 or d["in1_min"] <= -8190
        d["in2_clip"] = d["in2_max"] >= 8190 or d["in2_min"] <= -8190
        d["mag"] = math.hypot(d["out1"], d["out2"])
        d["phase_deg"] = math.degrees(math.atan2(d["out2"], d["out1"]))
        return d

    def status(self):
        m_sig, m_ref = self.read("lpf_m_sig"), self.read("lpf_m_ref")
        ftw = self.read("ftw")
        return {
            "f_nco_Hz": ftw / (1 << PHASE_BITS) * FS,
            "sig_fc_Hz_per_stage": lpf_cutoff(m_sig),
            "ref_fc_Hz_per_stage": lpf_cutoff(m_ref),
            "notch_en": self.read("notch_en"),
            "out_sel": self.read("out_sel"),
            "out_gain": self.read("out_gain"),
        }


if __name__ == "__main__":
    # off-board sanity check of the register formulas
    print("ftw(20 MHz)      = 0x%08X" % ftw_for(20e6))
    print("ftw(30 MHz)      = 0x%08X" % ftw_for(30e6))
    print("lpf_coef(1 MHz)  =", lpf_coef(1e6), " -> fc ~ %.0f Hz" % lpf_cutoff(lpf_coef(1e6)))
    print("lpf_coef(80 kHz) =", lpf_coef(80e3))
    print("notch_coef(20 MHz) =", notch_coef(20e6), " notch_coef(30 MHz) =", notch_coef(30e6))
