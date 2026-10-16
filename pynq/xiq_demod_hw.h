// ==============================================================
// Vitis HLS - High-Level Synthesis from C, C++ and OpenCL v2023.1 (64-bit)
// Tool Version Limit: 2023.05
// Copyright 1986-2022 Xilinx, Inc. All Rights Reserved.
// Copyright 2022-2023 Advanced Micro Devices, Inc. All Rights Reserved.
// 
// ==============================================================
// ctrl
// 0x00 : reserved
// 0x04 : reserved
// 0x08 : reserved
// 0x0c : reserved
// 0x10 : Data signal of ftw
//        bit 31~0 - ftw[31:0] (Read/Write)
// 0x14 : reserved
// 0x18 : Data signal of lpf_m_sig
//        bit 16~0 - lpf_m_sig[16:0] (Read/Write)
//        others   - reserved
// 0x1c : reserved
// 0x20 : Data signal of lpf_m_ref
//        bit 16~0 - lpf_m_ref[16:0] (Read/Write)
//        others   - reserved
// 0x24 : reserved
// 0x28 : Data signal of notch_en
//        bit 0  - notch_en[0] (Read/Write)
//        others - reserved
// 0x2c : reserved
// 0x30 : Data signal of notch_coef
//        bit 17~0 - notch_coef[17:0] (Read/Write)
//        others   - reserved
// 0x34 : reserved
// 0x38 : Data signal of out_sel
//        bit 2~0 - out_sel[2:0] (Read/Write)
//        others  - reserved
// 0x3c : reserved
// 0x40 : Data signal of out_gain
//        bit 17~0 - out_gain[17:0] (Read/Write)
//        others   - reserved
// 0x44 : reserved
// 0x48 : Data signal of i_offset
//        bit 17~0 - i_offset[17:0] (Read/Write)
//        others   - reserved
// 0x4c : reserved
// 0x50 : Data signal of q_offset
//        bit 17~0 - q_offset[17:0] (Read/Write)
//        others   - reserved
// 0x54 : reserved
// 0x58 : Data signal of rot_cos
//        bit 15~0 - rot_cos[15:0] (Read/Write)
//        others   - reserved
// 0x5c : reserved
// 0x60 : Data signal of rot_sin
//        bit 15~0 - rot_sin[15:0] (Read/Write)
//        others   - reserved
// 0x64 : reserved
// 0x68 : Data signal of rst_state
//        bit 0  - rst_state[0] (Read/Write)
//        others - reserved
// 0x6c : reserved
// 0x70 : Data signal of mon_shift
//        bit 4~0 - mon_shift[4:0] (Read/Write)
//        others  - reserved
// 0x74 : reserved
// 0x78 : Data signal of mon_in1_max
//        bit 31~0 - mon_in1_max[31:0] (Read)
// 0x7c : Control signal of mon_in1_max
//        bit 0  - mon_in1_max_ap_vld (Read/COR)
//        others - reserved
// 0x88 : Data signal of mon_in1_min
//        bit 31~0 - mon_in1_min[31:0] (Read)
// 0x8c : Control signal of mon_in1_min
//        bit 0  - mon_in1_min_ap_vld (Read/COR)
//        others - reserved
// 0x98 : Data signal of mon_in2_max
//        bit 31~0 - mon_in2_max[31:0] (Read)
// 0x9c : Control signal of mon_in2_max
//        bit 0  - mon_in2_max_ap_vld (Read/COR)
//        others - reserved
// 0xa8 : Data signal of mon_in2_min
//        bit 31~0 - mon_in2_min[31:0] (Read)
// 0xac : Control signal of mon_in2_min
//        bit 0  - mon_in2_min_ap_vld (Read/COR)
//        others - reserved
// 0xb8 : Data signal of mon_o1_sum
//        bit 31~0 - mon_o1_sum[31:0] (Read)
// 0xbc : Control signal of mon_o1_sum
//        bit 0  - mon_o1_sum_ap_vld (Read/COR)
//        others - reserved
// 0xc8 : Data signal of mon_o2_sum
//        bit 31~0 - mon_o2_sum[31:0] (Read)
// 0xcc : Control signal of mon_o2_sum
//        bit 0  - mon_o2_sum_ap_vld (Read/COR)
//        others - reserved
// 0xd8 : Data signal of mon_count
//        bit 31~0 - mon_count[31:0] (Read)
// 0xdc : Control signal of mon_count
//        bit 0  - mon_count_ap_vld (Read/COR)
//        others - reserved
// (SC = Self Clear, COR = Clear on Read, TOW = Toggle on Write, COH = Clear on Handshake)

