# Vivado 2023.1 build script: Red Pitaya STEMlab 125-14 + iq_demod HLS IP -> iq_demod.bit / iq_demod.hwh
#
#   vivado -mode batch -source build.tcl                      (full build, default local build dir)
#   vivado -mode batch -source build.tcl -tclargs <build_dir> [bd_only]
#
# Block design = Derek's RedPitayaCavityRelocker design with the HLS IP swapped and the DDR/HP0 path removed:
#   PS7 -- M_AXI_GP0 --> axi_interconnect --> iq_demod.s_axi_ctrl
#   adc pins -> axis_red_pitaya_adc -> iq_demod.adc_in ; iq_demod.dac_out -> axis_red_pitaya_dac -> dac pins
#   clk_wiz(125 MHz ADC clock in) clk_out1 drives everything; clk_out2/3 feed the DAC ddr/wrt clocks.
#
# The project is built on a LOCAL disk (Vivado on a network share is slow and flaky); results are copied
# back to <script_dir>/output/.

set script_dir [file dirname [file normalize [info script]]]
set build_dir  "C:/Users/Dycontrol4/rp_iq_vivado_build"
set bd_only    0
if { $argc > 0 } { set build_dir [lindex $argv 0] }
if { $argc > 1 && [lindex $argv 1] eq "bd_only" } { set bd_only 1 }

set proj     rp_iq
set part     xc7z010clg400-1
set ip_zip   [file normalize "$script_dir/../hls/iq_demod_ip.zip"]
set out_dir  "$script_dir/output"
set n_jobs   4

puts "build_dir = $build_dir"
puts "ip_zip    = $ip_zip"
if { ![file exists $ip_zip] } { error "HLS IP not found: $ip_zip (run vitis_hls ... export first)" }

# ---------------------------------------------------------------------------------------------
# IP repository: unzip the HLS export
# ---------------------------------------------------------------------------------------------
set ip_repo "$build_dir/ip_repo"
set ip_dir  "$ip_repo/iq_demod"
file delete -force $ip_dir
file mkdir $ip_dir
if { $::tcl_platform(platform) eq "windows" } {
    exec powershell -NoProfile -Command "Expand-Archive -Force -LiteralPath '$ip_zip' -DestinationPath '$ip_dir'"
} else {
    exec unzip -o -q $ip_zip -d $ip_dir
}
if { ![file exists "$ip_dir/component.xml"] } { error "IP unzip failed" }

# ---------------------------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------------------------
file mkdir $build_dir
create_project $proj "$build_dir/$proj" -part $part -force
set_property target_language Verilog [current_project]
set_property ip_repo_paths [list $ip_repo] [current_project]
update_ip_catalog

add_files -norecurse [list "$script_dir/src/axis_red_pitaya_adc.v" "$script_dir/src/axis_red_pitaya_dac.v"]
add_files -fileset constrs_1 -norecurse "$script_dir/src/constraints.xdc"
update_compile_order -fileset sources_1

# ---------------------------------------------------------------------------------------------
# Block design
# ---------------------------------------------------------------------------------------------
set design_name design_1
create_bd_design $design_name
current_bd_design $design_name

# external ports (same names as Derek's design / constraints.xdc)
set DDR      [create_bd_intf_port -mode Master -vlnv xilinx.com:interface:ddrx_rtl:1.0 DDR]
set FIXED_IO [create_bd_intf_port -mode Master -vlnv xilinx.com:display_processing_system7:fixedio_rtl:1.0 FIXED_IO]
create_bd_port -dir I adc_clk_p_i
create_bd_port -dir I adc_clk_n_i
create_bd_port -dir I -from 15 -to 0 adc_dat_a_i
create_bd_port -dir I -from 15 -to 0 adc_dat_b_i
create_bd_port -dir O adc_csn_o
create_bd_port -dir O adc_enc_p_o
create_bd_port -dir O adc_enc_n_o
create_bd_port -dir O dac_clk_o
create_bd_port -dir O dac_rst_o
create_bd_port -dir O dac_sel_o
create_bd_port -dir O dac_wrt_o
create_bd_port -dir O -from 13 -to 0 dac_dat_o

# PS7 (same minimal config as Derek: PYNQ's boot firmware has already configured the PS)
set ps7 [create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7:5.5 processing_system7_0]
set_property -dict [list CONFIG.PCW_FPGA_FCLK0_ENABLE {1} CONFIG.PCW_USE_S_AXI_HP0 {0}] $ps7

set psr [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 proc_sys_reset_0]

