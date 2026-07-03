#!/usr/bin/env python3
"""K3 sweep: one QAD checkpoint (served as 'ckpt' on a given port) vs BF16 (:8001).
Reuses the fixed BF16 greedy samples (k3_vllm_bf16_samples.json) for the forward
direction so every step is scored on identical token sequences; reverse direction
samples fresh from the checkpoint. Appends results to k3_sweep_results.json.

Usage: k3_step_sweep.py <step_label> <ckpt_port>
"""
import json, sys, time
import numpy as np
sys.path.insert(0, "/home/c3-debug/.tmp/claude-0/-home-c3-debug-workspace-low-precision-project-turbo-skills/5a034cf5-4a26-413c-8eda-c5ed5c9f4c3b/scratchpad")
import k3_cross_model_vllm as K
from k3_cross_model_vllm import encode_prompts, sample_all, run_pair

SCRATCH = "/home/c3-debug/.tmp/claude-0/-home-c3-debug-workspace-low-precision-project-turbo-skills/5a034cf5-4a26-413c-8eda-c5ed5c9f4c3b/scratchpad"
OUT = f"{SCRATCH}/k3_sweep_results.json"

def main():
    step, port = sys.argv[1], int(sys.argv[2])
    K.MODELS["ckpt"] = (f"http://127.0.0.1:{port}", "ckpt")
    encoded = encode_prompts()
    bf16_samples = [(ids, lps) for ids, lps in json.load(open(f"{SCRATCH}/k3_vllm_bf16_samples.json"))]
    report = {}
    run_pair(encoded, bf16_samples, "ckpt", "bf16", report)     # fwd: bf16 samples scored on ckpt
    ckpt_samples = sample_all(encoded, "ckpt")
    run_pair(encoded, ckpt_samples, "bf16", "ckpt", report)     # rev: ckpt samples scored on bf16
    fwd = report["bf16->ckpt"]["splits"]["all"]
    rev = report["ckpt->bf16"]["splits"]["all"]
    # merge into sweep file (atomic-ish append keyed by step)
    try:
        sweep = json.load(open(OUT))
    except Exception:
        sweep = {}
    sweep[step] = dict(fwd=fwd, rev=rev,
                       fwd_k3=fwd["k3"]["mean"], rev_k3=rev["k3"]["mean"],
                       fwd_k1=fwd["k1"]["mean"], rev_k1=rev["k1"]["mean"])
    json.dump(sweep, open(OUT, "w"), indent=1)
    print(f"SWEEP {step}: fwd_k3={fwd['k3']['mean']:.5f} rev_k3={rev['k3']['mean']:.5f}", flush=True)

if __name__ == "__main__":
    main()