#define XIQ_DEMOD_CTRL_ADDR_FTW_DATA         0x10
#define XIQ_DEMOD_CTRL_BITS_FTW_DATA         32
#define XIQ_DEMOD_CTRL_ADDR_LPF_M_SIG_DATA   0x18
#define XIQ_DEMOD_CTRL_BITS_LPF_M_SIG_DATA   17
#define XIQ_DEMOD_CTRL_ADDR_LPF_M_REF_DATA   0x20
#define XIQ_DEMOD_CTRL_BITS_LPF_M_REF_DATA   17
#define XIQ_DEMOD_CTRL_ADDR_NOTCH_EN_DATA    0x28
#define XIQ_DEMOD_CTRL_BITS_NOTCH_EN_DATA    1
#define XIQ_DEMOD_CTRL_ADDR_NOTCH_COEF_DATA  0x30
#define XIQ_DEMOD_CTRL_BITS_NOTCH_COEF_DATA  18
#define XIQ_DEMOD_CTRL_ADDR_OUT_SEL_DATA     0x38
#define XIQ_DEMOD_CTRL_BITS_OUT_SEL_DATA     3
#define XIQ_DEMOD_CTRL_ADDR_OUT_GAIN_DATA    0x40
#define XIQ_DEMOD_CTRL_BITS_OUT_GAIN_DATA    18
#define XIQ_DEMOD_CTRL_ADDR_I_OFFSET_DATA    0x48
#define XIQ_DEMOD_CTRL_BITS_I_OFFSET_DATA    18
#define XIQ_DEMOD_CTRL_ADDR_Q_OFFSET_DATA    0x50
#define XIQ_DEMOD_CTRL_BITS_Q_OFFSET_DATA    18
#define XIQ_DEMOD_CTRL_ADDR_ROT_COS_DATA     0x58
#define XIQ_DEMOD_CTRL_BITS_ROT_COS_DATA     16
#define XIQ_DEMOD_CTRL_ADDR_ROT_SIN_DATA     0x60
#define XIQ_DEMOD_CTRL_BITS_ROT_SIN_DATA     16
#define XIQ_DEMOD_CTRL_ADDR_RST_STATE_DATA   0x68
#define XIQ_DEMOD_CTRL_BITS_RST_STATE_DATA   1
#define XIQ_DEMOD_CTRL_ADDR_MON_SHIFT_DATA   0x70
#define XIQ_DEMOD_CTRL_BITS_MON_SHIFT_DATA   5
#define XIQ_DEMOD_CTRL_ADDR_MON_IN1_MAX_DATA 0x78
#define XIQ_DEMOD_CTRL_BITS_MON_IN1_MAX_DATA 32
#define XIQ_DEMOD_CTRL_ADDR_MON_IN1_MAX_CTRL 0x7c
#define XIQ_DEMOD_CTRL_ADDR_MON_IN1_MIN_DATA 0x88
#define XIQ_DEMOD_CTRL_BITS_MON_IN1_MIN_DATA 32
#define XIQ_DEMOD_CTRL_ADDR_MON_IN1_MIN_CTRL 0x8c
#define XIQ_DEMOD_CTRL_ADDR_MON_IN2_MAX_DATA 0x98
#define XIQ_DEMOD_CTRL_BITS_MON_IN2_MAX_DATA 32
#define XIQ_DEMOD_CTRL_ADDR_MON_IN2_MAX_CTRL 0x9c
#define XIQ_DEMOD_CTRL_ADDR_MON_IN2_MIN_DATA 0xa8
#define XIQ_DEMOD_CTRL_BITS_MON_IN2_MIN_DATA 32
#define XIQ_DEMOD_CTRL_ADDR_MON_IN2_MIN_CTRL 0xac
#define XIQ_DEMOD_CTRL_ADDR_MON_O1_SUM_DATA  0xb8
#define XIQ_DEMOD_CTRL_BITS_MON_O1_SUM_DATA  32
#define XIQ_DEMOD_CTRL_ADDR_MON_O1_SUM_CTRL  0xbc
#define XIQ_DEMOD_CTRL_ADDR_MON_O2_SUM_DATA  0xc8
#define XIQ_DEMOD_CTRL_BITS_MON_O2_SUM_DATA  32
#define XIQ_DEMOD_CTRL_ADDR_MON_O2_SUM_CTRL  0xcc
#define XIQ_DEMOD_CTRL_ADDR_MON_COUNT_DATA   0xd8
#define XIQ_DEMOD_CTRL_BITS_MON_COUNT_DATA   32
#define XIQ_DEMOD_CTRL_ADDR_MON_COUNT_CTRL   0xdc

