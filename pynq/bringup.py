"""
Bring-up / acceptance sequence for the IQ demodulator overlay. Run on the Red Pitaya (PYNQ image):

    scp vivado/output/iq_demod.bit vivado/output/iq_demod.hwh pynq/iq_demod_driver.py pynq/bringup.py xilinx@rp-f0f9ad.local:~/iq/
    ssh xilinx@rp-f0f9ad.local
    cd ~/iq && sudo python3 bringup.py <step>

Steps (do them in order, each needs a specific cable setup; watch OUT1/OUT2 on a scope):

  load       load the overlay, print the register map and status
  adc        out_sel=ADC passthrough. Feed a known sine to IN1/IN2: OUT1/OUT2 must reproduce it.
             Checks DAC scale and sign (LV input +-1 V -> DAC +-1 V).
  ch2        out_sel=CH2: filtered IN2 baseband. With the 20 MHz reference on IN2, OUT1/OUT2 (I2, Q2) should
             be a slowly rotating phasor at the RP/LO beat (~400 Hz at 20 ppm). Confirms NCO + mixer + LPF.
  ch1        out_sel=CH1: same for IN1.
  iq         out_sel=IQ. With the SAME 20 MHz on IN1 and IN2 (power splitter) the (OUT1, OUT2) phasor must
             stand still in XY mode. Phase drift spec: < ~1 deg/min.
  offset     IN1 dark (PD blocked) or terminated, IN2 = reference: prints the mean I/Q offset caused by
             IN2 -> IN1 crosstalk, and writes it into i_offset / q_offset (sign-inverted).
             Needs the PS-side readback of the DAC values; without an ADC capture path, read OUT1/OUT2 on a
             scope/DMM instead and call iq.set_offsets(-I, -Q) by hand.
  magphase   out_sel=MAG_PHASE: OUT1 = |Z|, OUT2 = phi (8192 LSB = pi -> 1 V = 180 deg on the LV output scale).
  watch      any mode (default iq): poll the on-chip monitor registers for --seconds and print input peaks,
             clipping, mean OUT1/OUT2, |Z| and phase. No scope needed. Use --mode to pick the output.

Default settings used here: f_LO = 20 MHz, signal fc = 1 MHz per stage, reference fc = 80 kHz, gain = default.
"""
import sys
import time

from iq_demod_driver import IQDemod

F_LO = 20e6


def banner(s):
    print("\n" + "=" * 70 + "\n" + s + "\n" + "=" * 70)


