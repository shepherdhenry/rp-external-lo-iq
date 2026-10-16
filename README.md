# RP_IQ — Red Pitaya 外部 LO IQ 解调 HLS IP

*Red Pitaya STEMlab 125-14 (Zynq-7010) IQ demodulator locked to an external LO: a Vitis HLS IP (II=1 @ 125 MS/s)
that mixes both inputs with an internal NCO, filters, and multiplies IN1 by conj(IN2) so the RP/LO clock offset cancels;
real-time I/Q or |Z|/phase on the DACs, runtime-tunable via AXI-Lite from PYNQ. Bit-exact numpy model, HLS testbench,
Vivado 2023.1 build script and PYNQ driver included. Documentation is in Chinese.*


交接文档见 [RP_IQ_demod_extLO_handoff.md](RP_IQ_demod_extLO_handoff.md)。本文件记录实际实现和当前状态。

## 状态（2026-10-09）

| 步骤 | 状态 |
|---|---|
| 1. numpy 参考模型（浮点 + 定点逐位） | 完成，`model/` |
| 2. HLS C++ + testbench，C 仿真 | 完成，13 个用例 × 10 万采样逐点一致（含 30 MHz 和 5 MHz LO） |
| 3. C 综合 | 完成，II=1，估计 7.08 ns @ 8 ns（含 0.8 ns 不确定度），DSP 42/80，BRAM 2/120，LUT 估计 56%（HLS 估计偏保守，Vivado 实际 15%） |
| 4. C/RTL cosim | 完成，`wide` 用例 6000 采样：寄存器生效后 RTL 与 C 最大差 1 LSB，72% 逐位相同，幅度比 1.00000（见"cosim 说明"） |
| 5. IP 导出 | 完成，`hls/iq_demod_ip.zip`（Vitis HLS 2023.1） |
| 6. Vivado block design（2023.1，从 Derek 的 Tcl 重建） | 完成，`vivado/build.tcl`，时序满足：WNS +0.686 ns，WHS +0.012 ns；整片 LUT 3015/17600 (17%)，FF 3993，DSP 44/80，BRAM18 2/120 |
| 7. bitstream | 完成，`vivado/output/iq_demod.bit` + `iq_demod.hwh` |
| 8. PYNQ 驱动 | 完成，`pynq/iq_demod_driver.py`、`pynq/bringup.py`，已在板上运行 |
| 9. 上板 | **通过**（2026-10-15）：VCO 主输出接 IN1、辅助输出接 IN2（相干），`iq` 模式下 OUT1/OUT2 为恒定直流，相位 5 秒内逐位不变（std < 0.1°），扫频找到 VCO 实际频率 20.578 MHz |
| 10. 板上监视 | 完成：IP 新增只读监视寄存器（输入峰值、输出均值、窗口计数），`bringup.py watch` 不用示波器就能看电平和 I/Q |

### SD 卡与镜像（2026-10-12 的教训）

