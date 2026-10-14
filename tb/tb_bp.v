// ============================================================================
// tb_bp.v  --  trace-driven, cycle-level testbench for the branch predictor lab
// ----------------------------------------------------------------------------
// Pipeline model (must match model/golden.py exactly):
//   * Branch i is PREDICTED in some cycle c (pred_valid=1). The DUT's
//     prediction is combinational and is sampled before the clock edge.
//   * It RESOLVES (upd_valid=1) in the first cycle >= c + LAT where it is the
//     oldest in-flight branch.
//   * If it mispredicted, all younger in-flight branches are squashed (their
//     updates never happen) and fetch rewinds to branch i+1. No predict is
//     issued in the flush cycle.
//   * A new predict is issued only if fewer than DEPTH branches were in
//     flight at the START of the cycle. DEPTH=1, LAT=1 = fully serial
//     (predict, update, predict, update, ...): Parts 1-4.
//
// Plusargs:
//   +TRACE=<file.hex>  +N=<#branches>  [+LAT=1] [+DEPTH=1] [+LOG=rtl_pred.log]
//   [+VCD=<file.vcd>]  dump waveforms (use with a small N, e.g. run.py --limit 200)
// Defines (pick the DUT and its sizes):
//   -DBP_BIMODAL | -DBP_GSHARE | -DBP_TOURNAMENT (default)
//   -DBIM_BITS=10 -DGSH_BITS=10 -DCHO_BITS=10 -DSPEC_HIST=0
//
// You should not need to edit this file.
// ============================================================================
`timescale 1ns/1ps

`ifndef BIM_BITS
  `define BIM_BITS 10
`endif
`ifndef GSH_BITS
  `define GSH_BITS 10
`endif
`ifndef CHO_BITS
  `define CHO_BITS 10
`endif
`ifndef SPEC_HIST
  `define SPEC_HIST 0
`endif

module tb_bp;

  // ---------------- clock / reset ----------------
  reg clk = 1'b0;
  always #5 clk = ~clk;
  reg rst;

  // ---------------- DUT interface ----------------
  reg         pred_valid;
  reg  [31:0] pred_pc;
  wire        pred_taken;
  wire [63:0] pred_meta;

  reg         upd_valid;
  reg  [31:0] upd_pc;
  reg         upd_taken;
  reg         upd_mispred;
  reg  [63:0] upd_meta;

`ifdef BP_BIMODAL
  bp_bimodal #(.BIM_BITS(`BIM_BITS)) dut (
