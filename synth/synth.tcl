# ============================================================================
# synth.tcl -- out-of-context synthesis + place & route of your tournament
# predictor on the Nexys A7-100T part, to get real area and timing numbers.
#
# Run from the bp_lab/ directory:
#   vivado -mode batch -nojournal -nolog -source synth/synth.tcl -tclargs 10 0 4.0
#                                                     index bits ^  ^spec ^clock period (ns)
# Reports land in results/synth_b<bits>_s<spec>_*.rpt and a one-line summary
# is printed at the end (LUTs, LUTRAM, FFs, WNS, Fmax).
# ============================================================================
set bits   [expr {[llength $argv] > 0 ? [lindex $argv 0] : 10}]
set spec   [expr {[llength $argv] > 1 ? [lindex $argv 1] : 0}]
set period [expr {[llength $argv] > 2 ? [lindex $argv 2] : 4.0}]
set part   xc7a100tcsg324-1
set tag    "b${bits}_s${spec}"

file mkdir results
read_verilog [glob rtl/*.v]

synth_design -top bp_tournament -part $part -mode out_of_context \
  -generic BIM_BITS=$bits -generic GSH_BITS=$bits -generic CHO_BITS=$bits \
  -generic SPEC_HIST=$spec

# Constrain the clock AND the ports, so the combinational predict path
# (pred_pc -> table read -> mux -> pred_taken) is timed, not ignored.
create_clock -name clk -period $period [get_ports clk]
set_input_delay  -clock clk 0.0 [get_ports -filter {DIRECTION == IN && NAME != clk}]
set_output_delay -clock clk 0.0 [get_ports -filter {DIRECTION == OUT}]

opt_design
place_design
route_design

report_utilization    -file results/synth_${tag}_util.rpt
report_timing_summary -max_paths 5 -file results/synth_${tag}_timing.rpt
report_power          -file results/synth_${tag}_power.rpt

set wns    [get_property SLACK [get_timing_paths -max_paths 1 -setup]]
set fmax   [expr {1000.0 / ($period - $wns)}]
set luts   [llength [get_cells -hier -filter {PRIMITIVE_GROUP == LUT}]]
set lutram [llength [get_cells -hier -filter {PRIMITIVE_GROUP == DMEM}]]
set ffs    [llength [get_cells -hier -filter {PRIMITIVE_GROUP == FLOP_LATCH}]]
set bram   [llength [get_cells -hier -filter {PRIMITIVE_GROUP == BMEM}]]
puts "SUMMARY bits=$bits spec=$spec period=${period}ns WNS=${wns}ns Fmax=[format %.1f $fmax]MHz LUT=$luts LUTRAM=$lutram FF=$ffs BRAM=$bram"