def main():
    import argparse
    ap = argparse.ArgumentParser(description="IQ demodulator bring-up steps")
    ap.add_argument("step", nargs="?", default="load", help="load|adc|ch1|ch2|iq|magphase|offset")
    ap.add_argument("--freq", type=float, default=F_LO, help="LO / NCO frequency in Hz (default 20e6)")
    ap.add_argument("--bw-sig", type=float, default=1e6, help="signal channel per-stage cutoff in Hz")
    ap.add_argument("--bw-ref", type=float, default=None,
                    help="reference channel per-stage cutoff in Hz (default: same as --bw-sig, which cancels the "
                         "reference filter's phase exactly; a narrower value lowers noise but makes the output phase "
                         "move by ~2/fc_ref per Hz of LO frequency drift)")
    ap.add_argument("--gain", type=float, default=None, help="overall output gain (default ~0.064)")
    ap.add_argument("--phase", type=float, default=0.0, help="output rotation in degrees")
    ap.add_argument("--offset", type=int, nargs=2, default=(0, 0), metavar=("I", "Q"), help="I/Q offsets in LSB")
    ap.add_argument("--no-notch", action="store_true", help="disable the image notch")
    ap.add_argument("--no-reload", action="store_true", help="do not re-download the bitstream (keeps filter state)")
    ap.add_argument("--mode", default="iq", help="output selection for the watch step (iq|magphase|ch1|ch2|adc)")
    ap.add_argument("--seconds", type=float, default=5.0, help="duration of the watch step")
    ap.add_argument("--interval", type=float, default=0.25, help="print interval of the watch step")
    a = ap.parse_args()
    step = a.step
    iq = IQDemod("iq_demod.bit", download=not a.no_reload)
    iq.set_frequency(a.freq, notch=not a.no_notch)
    iq.set_bandwidth(a.bw_sig, ref_fc_hz=a.bw_ref)
    if a.gain is not None:
        iq.set_gain(a.gain)
    iq.set_phase_offset(a.phase)
    iq.set_offsets(a.offset[0], a.offset[1])

    if step == "load":
        banner("overlay loaded")
        print(iq.rm)
        print(iq.status())

    elif step == "adc":
        banner("ADC passthrough: OUT1 = IN1, OUT2 = IN2")
        iq.select("adc")
        print("Feed a known sine (< 1 Vpp) to IN1 and IN2; OUT1/OUT2 must reproduce amplitude and sign.")

    elif step in ("ch1", "ch2"):
        banner(f"{step.upper()}: filtered baseband of {'IN1' if step == 'ch1' else 'IN2'} (OUT1 = I, OUT2 = Q)")
        iq.select(step)
        print("Expect a phasor rotating at the RP/LO beat frequency (hundreds of Hz). Amplitude ~ input amplitude * 0.57.")

    elif step == "iq":
        banner("I/Q output relative to the external LO")
        iq.select("iq")
        print("Same 20 MHz on IN1 and IN2 via a splitter: XY phasor must stand still.")
        print("Adjust gain with iq.set_gain(...) if OUT1/OUT2 clip (8191 LSB = full scale).")

    elif step == "magphase":
        banner("|Z| on OUT1, phase on OUT2 (8192 LSB = 180 deg)")
        iq.select("magphase")

    elif step == "watch":
        banner(f"monitor registers, output = {a.mode}, {a.seconds:.0f} s")
        iq.select(a.mode)
        print(f"{'t[s]':>5} {'in1 pk[V]':>9} {'in2 pk[V]':>9} {'clip':>5} {'OUT1':>8} {'OUT2':>8} {'|Z|':>8} {'phase':>8}")
        t0 = time.time()
        rows = []
        while time.time() - t0 < a.seconds:
            m = iq.monitor()
            rows.append(m)
            clip = ("1" if m["in1_clip"] else "-") + ("2" if m["in2_clip"] else "-")
            print(f"{time.time() - t0:5.2f} {m['in1_pk_V']:9.3f} {m['in2_pk_V']:9.3f} {clip:>5} "
                  f"{m['out1']:8.0f} {m['out2']:8.0f} {m['mag']:8.0f} {m['phase_deg']:8.2f}")
            time.sleep(a.interval)
        if len(rows) > 2:
            import statistics
            ph = [r["phase_deg"] for r in rows]
            # unwrap phase for std
            unw = [ph[0]]
            for p in ph[1:]:
                d = p - unw[-1]
                while d > 180: d -= 360
                while d < -180: d += 360
                unw.append(unw[-1] + d)
            print(f"\nphase: mean {statistics.mean(unw):.2f} deg, std {statistics.pstdev(unw):.3f} deg, "
                  f"drift {(unw[-1] - unw[0]):+.2f} deg over {a.seconds:.0f} s; "
                  f"|Z| mean {statistics.mean(r['mag'] for r in rows):.0f} +- {statistics.pstdev([r['mag'] for r in rows]):.0f}")

    elif step == "offset":
        banner("crosstalk offset")
        iq.select("iq")
        print("Block the light / terminate IN1, keep the reference on IN2.")
        print("Read the DC levels of OUT1 (I) and OUT2 (Q) in DAC LSB (1 V = 8191 LSB on the LV scale),")
        print("then run:  iq.set_offsets(-I_lsb, -Q_lsb)   and re-check that OUT1/OUT2 are ~0.")

    else:
        print(__doc__)
        return
    print("\nstatus:", iq.status())


if __name__ == "__main__":
    main()
