
module bp_tournament #(
  parameter BIM_BITS  = 10,
  parameter GSH_BITS  = 10,
  parameter CHO_BITS  = 10,          // chooser (meta-predictor) table size
  parameter SPEC_HIST = 0            // Part 5: 1 = speculative GHR with repair
) (
  input  wire        clk,
  input  wire        rst,          // synchronous, active-high; clears history, NOT the tables

  // ---- predict port (combinational) ----
  input  wire        pred_valid,   // a branch is being predicted this cycle
  input  wire [31:0] pred_pc,
  output reg        pred_taken,   // must be valid in the same cycle (no flops on this path)
  output wire [63:0] pred_meta,    // anything you need at update time; tb hands it back

  // ---- update / resolve port (sequential, posedge) ----
  input  wire        upd_valid,
  input  wire [31:0] upd_pc,
  input  wire        upd_taken,    // actual outcome
  input  wire        upd_mispred,  // 1 if this branch was predicted wrong
  input  wire [63:0] upd_meta      // the pred_meta you produced for this branch
);

  // design
  reg [GSH_BITS-1:0] ghrr;
  reg [1:0] bimtbl [0 : (1 << BIM_BITS) - 1];
  reg [1:0] gshtbl [0 : (1 << GSH_BITS) - 1];
  reg [1:0] chotbl [0 : (1 << CHO_BITS) - 1];

  integer i;
  initial begin
    for (i = 0; i < (1 << BIM_BITS); i = i + 1) bimtbl[i] = 2'b01;
    for (i = 0; i < (1 << GSH_BITS); i = i + 1) gshtbl[i] = 2'b01;
    for (i = 0; i < (1 << CHO_BITS); i = i + 1) chotbl[i] = 2'b01;
  end


  function [1:0] update_ctr;
    input [1:0] ctr;
    input       taken;
    begin
      if (taken) begin
        if (ctr == 2'b11) update_ctr = ctr;
        else              update_ctr = ctr + 1'b1;
      end
      else begin
        if (ctr == 2'b00) update_ctr = ctr;
        else              update_ctr = ctr - 1'b1;
      end
    end
  endfunction

  wire [BIM_BITS-1:0] bim_pred_idx = pred_pc[BIM_BITS+1:2];           // same as bimodal
  wire [GSH_BITS-1:0] gsh_pred_idx = pred_pc[GSH_BITS+1:2] ^ ghrr;    // same as gshare
  wire [CHO_BITS-1:0] cho_pred_idx = pred_pc[CHO_BITS+1:2];           // PC-indexed, like bimodal

  wire [1:0] bim_pred_ctr = bimtbl[bim_pred_idx];
  wire [1:0] gsh_pred_ctr = gshtbl[gsh_pred_idx];
  wire [1:0] cho_pred_ctr = chotbl[cho_pred_idx];

  wire pb      = bim_pred_ctr[1];   // bimodal's prediction
  wire pg      = gsh_pred_ctr[1];   // gshare's prediction
  wire use_gsh = cho_pred_ctr[1];   // chooser: 1 = trust gshare

 
  
  always @(*) begin
    if (use_gsh)begin
      pred_taken = pg; 
    end
    else begin
      pred_taken = pb; 
    end
  end

  assign pred_meta = {{(64-GSH_BITS-2){1'b0}}, pg, pb, ghrr[GSH_BITS-1:0]};  

  wire [GSH_BITS-1:0] upd_ghr; 
  wire                upd_pb;           
  wire                upd_pg;       
  assign upd_ghr = upd_meta[GSH_BITS-1:0];   
  assign                upd_pb  = upd_meta[GSH_BITS]; 
  assign                upd_pg  = upd_meta[GSH_BITS+1];  

  // Indices for the resolving branch
  wire [BIM_BITS-1:0] bim_upd_idx = upd_pc[BIM_BITS+1:2];           // PC only: recompute is fine
  wire [GSH_BITS-1:0] gsh_upd_idx = upd_pc[GSH_BITS+1:2] ^ upd_ghr; // uses the SAVED history
  wire [CHO_BITS-1:0] cho_upd_idx = upd_pc[CHO_BITS+1:2];           // PC only: recompute is fine

  // Both component counters always learn the actual outcome (reused logic)
  always @(posedge clk) begin
    if (upd_valid) begin
      bimtbl[bim_upd_idx] <= update_ctr(bimtbl[bim_upd_idx], upd_taken);
      gshtbl[gsh_upd_idx] <= update_ctr(gshtbl[gsh_upd_idx], upd_taken);
    end
  end

  always @ (posedge clk) begin
    if ((upd_pb ^ upd_pg) && upd_valid) begin
      if (upd_pg == upd_taken) begin
        if (chotbl[cho_upd_idx] != 2'b11)
          chotbl[cho_upd_idx] <= chotbl[cho_upd_idx] + 1; 
      end

      else begin
        if (chotbl[cho_upd_idx] != 2'b00)
          chotbl[cho_upd_idx] <= chotbl[cho_upd_idx] - 1; 
      end
    end

  end

  always @(posedge clk) begin
    if (rst) begin
      ghrr <= {GSH_BITS{1'b0}};
    end
    else if (SPEC_HIST) begin
      if (upd_mispred && upd_valid) begin
        ghrr <= {upd_ghr[GSH_BITS-2:0], upd_taken}; 
      end
      else if (pred_valid) begin
        ghrr <= {ghrr[GSH_BITS-2:0], pred_taken}; 
      end
    end
    else begin
      if (upd_valid) begin
        ghrr <= {ghrr[GSH_BITS-2:0], upd_taken};
      end
    end
  end

endmodule