Derek 共享盘上的两份 `Pynq-Redpitaya-125-14-3.0.1_v1.1_img.img` 都是坏的（一份根文件系统全零，一份 ext4 大面积损坏），
它们其实就是上游 dspsandbox 的 `Pynq-Redpitaya-125-14-3.0.1.img`（启动分区逐字节相同）。可用的镜像：
Google Drive id `1yZ7dBUzU9IY8ECg2v9Jc2SXsewsgbF-1`，MD5 `8DAF2B9EEAE2A5C62A1846728C4D43E7`，本地留在 `C:\Users\Dycontrol4\rp_iq_sd\dspsandbox\`。
写卡用 `pynq/write_sd_image.ps1`（管理员；先写数据再写 MBR，否则 Windows 会中途接管设备；写完自动读回校验）。
镜像内 ext4 的检查/修复脚本：`C:\Users\Dycontrol4\rp_iq_sd\fsck_image.sh`（在 WSL 里跑）。
板子直连电脑时拿不到 DHCP，PYNQ 会用固定地址 192.168.2.99；给电脑网口加 192.168.2.1/24 即可。
PYNQ 不点亮 RP 的蓝灯/红灯，只亮绿灯是正常的。串口 CON 口 115200 8N1，启动完成后有自动登录的控制台。
板上 `sudo` 默认要密码，已为 xilinx 用户加了 `/etc/sudoers.d/90-xilinx-nopasswd`；加载 overlay 必须 root，并在 PYNQ 的 venv 里运行：
`sudo bash -lc "source /usr/local/share/pynq-venv/bin/activate; python3 bringup.py <step>"`。

工具：Vitis HLS / Vivado **2023.1**（`C:\Xilinx`），Python 3.12（numpy / scipy / matplotlib）。
Derek 的工程：`Y:\Users\Henry\code\RedPitayaCavityRelocker`（GitHub clone），HLS 源码 `Y:\Users\Henry\code\hls_cavity_relock`。
PyRPL 源码（参考）：`Y:\Users\Henry\code\pyrpl`。

## 目录

```
model/   iq_model.py        信号链定义、浮点模型、定点逐位模型、测试信号合成、导出 C 头文件
         run_float_sim.py   行为验证：相位锁定 / 阶跃响应 / 镜像抑制 / 定点 vs 浮点，图在 model/plots/
         gen_vectors.py     生成 HLS testbench 向量 hls/tb_data/*.txt 和 hls/nco_lut.h、hls/iq_params.h
hls/     iq_demod.h/.cpp    HLS 顶层 iq_demod
         tb_iq_demod.cpp    testbench：回放向量，逐采样比对
         run_hls.tcl        vitis_hls -f run_hls.tcl -tclargs csim|synth|cosim [N] [case]|export|all
         compare_cosim.py   cosim 的 C/RTL 输出对齐比对（见"cosim 说明"）
         iq_demod_ip.zip    导出的 IP（ip_catalog）
vivado/  build.tcl           Vivado 2023.1：解压 IP、重建 block design、综合实现、出 .bit/.hwh（在 C: 盘本地目录构建）
         src/                Derek 的 ADC/DAC wrapper 和引脚约束（原样拷贝）
         output/             iq_demod.bit, iq_demod.hwh, timing_summary.rpt, utilization.rpt, build_summary.txt
pynq/    iq_demod_driver.py  板上驱动：寄存器封装 + 频率/带宽/增益/旋转/偏置换算
         bringup.py          上板验收步骤脚本
```

## 信号链

```
adc_in (AXIS 32b, [15:0]=IN1, [31:16]=IN2, 125 MS/s)
 ├─ NCO: 32 位相位累加器, 1024 点 cos/sin 表 (16 bit, BRAM), ftw 寄存器
 │        截掉的 12 位相位余量 ε 做一阶泰勒校正 cos(φ+ε) ≈ cos φ − ε·sin φ        3 DSP
 ├─ 混频: Z1 = IN1·(cos − j sin), Z2 = IN2·(cos − j sin)              4 DSP
 ├─ 镜像陷波 (零点在 2·f_LO): y = (x[n] + c·x[n−1] + x[n−2]) / 2, c = notch_coef 寄存器 (Q14)   4 DSP
 │        直流增益 (2+c)/2 随 LO 频率变: 20 MHz 1.43, 30 MHz 1.99, 5 MHz 0.12, 用 out_gain 补偿
 ├─ 丢 7 位 LSB, 饱和到 23 位
 ├─ 低通: 每路 2 级一阶 IIR, 2 采样前瞻形式(见下), 系数寄存器 lpf_m_sig / lpf_m_ref   16 DSP
 ├─ 复乘: Z = Z1 · conj(Z2)   (24×18 bit)                                         4 DSP
 │        RP 的 NCO 相位在这里抵消, 剩下的是相对外部 LO 的 I/Q
 ├─ >>15, × out_gain (Q12), 饱和到 18 位, + i_offset/q_offset, 旋转 (rot_cos, rot_sin, Q14)   6 DSP
 ├─ CORDIC (16 次迭代) → |Z|, φ                                                   1 DSP
 └─ out_sel 选择 → 饱和 14 位 → dac_out (AXIS 32b, [13:0]=OUT1, [29:16]=OUT2)
```

流水线深度 36 拍（0.29 µs），II=1，`ap_ctrl_none`。

### 为什么 NCO 要做相位截断校正

fs/f_LO = 6.25，NCO 每 25 个采样回到同一相位，相位截断（32 位 → 10 位查表）的误差因此是周期 25 的序列，
在 40 MHz 处有一条谱线。混频输出里的 40 MHz 镜像和这条谱线相乘后正好落回基带，而陷波器在查表之后，拦不住。
后果是输出幅度随 NCO 起始相位变化 0.16%，在真实系统里表现为 800 Hz 的 0.06° 相位纹波。
用截掉的相位余量做一阶校正后，这个效应降到 2e-5（−94 dB）。这是在 cosim 比对里发现的（见"cosim 说明"）。

### 换 LO 频率

只改寄存器：`ftw`（NCO 频率）、`notch_coef`（陷波跟到 2·f_LO）、`out_gain`（补偿陷波直流增益的变化）。
`pynq/iq_demod_driver.py` 的 `set_frequency()` 一次做完这三件事。LO 低于约 5 MHz 时陷波零点离直流太近，
把有用信号也压掉大半（增益 0.12），这时建议关掉陷波（`notch_en=0`）靠 IIR 滤镜像。

### 为什么要陷波 + IIR

混频产生差频（我们要的，接近直流）和和频（40 MHz 镜像）两项。陷波器把 40 MHz 这一点打掉 100 dB 以上，
IIR 再压制其他杂散和噪声。有了陷波，IIR 带宽放到 3 MHz 以上镜像仍在 −120 dBc 以下；没有陷波时
3 MHz 带宽的镜像只有 −34 dBc。

### IIR 的实现形式

一阶 IIR `y[n] = a·y[n−1] + (1−a)·x[n]` 的反馈环路在 125 MHz 下关不上时序（HLS 估计 11.8 ns）。
改成 2 采样前瞻形式：

```
acc[n] = acc[n−2] − ((acc[n−2] >> 12) · m >> 5) + ((x[n] + x[n−1]) · m >> 6)
y[n]   = acc[n] >> 12,       m = round((1 − a²) · 2^17)
```

反馈路径拆成两拍：`acc[n−1] → DSP 乘 m → corr 寄存器`，`(acc[n−2], corr, 前馈) → 减、加 → acc`。
系数 `m` 由软件直接写，截止频率可以连续调（`model/iq_model.py: lpf_coef(fc)`），范围约 75 Hz 到 30 MHz。
前馈项用 `(x[n]+x[n−1])` 而不是精确的 `(x[n] + a·x[n−1])`，少一个乘法器，代价只是在 62.5 MHz 附近多一个零点。

### 参考路带宽的取舍

RP 时钟和外部 LO 有 ppm 级频偏，解调后参考路信号在 Δf ≈ 400 Hz（20 ppm 时）。参考路滤波器在 Δf 处的
相移会原样出现在输出相位里：`dφ/dΔf ≈ 2/fc_ref`。默认 fc_ref = 80 kHz 时为 0.0014°/Hz，RP 晶振漂移几 ppm
对应几十 Hz → 0.05° 量级。要彻底消掉就把 `lpf_m_ref` 设成和 `lpf_m_sig` 一样（两路滤波器完全相同，相移抵消）。

## 寄存器（AXI-Lite，每个占一个 32 位寄存器，偏移见 `hls/iq_demod_ip.zip` 里的 `drivers/.../xiq_demod_hw.h`）

| 名称 | 位宽 | 含义 | 默认 |
|---|---|---|---|
| ftw | u32 | NCO 频率字，f = ftw/2³²·125 MHz | 0x28F5C28F（20 MHz） |
| lpf_m_sig | u17 | IN1 路滤波系数 m = round((1−a²)·2¹⁷)，a = exp(−2π fc/fs) | 12536（每级 1 MHz，两级 −3 dB 约 640 kHz） |
| lpf_m_ref | u17 | IN2 路滤波系数 | 1050（80 kHz / 52 kHz） |
| notch_en | u1 | 镜像陷波使能 | 1 |
| notch_coef | s18 | 陷波系数 c = −2cos(2π·2f_LO/fs)，Q14 | 13952（40 MHz） |
| out_sel | u3 | 0: I/Q；1: |Z|/φ（8192 = π）；2: I1/Q1 调试；3: I2/Q2 调试；4: ADC 直通 | 0 |
| out_gain | s18 | 输出增益，Q12（4096 = 1.0） | 128 |
| i_offset, q_offset | s18 | 增益后加的偏置（扣串扰） | 0 |
| rot_cos, rot_sin | s16 | 输出旋转角，Q14 | 16384, 0 |
| rst_state | u1 | 清 NCO 相位、滤波器状态；保持 ≥ 2 个采样 | 0 |

## 运行

```bash
cd model
python gen_vectors.py          # 生成向量和 hls/*.h（改了 iq_model.py 之后必须重跑）
python run_float_sim.py        # 行为验证，图在 model/plots/
cd ../hls
vitis_hls -f run_hls.tcl -tclargs csim
vitis_hls -f run_hls.tcl -tclargs synth
vitis_hls -f run_hls.tcl -tclargs cosim 6000 wide
python compare_cosim.py iq_demod_prj 500
vitis_hls -f run_hls.tcl -tclargs export     # -> iq_demod_ip.zip
vitis_hls -f run_hls.tcl -tclargs all 6000 wide   # 以上全部
```

## cosim 说明

IP 是 `ap_ctrl_none` 的自由运行流水线。cosim 框架从第 0 拍就开始灌 ADC 数据，但要花约 50 拍才把
AXI-Lite 寄存器写完，所以 RTL 前几十个采样是用全零寄存器处理的，而 C 模型从第 0 个采样起寄存器就有效。
框架自带的逐采样比对因此必然报不匹配（日志里的 `C TB post check failed` 是预期的）。`compare_cosim.py`
先对齐（RTL 比 C 滞后 1 个采样）、跳过寄存器生效和滤波器状态收敛的时间段，再比对。cosim 每次只跑一个用例
（寄存器不变），`wide` 用例滤波器时间常数短，最适合。RTL 的 NCO 比 C 晚约 60 拍开始走，起始相位不同，
所以稳定后仍有 ±1 LSB 的定点舍入差异，不是错误。RTL 与 C 的真正逐位一致性由 C 仿真保证（HLS 的 C→RTL 翻译）。

## 模型验证结果（run_float_sim.py）

- 相位锁定：±20 ppm 时钟偏差下输出相位残差恒定，拟合漂移在拟合噪声内（0 ppm 对照组量级相同）；
  ±20 ppm 对应 ∓0.53° 的恒定偏置，来自参考路滤波器在 400 Hz 的相移。
- 90° 相位阶跃，默认带宽：0 到 90% 用时 0.59 µs。
- 定点 vs 浮点：相位差 0.003° rms，幅度差 0.3 LSB rms；CORDIC 相位误差 0.01° rms。
- 镜像抑制：有陷波时 3 MHz 带宽下 40 MHz 镜像 −131 dBc（无陷波 −34 dBc）。

## Vivado 构建

```bash
cd vivado
vivado -mode batch -source build.tcl                    # 约 5 分钟，结果在 vivado/output/
vivado -mode batch -source build.tcl -tclargs C:/some/dir bd_only   # 只建 block design 做检查
```

Block design 和 Derek 的一样，只有三处不同：去掉了 HLS IP 到 DDR 的 m_axi / HP0 / axi_mem_intercon；
`cavity_relocker_0` 换成 `iq_demod_0`（levlab:hls:iq_demod:1.0）；没有 xlconstant。clk_wiz、PS7、
复位、ADC/DAC wrapper、引脚约束全部照抄。寄存器基地址 0x4000_0000。

**DAC 时钟（2026-10-15 修正）**：Derek 的 clk_wiz 把 ddr_clk/wrt_clk 都设成 125 MHz / 0°，上板后 OUT1 恒为直流、只有
OUT2 工作。这颗双通道 DAC 的两路数据在一个 125 MHz 周期内交错送出，需要 250 MHz 的写时钟才能两路都锁存。现在
`build.tcl` 用 Pavel Demin 原版参数：PLL，clk_out2 = 250 MHz @ 157.5°（ddr_clk），clk_out3 = 250 MHz @ 202.5°（wrt_clk）。
Derek 的锁腔只用了一路输出所以没发现。`adc_enc_p_o/n_o` 两个引脚仍无驱动（Derek 也没有），不影响工作。

**监视寄存器**：IP 多了一组只读寄存器（`mon_shift` 设窗口长度 2^n 个采样；`mon_in1/2_max/min` 输入极值；
`mon_o1/o2_sum` DAC 输出在窗口内的和；`mon_count` 窗口计数）。PYNQ 的 `register_map` 不显示 HLS 的输出寄存器，
所以驱动改为按 `pynq/xiq_demod_hw.h` 里的偏移直接 MMIO 读写。

## 上板

编译好的 `iq_demod.bit` + `iq_demod.hwh`（含 DAC 时钟修正和监视寄存器）在 GitHub Releases 里：
https://github.com/shepherdhenry/rp-external-lo-iq/releases ，不装 Vivado 也能直接用。

1. 用 PYNQ 镜像启动 RP（见上面"SD 卡与镜像"）。
2. `scp vivado/output/iq_demod.bit vivado/output/iq_demod.hwh pynq/*.py xilinx@rp-f0f9ad.local:~/iq/`
3. 按 `pynq/bringup.py` 开头的顺序做：`load` → `adc`（DAC 刻度/符号）→ `ch2`/`ch1`（NCO+混频+滤波）→
   `iq`（功分器，XY 模式 phasor 静止）→ `offset`（扣串扰）→ `magphase`。
4. 换频率：`iq.set_frequency(30e6)`，驱动同时改 ftw、notch_coef 并补偿增益。
5. 不用示波器看状态：`python3 bringup.py watch --mode iq --freq 20.578e6 --seconds 10`；找未知的 LO 频率：`python3 sweep.py 19.5 22`。

### 2026-10-15 验收记录（VCO 测试）

- 接线：VCO 主输出（约 5 dBm，有 50 Ω 终端）→ IN1，VCO 辅助输出（约 −8 dBm）→ IN2。板上读到 IN1 峰值 0.646 V、IN2 峰值 0.171 V，无削顶。
- `iq` 模式、NCO 20.7 MHz、增益 0.03：OUT1 = −2820、OUT2 = +602 LSB，5 秒内逐位不变（相位 167.94°，|Z| 2884），VCO 自身的相位噪声和漂移被抵消。
- 扫频：|Z| 峰值在 20.578 MHz，单峰，边缘按 100 kHz 滤波器形状衰减。
- 参考路带宽的实测影响（NCO 20.578 MHz，20 秒）：信号路/参考路 = 1 MHz/1 MHz 时相位 std 0.029°、漂移 +0.18°；
  1 MHz/80 kHz 时 std 0.54°、漂移 −3.5°。差别来自自由运行 VCO 的频率漂移乘以参考路滤波器的相位斜率（约 2/fc_ref）。
  结论：**默认让两路带宽相同**（驱动和 bringup 的默认值已改为相同），只有在参考信号很弱、需要压噪声时才把参考路调窄。
- 待做：功分器把同一信号接 IN1/IN2 长时间看漂移；PD 信号的实际带宽下调 `lpf` 参数；挡光测串扰偏置。
