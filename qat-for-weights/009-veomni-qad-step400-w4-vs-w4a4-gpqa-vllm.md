# 009 — VeOmni QAD step400 (w4 vs w4a4) NVFP4：GPQA-Diamond via vLLM + turbogate + turboeval

- **Date:** 2026-07-03（jobs 提交于 07-02 深夜）
- **Box / stack:** 4x B200；serving 用 **vLLM 0.24.0**（`/home/c3-debug/venvs/vllm`，torch 2.11.0+cu130，含 008 的 `KVCacheScaleParameter` 补丁——本次 checkpoint 无 KV scale，不触发），GPU2/3；eval 走 turbogate + 托管 turboeval，流程与 005–008 相同。
- **模型（`model-and-data/` 本地快照，各 6.0G，单 safetensors）：**
  - `Qwen3-8B-VeOmni-QAD-w4-step400-NVFP4`（togethercomputer 私有 repo）— VeOmni QAD **只训权重量化**；导出为完整 NVFP4 (w4a4) 格式，activation scale 由 128 条 calib 样本**事后校准**（manifest `reuse_trained_act_amax: false`）
  - `Qwen3-8B-VeOmni-QAD-w4a4-step400-NVFP4` — VeOmni QAD **权重+激活都在训练中量化**，导出**复用训练学到的 act amax**（`reuse_trained_act_amax: true`，252 个 buffer）
  - 两者 `hf_quant_config` 相同：`quant_algo NVFP4`，group 16，**KV 不量化**，MoE gate/lm_head 排除；safetensors 均含 252 组 packed-FP4 权重 + weight_scale + **input_scale**（w4a4 推理格式）。
- **对比问题:** 同样的 w4a4 推理下，"训练时见过激活量化（trained amax）" vs "训练时没见过、事后校准"差多少？

## Serving（008 配方 + 3 处 VeOmni 适配）

```bash
CUDA_VISIBLE_DEVICES={3|2} vllm serve model-and-data/Qwen3-8B-VeOmni-QAD-{w4|w4a4}-step400-NVFP4 \
  --tokenizer model-and-data/Qwen3-8B \          # repo 不带 tokenizer 文件，借 base
  --served-model-name veomni-qad-{w4|w4a4}-s400 \
  --host 0.0.0.0 --port {8002|8003} \
  --tensor-parallel-size 1 --max-model-len 40960 --gpu-memory-utilization 0.85 \
  --override-generation-config '{"temperature":0.6,"top_p":0.95,"top_k":20,"min_p":0}' \
  --trust-remote-code
# 注意：不加 --kv-cache-dtype fp8（这两个 checkpoint 无 KV 量化，照抄 008 反而错）
# override 是必须的：这两个 repo 的 generation_config.json 不含任何采样参数（全 None）
```

量化自动识别（vLLM modelopt NVFP4 路径）；warmup ~18 分钟（fp4_gemm autotune 多轮，与 004/008 一致）。

## 提交（与 005–008 完全同口径）

`gpqa_diamond`，`num_examples=198`，`concurrency=32`，`temperature=0.6`，`top_p=0.95`，`max_tokens=32768`，
`TURBOEVAL_TARGET_API_KEY=$TOGETHER_API_KEY`。turbogate: `zhizhou-veomni-{w4,w4a4}-vllm.gate.together-turbo.com`。
turboeval smoke-5：w4 0.6、w4a4 0.8（5/5，0 err）。本地 5 题冒烟：全部 `finish=stop`（2.9k–9.2k tok）、全部 `Answer:` 格式、w4 4/5 对、w4a4 3/5 对。

## Results（full gpqa_diamond 198，全部 completed，0 errored）

| model | run | accuracy | job_id |
| --- | --- | --- | --- |
| w4-QAD s400 | 1 | 0.5505 | `evl_7698695253e34db98b59b8d8563f9af8` |
| w4-QAD s400 | 2 | 0.5707 | `evl_ef816ac06645440a8f94d5502701fbc0` |
| w4-QAD s400 | 3 | 0.5758 | `evl_f283f6483dec40f798b0ae404c396a0a` |
| w4a4-QAD s400 | 1 | 0.5556 | `evl_4f147340d13d41e0a313e31008486388` |
| w4a4-QAD s400 | 2 | 0.5051 | `evl_88b5ab939709496e920910f084590d34` |
| w4a4-QAD s400 | 3 | 0.5101 | `evl_a81f9eac65a94bcc96afdf0d845fbd1f` |

Frontend: `https://eval.together-turbo.com/runs/<job_id>`。

### Summary（3 次均值）+ 参照

| model | GPQA mean (range) | Δ vs BF16 |
| --- | --- | --- |
| Qwen3-8B BF16（006） | 0.5791 (0.561–0.601) | — |
| **VeOmni QAD w4-s400（本次）** | **0.5657** (0.551–0.576) | **−1.3 pts** |
| **VeOmni QAD w4a4-s400（本次）** | **0.5236** (0.505–0.556) | **−5.6 pts** |
| ModelOpt QAD w4a4-kv8 s500（004/008，vLLM） | 0.5808 / 0.5152（两环境） | ~0 / −6.4 |
| nvidia NVFP4 PTQ（006） | 0.4933 (0.449–0.530) | −8.6 pts |

## Findings

1. **两个 VeOmni QAD step400 都显著优于裸 PTQ**（0.566/0.524 vs 0.493），QAD 训练有效。
2. **w4-QAD（calib act scales）反而比 w4a4-QAD（trained act amax）高 ~4 pts**——"激活量化参与训练"在这个 step400 checkpoint 上没有带来收益，反而略伤。样本只有 3 runs（GPQA 单次 ±3 pts），差距接近噪声边缘但方向一致（w4 三次全部 ≥0.55，w4a4 两次 ~0.51）。
3. **输出行为完全健康**（vLLM 下）：无截断、无退化、无 `\boxed`（本地冒烟 10/10 全部 `Answer:` 格式干净收尾）——step400 VeOmni export 没有 004 家族的格式/终止问题。
4. **SGLang 0.5.9 serving bug 在 VeOmni export 上再次复现**（追加 008 的证据链）：同两个模型、同题、同采样在 sglang（modelopt_fp4 + flashinfer_cutlass）下 5 题冒烟 0/5、大面积 runaway 到 32768 cap，turboeval 中途 live acc 仅 0.07–0.20（6 个 job 跑到 ~90/198 时被取消，job id 见 `serve-logs/veomni-full-jobs.txt`：`evl_cf707bfa…` 等 6 个，均 canceled）。**结论再次确认：sglang 的 w4a4 路径对 ModelOpt NVFP4 QAD export 不可用，量化模型评测一律用 vLLM。**

## Ops notes

- 两个 repo **不带 tokenizer**（也因此没有 004 系的 `extra_special_tokens` 坏字段问题），`--tokenizer` 指 base 即可。
- 第一次 warmup 完成后服务器被并行 session 的宽匹配 pkill 误杀，重启重跑一遍 autotune（fp4_gemm autotune 不吃 Triton 缓存）。清理进程时用 PID 或带独特关键字的匹配。
- 全程 GPU2/3（GPU0/1 留给并行 session）。跑完已拆除 server + tunnel，GPU2/3 归零。
