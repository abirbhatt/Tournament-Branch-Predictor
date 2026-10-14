
module bp_gshare #(
  parameter GSH_BITS = 10           // table has 2^GSH_BITS counters; GHR is GSH_BITS wide
) (
  input  wire        clk,
  input  wire        rst,          // synchronous, active-high; clears history, NOT the tables

  // ---- predict port (combinational) ----
  input  wire        pred_valid,   // a branch is being predicted this cycle
  input  wire [31:0] pred_pc,
  output wire        pred_taken,   // must be valid in the same cycle (no flops on this path)
  output wire [63:0] pred_meta,    // anything you need at update time; tb hands it back

  // ---- update / resolve port (sequential, posedge) ----
  input  wire        upd_valid,
  input  wire [31:0] upd_pc,
  input  wire        upd_taken,    // actual outcome
  input  wire        upd_mispred,  // 1 if this branch was predicted wrong
  input  wire [63:0] upd_meta      // the pred_meta you produced for this branch
);

  // design
  wire [GSH_BITS-1:0] pred_idx;
  wire [GSH_BITS-1:0] upd_idx;
  wire [1:0] pred_ctr; 
  wire [1:0] upd_ctr; 
  reg [GSH_BITS-1:0] ghrr; 
  reg [1:0] tbl [0 : (1 << GSH_BITS) - 1]; 

  integer i; 

  initial begin
    
    for (i = 0; i < (1 << GSH_BITS); i = i + 1) begin
      tbl[i] = 2'b01; 
    end

  end

  function [1:0] update_ctr; 
    input [1:0] ctr; 
    input taken; 
    begin
      if (taken) begin
        if (ctr == 2'b11)
          update_ctr = ctr;
        else
          update_ctr = ctr + 1'b1;
      end
      else begin
        if (ctr == 2'b00)
          update_ctr = ctr;
        else
          update_ctr = ctr - 1'b1;
      end
    end
  endfunction

  assign pred_idx = pred_pc[GSH_BITS+1:2] ^ ghrr; 
  assign pred_ctr = tbl[pred_idx]; 
  assign pred_taken = pred_ctr[1];

  assign pred_meta  = {{(64-GSH_BITS){1'b0}}, pred_idx};

  assign upd_idx = upd_meta[GSH_BITS-1:0];
  assign upd_ctr = tbl[upd_idx];

  always @ (posedge clk) begin
    if (rst) begin
      ghrr <= {GSH_BITS{1'b0}};
    end

    else if (upd_valid) begin
      ghrr <= {ghrr[GSH_BITS-2:0], upd_taken};
    end

  end

  always @ (posedge clk) begin

    if (upd_valid) begin
      tbl[upd_idx] <= update_ctr(upd_ctr, upd_taken); 
    end

  end


endmodule