# clock wizard, Pavel Demin's red-pitaya-notes configuration (PLL, 125 MHz differential ADC clock in):
#   clk_out1 125 MHz          -> all logic, ADC/DAC wrappers
#   clk_out2 250 MHz @ 157.5  -> DAC ddr_clk (the dual DAC takes both channels interleaved per 125 MHz cycle)
#   clk_out3 250 MHz @ 202.5  -> DAC wrt_clk
# Derek's design had all three at 125 MHz / 0 deg; with that the DAC latched only channel B (OUT1 stayed DC).
set clk_wiz [create_bd_cell -type ip -vlnv xilinx.com:ip:clk_wiz:6.0 clk_wiz_0]
set_property -dict [list     CONFIG.PRIMITIVE {PLL}     CONFIG.PRIM_SOURCE {Differential_clock_capable_pin}     CONFIG.PRIM_IN_FREQ {125.000}     CONFIG.CLKIN1_JITTER_PS {80.0}     CONFIG.CLKOUT1_USED {true}     CONFIG.CLKOUT1_REQUESTED_OUT_FREQ {125.000}     CONFIG.CLKOUT2_USED {true}     CONFIG.CLKOUT2_REQUESTED_OUT_FREQ {250.000}     CONFIG.CLKOUT2_REQUESTED_PHASE {157.5}     CONFIG.CLKOUT3_USED {true}     CONFIG.CLKOUT3_REQUESTED_OUT_FREQ {250.000}     CONFIG.CLKOUT3_REQUESTED_PHASE {202.5}     CONFIG.USE_RESET {false}     CONFIG.USE_LOCKED {true} ] $clk_wiz

# ADC / DAC wrappers as module references (Verilog sources added above)
set adc [create_bd_cell -type module -reference axis_red_pitaya_adc axis_red_pitaya_adc_0]
set dac [create_bd_cell -type module -reference axis_red_pitaya_dac axis_red_pitaya_dac_0]

# the HLS IP
set iq [create_bd_cell -type ip -vlnv levlab:hls:iq_demod:1.0 iq_demod_0]

# AXI-Lite interconnect PS -> IP
set ic [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 ps7_0_axi_periph]
set_property CONFIG.NUM_MI {1} $ic

# ---- interface connections
connect_bd_intf_net [get_bd_intf_ports DDR]      [get_bd_intf_pins processing_system7_0/DDR]
connect_bd_intf_net [get_bd_intf_ports FIXED_IO] [get_bd_intf_pins processing_system7_0/FIXED_IO]
connect_bd_intf_net [get_bd_intf_pins processing_system7_0/M_AXI_GP0] [get_bd_intf_pins ps7_0_axi_periph/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins ps7_0_axi_periph/M00_AXI]       [get_bd_intf_pins iq_demod_0/s_axi_ctrl]
connect_bd_intf_net [get_bd_intf_pins axis_red_pitaya_adc_0/m_axis]   [get_bd_intf_pins iq_demod_0/adc_in]
connect_bd_intf_net [get_bd_intf_pins iq_demod_0/dac_out]             [get_bd_intf_pins axis_red_pitaya_dac_0/s_axis]

# ---- ADC / DAC pins
connect_bd_net [get_bd_ports adc_clk_p_i] [get_bd_pins clk_wiz_0/clk_in1_p]
connect_bd_net [get_bd_ports adc_clk_n_i] [get_bd_pins clk_wiz_0/clk_in1_n]
connect_bd_net [get_bd_ports adc_dat_a_i] [get_bd_pins axis_red_pitaya_adc_0/adc_dat_a]
connect_bd_net [get_bd_ports adc_dat_b_i] [get_bd_pins axis_red_pitaya_adc_0/adc_dat_b]
connect_bd_net [get_bd_ports adc_csn_o]   [get_bd_pins axis_red_pitaya_adc_0/adc_csn]
connect_bd_net [get_bd_ports dac_clk_o]   [get_bd_pins axis_red_pitaya_dac_0/dac_clk]
connect_bd_net [get_bd_ports dac_rst_o]   [get_bd_pins axis_red_pitaya_dac_0/dac_rst]
connect_bd_net [get_bd_ports dac_sel_o]   [get_bd_pins axis_red_pitaya_dac_0/dac_sel]
connect_bd_net [get_bd_ports dac_wrt_o]   [get_bd_pins axis_red_pitaya_dac_0/dac_wrt]
connect_bd_net [get_bd_ports dac_dat_o]   [get_bd_pins axis_red_pitaya_dac_0/dac_dat]
# adc_enc_p_o / adc_enc_n_o are left undriven, as in Derek's working design (the 125-14 ADC clock
# comes from the on-board oscillator, not from the FPGA)

