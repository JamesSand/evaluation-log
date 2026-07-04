# Investigating TRT-LLM's NVFP4 KV Cache Accuracy Loss

We observed a large accuracy degradation with NVFP4 KV cache vs. FP8 KV cache in TRT-LLM. Our hypothesis is that this degradation does not come from the precision loss of NVFP4 alone — there may be a component of the TRT-LLM engine (other kernels, or potentially a bug) responsible for the unusual drop. To evaluate this, we built a simulated KV cache quantization in SGLang that matches the precision of TRT-LLM's NVFP4 exactly, and ran the same evaluations. The simulated path shows significantly smaller degradation, pointing to the TRT-LLM engine — not the NVFP4 KV cache — as the source of the accuracy loss.

# Method: simulated NVFP4 KV cache in SGLang

We added a Triton kernel in SGLang that quantizes each token's KV from BF16 to NVFP4 precision before it is written to the page table. The KV cache is still stored in BF16, but the values have been rounded to NVFP4 — so attention runs on values exactly as lossy as real NVFP4, without needing custom FP4 attention kernels. We use the same 2-level scaling as TRT-LLM (FP32 per-tensor global scale + FP8 per-group scales) with the same calibrated FP32 scales, so quantization error is identical.

## Real vs. Simulated KV Quantization

**TRT-LLM (NVFP4 KV cache quantization):**
BF16 QKV projection output → attention norm + RoPE → quantize to NVFP4 (2-level: FP32 global scale + FP8 group scales) → write to KV cache page table as NVFP4 → attention computed on NVFP4 KV cache

**SGLang (simulated NVFP4 KV cache quantization):**
BF16 QKV projection output → attention norm + RoPE → quantize to NVFP4 (2-level: FP32 global scale + FP8 group scales), then dequantize back to BF16 → write to KV cache page table as BF16 → attention computed on BF16 KV cacheni

**SGLang (FP8 KV cache + `trtllm_mha` attention backend, simulated NVFP4 KV cache quantization):**
BF16 QKV projection output → attention norm + RoPE → quantize to NVFP4 (2-level: FP32 global scale + FP8 group scales), then dequantize back to BF16 → requantize to FP8 and write to KV cache page table as FP8 → attention computed on FP8 KV cache (FP8 matmul)

# Experiment 1 — Qwen3-8B microbenchmarks (32K context)

| Backend / KV Cache | ruler_vt | Δ vs. bf16 | niah_mk2 | Δ vs. bf16 |
| --- | --- | --- | --- | --- |
| sglang BF16 (full KV) | 0.9996 | — | 0.9580 | — |
| sglang fake_nvfp4 (1-level) | 0.9984 | −0.12 pp | 0.9180 | −4.00 pp |
| sglang fake_nvfp4_2level | 0.9944 | −0.52 pp | 0.9080 | −5.00 pp |
| TRT-LLM NVFP4 (real) | 0.9744 | −2.52 pp | 0.8640 | −9.40 pp |

The simulated 2-level path — which matches TRT-LLM's NVFP4 scheme exactly in precision — degrades far less than TRT-LLM's real NVFP4. The remaining gap (~2 pp on ruler_vt, ~4.4 pp on niah_mk2) is not explained by quantization error and points at the TRT-LLM execution path.

# Experiment 2 — SWE-bench Verified on SWE-1.6 (FP8 weights, first 100 instances)

| KV Cache | Engine | 16K reasoning | 32K reasoning |
| --- | --- | --- | --- |
| fake_nvfp4_2level | SGLang | 31 | 32 |
| Real NVFP4 | TRT-LLM 1.3.0rc11 | 20 | 23 |

Same pattern on a real coding workload: TRT-LLM resolves 9–11 fewer instances despite using the same nominal precision as the simulation.

# Experiment 3 — Long-context lm-eval on SWE-1.6 (32K)

| KV Cache | Engine | ruler_vt | niah_multikey_2 |
| --- | --- | --- | --- |
| BF16 | SGLang | 1.000 | 0.994 |
| fake_nvfp4_2level | SGLang | 1.000 | 0.992 |
| Real NVFP4 | TRT-LLM | 0.7424 | 0.98 |

The sharpest gap so far. Simulated NVFP4 is essentially indistinguishable from BF16 on ruler_vt; TRT-LLM drops 25.8 pp on the same benchmark.

# Experiment 4 — Is it the `trtllm_mha` attention backend with FP8 matmul?

