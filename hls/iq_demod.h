// External-LO IQ demodulator for Red Pitaya STEMlab 125-14 (xc7z010), Vitis HLS.
//
// One ADC sample pair in, one DAC sample pair out, every clock (II=1 @ 125 MHz), ap_ctrl_none.
// Signal chain and all fixed-point constants are defined in model/iq_model.py and exported to
// iq_params.h / nco_lut.h.  The C++ here must stay bit-exact with fixed_model() in that file.
#ifndef IQ_DEMOD_H
#define IQ_DEMOD_H

#include "ap_int.h"
#include "ap_axi_sdata.h"
#include "hls_stream.h"
#include "iq_params.h"

typedef ap_axiu<32, 0, 0, 0> axis_t;

// AXI-Lite register block (each stored in a 32-bit register):
//   ftw        NCO frequency tuning word, f = ftw / 2^32 * 125 MHz           (default 20 MHz)
//   lpf_m_sig  IN1 channel LPF coefficient, m = round((1 - a^2) * 2^17), a = exp(-2 pi fc / fs), 1..131071
//   lpf_m_ref  IN2 (reference) channel LPF coefficient
//   notch_en   1 = enable the image notch
//   notch_coef notch coefficient c = -2 cos(2 pi * 2 f_LO / fs), Q14 signed 18 bit (20 MHz: 13952)
//   out_sel    SEL_IQ, SEL_MAG_PHASE, SEL_CH1, SEL_CH2, SEL_ADC
//   out_gain   Q12 output gain (4096 = 1.0), signed 18 bit
//   i_offset   added after the gain (crosstalk / DC correction), signed 18 bit, output units
//   q_offset
//   rot_cos    output rotation, Q14 (16384 = 1.0), signed 16 bit
//   rot_sin
//   rst_state  1 = clear NCO phase, filters and delay lines. Hold for >= 2 samples (16 ns); outputs
//              during reset are undefined.
//   mon_shift  monitor window = 2^mon_shift samples (use <= 18 so the sums fit in 32 bit)
// Read-only monitor registers, updated once per window (lets the PS check levels and I/Q without a scope):
//   mon_in1_max/min, mon_in2_max/min   ADC extremes in the last window (14-bit signed codes)
//   mon_o1_sum, mon_o2_sum             sums of the DAC outputs OUT1/OUT2 over the window (divide by 2^mon_shift)
//   mon_count                          number of completed windows since power-up

void iq_demod(
    hls::stream<axis_t> &adc_in,
    hls::stream<axis_t> &dac_out,
    ap_uint<32> ftw,
    ap_uint<17> lpf_m_sig,
    ap_uint<17> lpf_m_ref,
    ap_uint<1>  notch_en,
    ap_int<18>  notch_coef,
    ap_uint<3>  out_sel,
    ap_int<18>  out_gain,
    ap_int<18>  i_offset,
    ap_int<18>  q_offset,
    ap_int<16>  rot_cos,
    ap_int<16>  rot_sin,
    ap_uint<1>  rst_state,
    ap_uint<5>  mon_shift,
    int        *mon_in1_max,
    int        *mon_in1_min,
    int        *mon_in2_max,
    int        *mon_in2_min,
    int        *mon_o1_sum,
    int        *mon_o2_sum,
    unsigned   *mon_count
);

#endif