# ---- clocks
connect_bd_net [get_bd_pins clk_wiz_0/clk_out1] \
    [get_bd_pins axis_red_pitaya_adc_0/aclk] \
    [get_bd_pins axis_red_pitaya_dac_0/aclk] \
    [get_bd_pins iq_demod_0/ap_clk] \
    [get_bd_pins proc_sys_reset_0/slowest_sync_clk] \
    [get_bd_pins processing_system7_0/M_AXI_GP0_ACLK] \
    [get_bd_pins ps7_0_axi_periph/ACLK] \
    [get_bd_pins ps7_0_axi_periph/S00_ACLK] \
    [get_bd_pins ps7_0_axi_periph/M00_ACLK]
connect_bd_net [get_bd_pins clk_wiz_0/clk_out2] [get_bd_pins axis_red_pitaya_dac_0/ddr_clk]
connect_bd_net [get_bd_pins clk_wiz_0/clk_out3] [get_bd_pins axis_red_pitaya_dac_0/wrt_clk]
connect_bd_net [get_bd_pins clk_wiz_0/locked] \
    [get_bd_pins proc_sys_reset_0/dcm_locked] \
    [get_bd_pins axis_red_pitaya_dac_0/locked]

# ---- resets
connect_bd_net [get_bd_pins processing_system7_0/FCLK_RESET0_N] [get_bd_pins proc_sys_reset_0/ext_reset_in]
connect_bd_net [get_bd_pins proc_sys_reset_0/peripheral_aresetn] \
    [get_bd_pins ps7_0_axi_periph/ARESETN] \
    [get_bd_pins ps7_0_axi_periph/S00_ARESETN] \
    [get_bd_pins ps7_0_axi_periph/M00_ARESETN] \
    [get_bd_pins iq_demod_0/ap_rst_n]

# ---- address map: iq_demod registers at 0x4000_0000
assign_bd_address -offset 0x40000000 -range 0x00010000 \
    -target_address_space [get_bd_addr_spaces processing_system7_0/Data] \
    [get_bd_addr_segs iq_demod_0/s_axi_ctrl/Reg] -force

regenerate_bd_layout
validate_bd_design
save_bd_design

# wrapper
set wrapper [make_wrapper -files [get_files $design_name.bd] -top]
add_files -norecurse $wrapper
set_property top ${design_name}_wrapper [current_fileset]
update_compile_order -fileset sources_1

if { $bd_only } {
    puts "bd_only: block design created and validated, stopping before synthesis."
    return
}

# ---------------------------------------------------------------------------------------------
# Synthesis, implementation, bitstream
# ---------------------------------------------------------------------------------------------
generate_target all [get_files $design_name.bd]
launch_runs synth_1 -jobs $n_jobs
wait_on_run synth_1
if { [get_property PROGRESS [get_runs synth_1]] ne "100%" } { error "synthesis failed" }

launch_runs impl_1 -to_step write_bitstream -jobs $n_jobs
wait_on_run impl_1
if { [get_property PROGRESS [get_runs impl_1]] ne "100%" } { error "implementation failed" }

# ---------------------------------------------------------------------------------------------
# Reports and outputs
# ---------------------------------------------------------------------------------------------
file mkdir $out_dir
open_run impl_1
report_timing_summary -delay_type min_max -report_unconstrained -max_paths 10 -file "$out_dir/timing_summary.rpt"
report_utilization -hierarchical -file "$out_dir/utilization.rpt"
set wns [get_property STATS.WNS [get_runs impl_1]]
set whs [get_property STATS.WHS [get_runs impl_1]]
set tns [get_property STATS.TNS [get_runs impl_1]]

set bit "$build_dir/$proj/$proj.runs/impl_1/${design_name}_wrapper.bit"
set hwh "$build_dir/$proj/$proj.gen/sources_1/bd/$design_name/hw_handoff/$design_name.hwh"
file copy -force $bit "$out_dir/iq_demod.bit"
file copy -force $hwh "$out_dir/iq_demod.hwh"

set f [open "$out_dir/build_summary.txt" w]
puts $f "build: [clock format [clock seconds]]"
puts $f "WNS = $wns ns   WHS = $whs ns   TNS = $tns ns   (timing met if WNS >= 0 and WHS >= 0)"
puts $f "bit: $out_dir/iq_demod.bit"
puts $f "hwh: $out_dir/iq_demod.hwh"
close $f
puts "=================================================================="
puts " WNS = $wns ns   WHS = $whs ns   TNS = $tns ns"
puts " outputs in $out_dir"
puts "=================================================================="
