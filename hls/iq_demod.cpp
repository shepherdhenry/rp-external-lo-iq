#include "iq_demod.h"
#include "nco_lut.h"

// ---- fixed-point types (widths derived from the analysis in model/iq_model.py) ---------------
typedef ap_int<16> adc_t;                        // sign-extended 14-bit ADC sample
typedef ap_int<16> lut_t;
typedef ap_int<17> nco_t;                        // LUT value after phase-truncation correction (|.| <= 32768)
typedef ap_uint<NCO_RES_BITS> res_t;
typedef ap_uint<13> eps_t;                       // eps * 2^20, max 4095 * pi/2 = 6433
typedef ap_int<30> mix_t;                        // 14b x 17b
typedef ap_int<36> notch_t;                      // notch output before saturation
typedef ap_int<18> ncoef_t;                      // notch coefficient, Q14
typedef ap_int<IIR_DATA_BITS> data_t;            // 23-bit filter input
typedef ap_int<24> iir_y_t;                      // filter output (DSP A operand of the next stage / cmul)
typedef ap_int<36> acc_t;                        // IIR accumulator, y << 12
typedef ap_uint<17> coef_t;                      // LPF coefficient m, 1..131071
typedef ap_int<18> ref_t;                        // reference channel truncated for the 24x18 DSP
typedef ap_int<43> prod_t;                       // 24x18 + 24x18
typedef ap_int<25> pre_t;
typedef ap_int<POST_BITS> post_t;                // 18 bit
typedef ap_int<21> cordic_xy_t;
typedef ap_int<20> cordic_z_t;
typedef ap_int<DAC_BITS> dac_t;

template <int W, int WIN>
static inline ap_int<W> saturate(ap_int<WIN> x) {
#pragma HLS INLINE
    const ap_int<WIN> hi = (ap_int<WIN>(1) << (W - 1)) - 1;
    const ap_int<WIN> lo = -(ap_int<WIN>(1) << (W - 1));
    if (x > hi) return hi;
    if (x < lo) return lo;
    return ap_int<W>(x);
}

// 3-tap FIR notch at 2 f_LO: y = (x + x2 + c*x1) >> 1,  c = notch_coef / 2^14 (one DSP)
static inline notch_t notch_step(mix_t x, mix_t &d1, mix_t &d2, ap_uint<1> en, ncoef_t coef) {
#pragma HLS INLINE
    ap_int<48> m = ap_int<48>(d1) * coef;
    ap_int<35> cx1 = ap_int<35>(m >> NOTCH_COEF_Q);
    ap_int<36> s = ap_int<36>(x) + d2 + cx1;
    notch_t y = notch_t(s >> NOTCH_POST_SHIFT);
    d2 = d1;
    d1 = x;
    return en ? y : notch_t(x);
}

// One look-ahead 1st-order IIR stage (see iq_model.py). Loop-carried paths are
//   p1 -> DSP multiply -> corr   and   (p2, corr, ff) -> sub, add -> p1,  each one clock.
struct iir_state_t {
    acc_t p1;      // acc[n-1]
    acc_t p2;      // acc[n-2]
    acc_t corr;    // ((acc[n-2] >> 12) * m) >> 5, computed one sample earlier from p1
    iir_y_t xp;    // x[n-1]
};

static inline iir_y_t iir_stage(iir_y_t x, iir_state_t &st, coef_t m) {
#pragma HLS INLINE
    ap_int<25> sum = ap_int<25>(x) + st.xp;
    ap_int<42> ffm = sum * m;
    acc_t ff = acc_t(ffm >> IIR_FF_SHIFT);
    acc_t acc = st.p2 - st.corr + ff;
    iir_y_t y = iir_y_t(acc >> IIR_FRAC_BITS);
    ap_int<24> p1h = ap_int<24>(st.p1 >> IIR_CORR_DROP);
    ap_int<41> cm = p1h * m;
    st.corr = acc_t(cm >> IIR_CORR_SHIFT);
    st.p2 = st.p1;
    st.p1 = acc;
    st.xp = x;
    return y;
}