`elsif BP_GSHARE
  bp_gshare #(.GSH_BITS(`GSH_BITS)) dut (
`else
  bp_tournament #(.BIM_BITS(`BIM_BITS), .GSH_BITS(`GSH_BITS),
                  .CHO_BITS(`CHO_BITS), .SPEC_HIST(`SPEC_HIST)) dut (
`endif
    .clk(clk), .rst(rst),
    .pred_valid(pred_valid), .pred_pc(pred_pc),
    .pred_taken(pred_taken), .pred_meta(pred_meta),
    .upd_valid(upd_valid), .upd_pc(upd_pc), .upd_taken(upd_taken),
    .upd_mispred(upd_mispred), .upd_meta(upd_meta)
  );

  // ---------------- trace + in-flight queue ----------------
  localparam MAXN = 1 << 20;
  localparam QD   = 64;

  reg [35:0] trace [0:MAXN-1];

  integer q_ti   [0:QD-1];
  reg     q_pred [0:QD-1];
  reg [63:0] q_meta [0:QD-1];
  integer q_cyc  [0:QD-1];
  integer q_head, q_tail, q_cnt;

  reg [8*512-1:0] tracefile, logfile, vcdfile;
  integer N, LAT, DEPTH, fd;
  integer nxt, cyc, occ, ti;
  integer resolved, correct, npred, nflush;
  reg do_upd, do_pred, flush, p_taken, u_pred;
  reg [63:0] p_meta;
  real acc;

  initial begin
    if (!$value$plusargs("TRACE=%s", tracefile)) begin
      $display("ERROR: +TRACE=<file> required"); $finish;
    end
    if (!$value$plusargs("N=%d", N)) begin
      $display("ERROR: +N=<branches> required"); $finish;
    end
    if (!$value$plusargs("LAT=%d", LAT))     LAT = 1;
    if (!$value$plusargs("DEPTH=%d", DEPTH)) DEPTH = 1;
    if (!$value$plusargs("LOG=%s", logfile)) logfile = "rtl_pred.log";
    if (DEPTH < 1 || DEPTH > QD) begin
      $display("ERROR: DEPTH must be 1..%0d", QD); $finish;
    end
    if (N > MAXN) begin
      $display("ERROR: N too large"); $finish;
    end
    $readmemh(tracefile, trace, 0, N-1);
    if ($value$plusargs("VCD=%s", vcdfile)) begin
      $dumpfile(vcdfile);
      $dumpvars(0, tb_bp);
    end
    fd = $fopen(logfile, "w");

    pred_valid = 0; pred_pc = 0;
    upd_valid = 0; upd_pc = 0; upd_taken = 0; upd_mispred = 0; upd_meta = 0;
    q_head = 0; q_tail = 0; q_cnt = 0;
    nxt = 0; cyc = 0; resolved = 0; correct = 0; npred = 0; nflush = 0;

    rst = 1'b1;
    repeat (3) @(posedge clk);
    @(negedge clk);
    rst = 1'b0;

    // ---------------- main loop: one iteration per cycle ----------------
    // Inputs are driven at the negedge; combinational outputs are sampled
    // just before the posedge, where the DUT commits state.
    while (nxt < N || q_cnt > 0) begin
      occ    = q_cnt;
      do_upd = 0;
      flush  = 0;

      // resolve oldest in-flight branch if it is old enough
      if (q_cnt > 0 && (q_cyc[q_head] + LAT) <= cyc) begin
        do_upd = 1;
        ti     = q_ti[q_head];
        u_pred = q_pred[q_head];
        upd_meta = q_meta[q_head];
        q_head = (q_head + 1) % QD;
        q_cnt  = q_cnt - 1;
        upd_pc    = trace[ti][31:0];
        upd_taken = trace[ti][32];
        upd_mispred = (u_pred != trace[ti][32]);
        flush = upd_mispred;
      end
      upd_valid = do_upd;

      // fetch a new branch?
      do_pred    = (!flush) && (nxt < N) && (occ < DEPTH);
      pred_valid = do_pred;
      pred_pc    = do_pred ? trace[nxt][31:0] : 32'h0;

      #1; // let combinational prediction settle
      p_taken = pred_taken;
      p_meta  = pred_meta;
      if (do_pred) begin
        if (p_taken !== 1'b0 && p_taken !== 1'b1) begin
          $display("ERROR: pred_taken is X/Z at branch %0d (pc=%h), cycle %0d",
                   nxt, pred_pc, cyc);
          $fclose(fd); $finish;
        end
        $fdisplay(fd, "P %0d %0d", nxt, p_taken);
        npred = npred + 1;
      end

      if (do_upd) begin
        resolved = resolved + 1;
        if (!upd_mispred) correct = correct + 1;
        if (flush) begin
          nflush = nflush + 1;
          q_head = 0; q_tail = 0; q_cnt = 0;
          nxt    = ti + 1;
        end
      end

      if (do_pred) begin
        q_ti[q_tail]   = nxt;
        q_pred[q_tail] = p_taken;
        q_meta[q_tail] = p_meta;
        q_cyc[q_tail]  = cyc;
        q_tail = (q_tail + 1) % QD;
        q_cnt  = q_cnt + 1;
        nxt    = nxt + 1;
      end

      @(negedge clk);   // posedge happens in between: DUT updates state
      cyc = cyc + 1;
    end

    pred_valid = 0; upd_valid = 0;
    @(negedge clk);
    $fclose(fd);
    acc = 100.0 * correct / (resolved > 0 ? resolved : 1);
    $display("branches=%0d correct=%0d mispredicts=%0d predicts=%0d flushes=%0d cycles=%0d accuracy=%0.3f",
             resolved, correct, resolved - correct, npred, nflush, cyc, acc);
    $finish;
  end

endmodule
