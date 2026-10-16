# rp-external-lo-iq — Red Pitaya IQ demodulator locked to an external LO

A Vitis HLS IP for the Red Pitaya STEMlab 125-14 (Zynq-7010) that demodulates IN1 against an external
local oscillator supplied on IN2, in real time, at the full 125 MS/s ADC rate. Both inputs are mixed with the
same internal NCO and low-pass filtered; the result for IN1 is then multiplied by the complex conjugate of the
result for IN2, so the phase of the internal NCO (and therefore the frequency offset between the Red Pitaya
clock and the external LO) cancels exactly. The outputs (I/Q, or magnitude/phase) are available on the two
DACs within about 0.3 µs, and every parameter is a runtime register written from PYNQ.

Included:

- `model/` — bit-exact numpy model of the fixed-point signal chain, a float model for long simulations,
  and the test-vector generator that also emits the C headers used by the HLS source.
- `hls/` — the HLS C++ source, testbench, Tcl build script and a cosim comparison tool.
- `vivado/` — a Vivado 2023.1 script that builds the complete block design and bitstream.
- `pynq/` — the PYNQ driver, a bring-up script, an LO-frequency sweep tool, and an SD-card writer.

A prebuilt `iq_demod.bit` / `iq_demod.hwh` pair is attached to each
[GitHub release](https://github.com/shepherdhenry/rp-external-lo-iq/releases); with it you only need the
PYNQ image and the files in `pynq/`.

## Hardware and tools

| Item | Value |
|---|---|
| Board | Red Pitaya STEMlab 125-14 (tested on the Pro Gen 2 variant), xc7z010clg400-1, 125 MHz ADC clock |
| Inputs | IN1 = signal, IN2 = external LO (reference). LV range ±1 V, high impedance; terminate with 50 Ω if needed |
| Outputs | OUT1 / OUT2 (±1 V), selected by the `out_sel` register |
| OS on the board | PYNQ 3.0.1 image for the Red Pitaya 125-14 from [dspsandbox/Pynq-Redpitaya-125](https://github.com/dspsandbox/Pynq-Redpitaya-125) |
| Tools | Vitis HLS 2023.1, Vivado 2023.1, Python 3 with numpy / scipy / matplotlib |

The ADC/DAC AXI-Stream wrappers and the pin constraints in `vivado/src/` are Pavel Demin's
[red-pitaya-notes](https://github.com/pavel-demin/red-pitaya-notes) cores, taken from Derek Baldwin's
[RedPitayaCavityRelocker](https://github.com/drkbldwn208/RedPitayaCavityRelocker) project.

## Signal chain

```
adc_in (AXIS 32 bit, [15:0] = IN1, [31:16] = IN2, 125 MS/s)
 ├─ NCO: 32-bit phase accumulator, 1024-entry cos/sin LUT (16 bit, BRAM), frequency from `ftw`
 │        first-order correction of the truncated phase bits: cos(φ+ε) ≈ cos φ − ε·sin φ          3 DSP
 ├─ mix:   Z1 = IN1·(cos − j sin),  Z2 = IN2·(cos − j sin)                                       4 DSP
 ├─ image notch (zeros at 2·f_LO): y = (x[n] + c·x[n−1] + x[n−2]) / 2, c = `notch_coef` (Q14)     4 DSP
 ├─ drop 7 LSB, saturate to 23 bit
 ├─ low-pass: 2 cascaded first-order IIR per channel, coefficient registers `lpf_m_sig` / `lpf_m_ref`  16 DSP
 ├─ Z = Z1 · conj(Z2)   (24 × 18 bit)  — the NCO phase cancels here                               4 DSP
 ├─ >> 15, × `out_gain` (Q12), saturate 18 bit, + `i_offset` / `q_offset`, rotate by (`rot_cos`, `rot_sin`)  6 DSP
 ├─ CORDIC (16 iterations) → |Z|, φ                                                              1 DSP
 └─ `out_sel` mux → saturate 14 bit → dac_out (AXIS 32 bit, [13:0] = OUT1, [29:16] = OUT2)
```

Pipeline depth 37 cycles (0.3 µs), initiation interval 1, `ap_ctrl_none`. On the xc7z010 the whole design uses
about 17 % of the LUTs, 44 of 80 DSP slices and 2 of 120 BRAM18 blocks, and meets timing at 125 MHz.

### Design notes

**Why a notch and an IIR.** Mixing produces the wanted difference term near DC and an image at 2·f_LO.
The 3-tap FIR notch removes the image by more than 100 dB at exactly 2·f_LO, which lets the IIR bandwidth be
opened to several MHz; without the notch, a 3 MHz bandwidth leaves the image at only −34 dBc. The notch's DC
gain is (2 + c)/2 and therefore depends on the LO frequency (1.43 at 20 MHz, 1.99 at 30 MHz, 0.12 at 5 MHz);
the driver compensates it in `out_gain`. Below roughly 5 MHz the notch zeros approach DC and remove most of
the signal; disable it there (`notch_en = 0`) and rely on the IIR.

**NCO phase-truncation correction.** With f_LO/f_s = 20/125 the NCO revisits the same phases every 25
samples, so the error from truncating the 32-bit phase to the 10-bit LUT index is periodic and has a line at
exactly 2·f_LO. That line mixes the image back to baseband, where the notch (which sits after the LUT) cannot
remove it. Correcting the LUT output with the discarded phase bits reduces the effect from −60 dB to below
−94 dB.

**IIR structure.** The direct form `y[n] = a·y[n−1] + (1−a)·x[n]` does not close timing at 125 MHz on the
−1 speed grade. The implemented form is the two-sample look-ahead

```
acc[n] = acc[n−2] − ((acc[n−2] >> 12) · m >> 5) + ((x[n] + x[n−1]) · m >> 6)
y[n]   = acc[n] >> 12,          m = round((1 − a²) · 2^17)
```

whose feedback path is one DSP multiply followed by one add, each in its own clock cycle. The coefficient `m`
is written by software, so the cutoff is continuously tunable from about 75 Hz to 30 MHz
(`lpf_coef(fc)` in the model and the driver).

**Reference-channel bandwidth.** The reference channel sees the LO at the beat frequency between the LO and
the NCO. The reference filter's phase shift at that frequency appears directly in the output phase, with a
slope of roughly 2/fc_ref per hertz of beat-frequency change. With equal bandwidths on both channels the two
phase shifts cancel exactly; this is the default. Narrow the reference channel only when the reference is weak
and its noise matters.

## Registers (AXI-Lite, one 32-bit register each)

Offsets are in `pynq/xiq_demod_hw.h` (generated by Vitis HLS). All control registers are write-only;
the monitor registers are read-only.

| Name | Width | Meaning | Default |
|---|---|---|---|
| `ftw` | u32 | NCO frequency word, f = ftw / 2³² × 125 MHz | 0x28F5C28F (20 MHz) |
| `lpf_m_sig` | u17 | IN1 filter coefficient, m = round((1 − a²)·2¹⁷), a = exp(−2π fc / fs) | 12536 (1 MHz per stage) |
| `lpf_m_ref` | u17 | IN2 filter coefficient | same as `lpf_m_sig` |
| `notch_en` | u1 | enable the image notch | 1 |
| `notch_coef` | s18 | c = −2 cos(2π · 2 f_LO / fs), Q14 | 13952 (40 MHz) |
| `out_sel` | u3 | 0: I/Q; 1: \|Z\|/φ (8192 = π); 2: I1/Q1; 3: I2/Q2; 4: ADC passthrough | 0 |
| `out_gain` | s18 | output gain, Q12 (4096 = 1.0) | 128 |
| `i_offset`, `q_offset` | s18 | added after the gain, in output LSB (crosstalk / DC correction) | 0 |
| `rot_cos`, `rot_sin` | s16 | output rotation, Q14 | 16384, 0 |
| `rst_state` | u1 | clears NCO phase and filter state; hold for at least 2 samples | 0 |
| `mon_shift` | u5 | monitor window = 2^mon_shift samples (≤ 18) | 16 |
| `mon_in1_max/min`, `mon_in2_max/min` | s32 | ADC extremes in the last window | read-only |
| `mon_o1_sum`, `mon_o2_sum` | s32 | sums of OUT1 / OUT2 over the last window | read-only |
| `mon_count` | u32 | number of completed windows | read-only |

The DAC outputs are 14-bit signed; 8191 LSB corresponds to +1 V on the LV output range. The phase output
uses 8192 LSB = π, so the full output range is ±180°.

## Building

### Model and test vectors

```bash
cd model
python gen_vectors.py        # writes hls/tb_data/*.txt, hls/nco_lut.h, hls/iq_params.h  (rerun after any model change)
python run_float_sim.py      # behavioural checks: phase lock, step response, image rejection, fixed vs float; plots in model/plots/
```

`iq_model.py` is the reference for every bit width and truncation in the design; the C++ must stay bit-exact
with `fixed_model()`, which the HLS testbench verifies.

### HLS

```bash
cd hls
vitis_hls -f run_hls.tcl -tclargs csim                 # C simulation against the model vectors (must report ALL CASES PASS)
vitis_hls -f run_hls.tcl -tclargs synth                # C synthesis: II, timing estimate, resources
vitis_hls -f run_hls.tcl -tclargs cosim 6000 wide      # C/RTL co-simulation of one case
python compare_cosim.py iq_demod_prj 500               # aligned comparison of the cosim vectors (see below)
vitis_hls -f run_hls.tcl -tclargs export               # packages hls/iq_demod_ip.zip
vitis_hls -f run_hls.tcl -tclargs all 6000 wide        # everything above
```

The IP is a free-running `ap_ctrl_none` pipeline. The co-simulation harness starts streaming samples at
cycle 0 but needs about 50 cycles to write the AXI-Lite registers, so its built-in sample-by-sample check
always reports mismatches; `compare_cosim.py` aligns the C and RTL outputs, skips the register-settling period
and compares the rest (expected result: at most 1 LSB difference, caused by the different NCO start phase).
Co-simulation therefore runs one case at a time with constant registers; the `wide` case settles fastest.

### Vivado

```bash
cd vivado
vivado -mode batch -source build.tcl                              # about 5 minutes; outputs in vivado/output/
vivado -mode batch -source build.tcl -tclargs <build_dir> bd_only # only create and validate the block design
```

The script unpacks `hls/iq_demod_ip.zip`, builds the block design (PS7, clock wizard, ADC and DAC wrappers,
the IP on AXI-Lite at 0x4000_0000), runs synthesis and implementation, and copies `iq_demod.bit`,
`iq_demod.hwh` and the timing and utilization reports to `vivado/output/`. The build directory defaults to a
local path inside `build.tcl`; edit it or pass it as the first argument.

The DAC wrapper needs `ddr_clk` and `wrt_clk` at 250 MHz with the phases used in red-pitaya-notes
(157.5° and 202.5°); the dual DAC takes both channels interleaved within one 125 MHz cycle, and with 125 MHz
clocks only one output channel updates.

## Running on the board

1. Write the PYNQ 3.0.1 Red Pitaya image to a microSD card. `pynq/write_sd_image.ps1` does this from an
   administrator PowerShell on Windows and verifies the card afterwards (it writes the partition table last
   so that Windows does not mount the partitions while the data is still being written).
2. Boot the board from that card. The PYNQ image does not drive the Red Pitaya status LEDs, so only the green
   power LED is lit. If no DHCP server is present (for example with a direct cable to a PC) the board uses
   192.168.2.99/24; give the PC an address such as 192.168.2.1/24. SSH and Jupyter (port 9090) use the user
   `xilinx`. The serial console on the CON port runs at 115200 8N1.
3. Copy the files to the board:

   ```bash
   scp vivado/output/iq_demod.bit vivado/output/iq_demod.hwh pynq/*.py xilinx@192.168.2.99:~/iq/
   ```

4. Loading an overlay requires root and the PYNQ virtual environment:

   ```bash
   cd ~/iq
   sudo bash -lc "source /usr/local/share/pynq-venv/bin/activate; python3 bringup.py <step> [options]"
   ```

   Steps, in the order they are useful: `load` (print registers), `adc` (OUT = IN passthrough, checks the DAC
   scale and sign), `ch2` / `ch1` (filtered baseband of one input, a phasor rotating at the beat frequency),
   `iq` (the demodulated output; with the same signal on IN1 and IN2 the phasor stands still), `offset`
   (determine the crosstalk offset), `magphase`, and `watch` (poll the monitor registers and print input
   peaks, clipping, mean OUT1/OUT2, |Z| and phase without a scope). Options include `--freq`, `--bw-sig`,
   `--bw-ref`, `--gain`, `--phase`, `--offset I Q`, `--mode`, `--seconds`; see `python3 bringup.py -h`.
5. `sweep.py 19.5 22` scans the NCO over a frequency range and reports the LO frequency as the |Z| maximum.
6. From Python, `IQDemod` in `iq_demod_driver.py` exposes `set_frequency()` (also retunes the notch and
   compensates its gain), `set_bandwidth()`, `set_gain()`, `set_phase_offset()`, `set_offsets()`,
   `select()`, `monitor()` and `status()`. Register access uses MMIO offsets directly because PYNQ's
   `register_map` does not expose HLS output registers.

The bitstream is loaded into the FPGA at run time by `Overlay()`; it is not stored in the FPGA. Swapping back
to the stock Red Pitaya SD card restores the stock system with nothing else to undo.

## License

MIT, see `LICENSE`.