// Vectoring CORDIC: (x, y) -> magnitude (gain-compensated) and angle (pi == CORDIC_PI_UNITS)
static inline void cordic_vec(post_t xi, post_t yi, ap_int<22> &mag, cordic_z_t &ang) {
#pragma HLS INLINE
    cordic_xy_t x = xi, y = yi;
    cordic_z_t z = 0;
    if (xi < 0) {
        x = -cordic_xy_t(xi);
        y = -cordic_xy_t(yi);
        z = (yi >= 0) ? cordic_z_t(CORDIC_PI_UNITS) : cordic_z_t(-CORDIC_PI_UNITS);
    }
    for (int j = 0; j < CORDIC_ITERS; j++) {
#pragma HLS UNROLL
        cordic_xy_t xs = x >> j;
        cordic_xy_t ys = y >> j;
        if (y < 0) {
            x = x - ys;
            y = y + xs;
            z = z - CORDIC_ATAN[j];
        } else {
            x = x + ys;
            y = y - xs;
            z = z + CORDIC_ATAN[j];
        }
    }
    ap_int<37> m = ap_int<37>(x) * CORDIC_GAIN_INV;
    mag = ap_int<22>(m >> CORDIC_GAIN_Q);
    ang = z;
}

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
) {
#pragma HLS INTERFACE ap_ctrl_none port=return
#pragma HLS INTERFACE axis port=adc_in
#pragma HLS INTERFACE axis port=dac_out
#pragma HLS INTERFACE s_axilite port=ftw        bundle=ctrl
#pragma HLS INTERFACE s_axilite port=lpf_m_sig  bundle=ctrl
#pragma HLS INTERFACE s_axilite port=lpf_m_ref  bundle=ctrl
#pragma HLS INTERFACE s_axilite port=notch_en   bundle=ctrl
#pragma HLS INTERFACE s_axilite port=notch_coef bundle=ctrl
#pragma HLS INTERFACE s_axilite port=out_sel    bundle=ctrl
#pragma HLS INTERFACE s_axilite port=out_gain   bundle=ctrl
#pragma HLS INTERFACE s_axilite port=i_offset   bundle=ctrl
#pragma HLS INTERFACE s_axilite port=q_offset   bundle=ctrl
#pragma HLS INTERFACE s_axilite port=rot_cos    bundle=ctrl
#pragma HLS INTERFACE s_axilite port=rot_sin    bundle=ctrl
#pragma HLS INTERFACE s_axilite port=rst_state  bundle=ctrl
#pragma HLS INTERFACE s_axilite port=mon_shift   bundle=ctrl
#pragma HLS INTERFACE s_axilite port=mon_in1_max bundle=ctrl
#pragma HLS INTERFACE s_axilite port=mon_in1_min bundle=ctrl
#pragma HLS INTERFACE s_axilite port=mon_in2_max bundle=ctrl
#pragma HLS INTERFACE s_axilite port=mon_in2_min bundle=ctrl
#pragma HLS INTERFACE s_axilite port=mon_o1_sum  bundle=ctrl
#pragma HLS INTERFACE s_axilite port=mon_o2_sum  bundle=ctrl
#pragma HLS INTERFACE s_axilite port=mon_count   bundle=ctrl
#pragma HLS PIPELINE II=1
#pragma HLS BIND_STORAGE variable=NCO_COS type=rom_1p impl=bram
#pragma HLS BIND_STORAGE variable=NCO_SIN type=rom_1p impl=bram

    // ---- state ---------------------------------------------------------------------------------
    static ap_uint<32> phase = 0;
    static mix_t nd1[4], nd2[4];           // notch delay lines: i1 q1 i2 q2
    static iir_state_t st[8];              // IIR stages: (i1 q1 i2 q2) x (stage 1, stage 2)
#pragma HLS ARRAY_PARTITION variable=nd1 complete
#pragma HLS ARRAY_PARTITION variable=nd2 complete
#pragma HLS ARRAY_PARTITION variable=st complete

    // ---- input ---------------------------------------------------------------------------------
    axis_t in = adc_in.read();
    adc_t in1 = in.data.range(15, 0);
    adc_t in2 = in.data.range(31, 16);

    // ---- NCO -----------------------------------------------------------------------------------
    ap_uint<LUT_ADDR_BITS> idx = phase.range(PHASE_BITS - 1, PHASE_BITS - LUT_ADDR_BITS);
    res_t res = phase.range(PHASE_BITS - LUT_ADDR_BITS - 1, PHASE_BITS - LUT_ADDR_BITS - NCO_RES_BITS);
    lut_t c0 = NCO_COS[idx];
    lut_t s0 = NCO_SIN[idx];
    phase = rst_state ? ap_uint<32>(0) : ap_uint<32>(phase + ftw);
    // first-order correction of the truncated phase: removes the phase-truncation spur that
    // otherwise folds the 40 MHz mixing image onto baseband (-60 dBc -> < -94 dBc)
    eps_t eps = eps_t((ap_uint<27>(res) * NCO_EPS_MUL) >> NCO_EPS_MUL_Q);
    nco_t c = nco_t(c0) - nco_t((ap_int<30>(s0) * eps) >> NCO_EPS_FRAC);
    nco_t s = nco_t(s0) + nco_t((ap_int<30>(c0) * eps) >> NCO_EPS_FRAC);

    // ---- mix with (cos - j sin) ----------------------------------------------------------------
    mix_t i1 = mix_t(ap_int<33>(in1) * c);
    mix_t q1 = mix_t(-(ap_int<33>(in1) * s));
    mix_t i2 = mix_t(ap_int<33>(in2) * c);
    mix_t q2 = mix_t(-(ap_int<33>(in2) * s));

    // ---- image notch, then drop LSBs and saturate into the filter width ------------------------
    data_t x_i1 = saturate<IIR_DATA_BITS, 36>(notch_step(i1, nd1[0], nd2[0], notch_en, notch_coef) >> MIX_DROP);
    data_t x_q1 = saturate<IIR_DATA_BITS, 36>(notch_step(q1, nd1[1], nd2[1], notch_en, notch_coef) >> MIX_DROP);
    data_t x_i2 = saturate<IIR_DATA_BITS, 36>(notch_step(i2, nd1[2], nd2[2], notch_en, notch_coef) >> MIX_DROP);
    data_t x_q2 = saturate<IIR_DATA_BITS, 36>(notch_step(q2, nd1[3], nd2[3], notch_en, notch_coef) >> MIX_DROP);

    // ---- 2-stage IIR per channel ---------------------------------------------------------------
    iir_y_t f_i1 = iir_stage(iir_stage(x_i1, st[0], lpf_m_sig), st[1], lpf_m_sig);
    iir_y_t f_q1 = iir_stage(iir_stage(x_q1, st[2], lpf_m_sig), st[3], lpf_m_sig);
    iir_y_t f_i2 = iir_stage(iir_stage(x_i2, st[4], lpf_m_ref), st[5], lpf_m_ref);
    iir_y_t f_q2 = iir_stage(iir_stage(x_q2, st[6], lpf_m_ref), st[7], lpf_m_ref);

    // ---- state reset, applied at write-back so it stays out of the feedback paths --------------
    // corr is deliberately not cleared (it sits on the p1 -> DSP -> corr path); it becomes 0 one
    // sample later because p1 is 0.  Hold rst_state for >= 2 samples.
    if (rst_state) {
        for (int i = 0; i < 4; i++) { nd1[i] = 0; nd2[i] = 0; }
        for (int i = 0; i < 8; i++) { st[i].p1 = 0; st[i].p2 = 0; st[i].xp = 0; }
    }

    // ---- Z = Z1 * conj(Z2) ---------------------------------------------------------------------
    ref_t b_i = ref_t(f_i2 >> REF_TRUNC);
    ref_t b_q = ref_t(f_q2 >> REF_TRUNC);
    prod_t zi = prod_t(f_i1) * b_i + prod_t(f_q1) * b_q;
    prod_t zq = prod_t(f_q1) * b_i - prod_t(f_i1) * b_q;

    // ---- gain, offset, rotate ------------------------------------------------------------------
    pre_t pi_ = saturate<25, 28>(ap_int<28>(zi >> OUT_PRESHIFT));
    pre_t pq_ = saturate<25, 28>(ap_int<28>(zq >> OUT_PRESHIFT));
    ap_int<43> gi = ap_int<43>(pi_) * out_gain;
    ap_int<43> gq = ap_int<43>(pq_) * out_gain;
    post_t vi = saturate<POST_BITS, 31>(ap_int<31>(gi >> OUT_GAIN_Q));
    post_t vq = saturate<POST_BITS, 31>(ap_int<31>(gq >> OUT_GAIN_Q));
    vi = saturate<POST_BITS, 19>(ap_int<19>(vi) + i_offset);
    vq = saturate<POST_BITS, 19>(ap_int<19>(vq) + q_offset);
    ap_int<35> ri_full = ap_int<35>(vi) * rot_cos - ap_int<35>(vq) * rot_sin;
    ap_int<35> rq_full = ap_int<35>(vq) * rot_cos + ap_int<35>(vi) * rot_sin;
    post_t ri = saturate<POST_BITS, 21>(ap_int<21>(ri_full >> ROT_Q));
    post_t rq = saturate<POST_BITS, 21>(ap_int<21>(rq_full >> ROT_Q));

    // ---- magnitude / phase ---------------------------------------------------------------------
    ap_int<22> mag;
    cordic_z_t ang;
    cordic_vec(ri, rq, mag, ang);
    ap_int<16> ph = ap_int<16>(ang >> CORDIC_PHASE_SHIFT);

    // ---- output select -------------------------------------------------------------------------
    dac_t o1, o2;
    switch ((int)out_sel) {
        case SEL_MAG_PHASE:
            o1 = saturate<DAC_BITS, 22>(mag);
            o2 = saturate<DAC_BITS, 16>(ph);
            break;
        case SEL_CH1:
            o1 = saturate<DAC_BITS, 16>(ap_int<16>(f_i1 >> DBG_SHIFT));
            o2 = saturate<DAC_BITS, 16>(ap_int<16>(f_q1 >> DBG_SHIFT));
            break;
        case SEL_CH2:
            o1 = saturate<DAC_BITS, 16>(ap_int<16>(f_i2 >> DBG_SHIFT));
            o2 = saturate<DAC_BITS, 16>(ap_int<16>(f_q2 >> DBG_SHIFT));
            break;
        case SEL_ADC:
            o1 = saturate<DAC_BITS, 16>(in1);
            o2 = saturate<DAC_BITS, 16>(in2);
            break;
        default:  // SEL_IQ
            o1 = saturate<DAC_BITS, POST_BITS>(ri);
            o2 = saturate<DAC_BITS, POST_BITS>(rq);
            break;
    }

    // ---- monitor: peaks of the ADC inputs and sums of the DAC outputs over 2^mon_shift samples ----
    static ap_uint<32> mon_cnt = 0;
    static ap_uint<32> mon_windows = 0;
    static ap_int<16> mx1 = -32768, mn1 = 32767, mx2 = -32768, mn2 = 32767;
    static ap_int<48> sum1 = 0, sum2 = 0;
    ap_int<16> nmx1 = (in1 > mx1) ? in1 : mx1;
    ap_int<16> nmn1 = (in1 < mn1) ? in1 : mn1;
    ap_int<16> nmx2 = (in2 > mx2) ? in2 : mx2;
    ap_int<16> nmn2 = (in2 < mn2) ? in2 : mn2;
    ap_int<48> nsum1 = sum1 + o1;
    ap_int<48> nsum2 = sum2 + o2;
    ap_uint<32> win_len = ap_uint<32>(1) << mon_shift;
    if (mon_cnt + 1 >= win_len) {
        *mon_in1_max = nmx1; *mon_in1_min = nmn1;
        *mon_in2_max = nmx2; *mon_in2_min = nmn2;
        *mon_o1_sum = (int)nsum1; *mon_o2_sum = (int)nsum2;
        mon_windows++;
        *mon_count = mon_windows;
        mon_cnt = 0;
        mx1 = -32768; mn1 = 32767; mx2 = -32768; mn2 = 32767; sum1 = 0; sum2 = 0;
    } else {
        mon_cnt++;
        mx1 = nmx1; mn1 = nmn1; mx2 = nmx2; mn2 = nmn2; sum1 = nsum1; sum2 = nsum2;
    }

    axis_t out;
    out.data.range(15, 0)  = ap_int<16>(o1);
    out.data.range(31, 16) = ap_int<16>(o2);
    out.keep = 0xF;
    out.strb = 0xF;
    out.last = 0;
    out.user = 0;
    out.id = 0;
    out.dest = 0;
    dac_out.write(out);
}