![image.png](Investigating%20TRT-LLM's%20NVFP4%20KV%20Cache%20Accuracy%20Lo/image.png)

A natural follow-up hypothesis: the loss might come specifically from TRT-LLM's `trtllm_mha` attention backend running with FP8 matmul. To test this, we ported the simulated NVFP4 KV cache path into SGLang running the same `trtllm_mha` backend with FP8 matmul. On long-context benchmarks, this configuration shows no noticeable degradation. **The attention backend is not the cause.** The source of TRT-LLM's degradation remains unidentified.

# Where this leaves us

Across four experiments, the simulated NVFP4 path tracks BF16 closely, while TRT-LLM's real NVFP4 degrades sharply. The two share quantization scheme, scales, and — per Experiment 4 — the attention backend, yet the gap persists. The accuracy loss is somewhere in TRT-LLM's implementation, but we have not yet localized it.

# Next step

Layer-by-layer output comparison between SGLang's simulated path and TRT-LLM's real path on the same inputs, to find where outputs first diverge.

# Comparing the Intermediate Activations of TRTLLM and SGLang

When using different KV cache quantization (FP8 vs NVFP4), the intermediate model activations in the same engine are relatively similar. However, the intermediate activations of attn_inner_out (the output of FlashAttention) from different engines (TRTLLM vs SGLang) with the same KV cache quantization is highly different.

Findings:

- TRTLLM w. NVFP4 KV cache quantization uses FP8 KV cache during prefill
- Activation differences between TRTLLM w. FP8 KV and SGLang w. FP8 KV (trtllm_mha attention backend) is surprising big
- Activation differences between TRTLLM w. FP8 KV and TRTLLM w. NVFP4 KV is much higher than the differences between SGLang w. FP8 KV and SGLang w. NVFP4 KV.

#### TRTLLM FP8 KV vs SGLang FP8 KV

![trtllm_fp8_vs_sglang_fp8.png](Investigating%20TRT-LLM's%20NVFP4%20KV%20Cache%20Accuracy%20Lo/trtllm_fp8_vs_sglang_fp8.png)

#### TRTLLM NVFP4 KV vs SGLang NVFP4 KV

![trtllm_nvfp4_vs_sglang_nvfp4.png](Investigating%20TRT-LLM's%20NVFP4%20KV%20Cache%20Accuracy%20Lo/trtllm_nvfp4_vs_sglang_nvfp4.png)

#### SGLang FP8 KV vs SGLang NVFP4 KV

![sglang_fp8_vs_nvfp4.png](Investigating%20TRT-LLM's%20NVFP4%20KV%20Cache%20Accuracy%20Lo/sglang_fp8_vs_nvfp4.png)

#### TRTLLM FP8 KV vs TRTLLM NVFP4 KV

![trtllm_fp8_vs_nvfp4.png](Investigating%20TRT-LLM's%20NVFP4%20KV%20Cache%20Accuracy%20Lo/trtllm_fp8_vs_nvfp4.png)

# TRT Attention Kernel for NVFP4 KV Cache

When using NVFP4 KV cache in TRT, the FP8 attention kernel is used. Matrix multiplications are done in FP8 with FP32 accumulators. We identified where the precision mismatch comes from: FP8 attention merges o_proj input scaling factor into its FP8 outputs, whereas SGLang does not.

We identified **3 opportunities** for improving the model quality with NVFP4 KV cache:

1. TRT does not use a Q scale for quantizing the Q activations to FP8 (easy fix in the engine)
2. TRT does not support FP8 o_proj with NVFP4 MLP/MoE yet (should be a fix without kernel changes)
    1. TRT supports BF16 attention + NVFP4 MLP/MoE layers
    2. TRT does not support FP8 attention + NVFP4 MLP/MoE layers
    3. TRT NVFP4 KV cache requires O proj weights to be FP8 or NVFP4
3. There is an asymmetry of precision in Q (FP8) and K (NVFP4). We can learn scaling factors to scale K norm weights and inversely scale Q norm weights to mitigate outliers in K activations while keeping outputs exactly the same. K activations are the primary source of quantization errors due to outliers.

With the first 2 opportunities, we can get TRT accuracy with NVFP4 KV cache on par with SGL simulated NVFP4 KV cache. With the 3rd opportunity, we can exceed SGLang simulated NVFP4 KV cache accuracy with TRT.

![TRT_FP4KV.png](Investigating%20TRT-LLM's%20NVFP4%20KV%20Cache%20Accuracy%20Lo/TRT_FP4KV.png)