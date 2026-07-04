# 002 — Qwen3-8B modelopt-QAD 量化 student 模型：GPQA + RULER@8k

- **日期:** 2026-07-01
- **机器:** zhizhousha 4x NVIDIA B200 (sm100)
- **Serving 栈:** `/home/zhizhousha/vllm-venv` — vLLM 0.24.0, torch 2.11.0+cu130, flashinfer, Python 3.12
- **评测服务:** 托管的 TURBO-EVAL（`eval.together-turbo.com`），通过 `run_eval.py` 提交，走 cloudflared 隧道
- **模型（来自 HF `togethercomputer/`，modelopt QAD 量化 student 模型）:**
  - `Qwen3-8B-w4a4-kvfp8-exported` — quant_algo NVFP4 (w4a4)，KV FP8
  - `Qwen3-8B-w4a16-kv16-exported` — quant_algo W4A16_NVFP4，KV bf16
  - `Qwen3-8B-w4a16-kvfp8-cast-exported` — quant_algo W4A16_NVFP4，KV FP8
- **实验设计:** 3 个模型 x {GPQA, RULER@8k} = 6 组实验（每组 = 1 个 GPQA job + 13 个 RULER job）。共 42 个 job，打散排布在 4 块 GPU 上。

## 配置

Serving 配置（TP=1，`--max-model-len 40960`，`--trust-remote-code`，不加 `--enforce-eager`，量化方式自动识别为 `modelopt_fp4`）：
- Thinking 模式服务（GPQA）：`--override-generation-config {"temperature":0.6,"top_p":0.95,"top_k":20,"min_p":0}`，使用默认模板（thinking 开启），不加 reasoning-parser。
- 非 thinking 服务（RULER）：`--default-chat-template-kwargs '{"enable_thinking": false}'` + `--override-generation-config {"temperature":0.7,"top_p":0.8,"top_k":20,"min_p":0}`。
- KV cache：两个 `kvfp8` 模型加 `--kv-cache-dtype fp8`；`kv16` 用默认值（bf16）。由于 GPU 是共享的，设置 `--gpu-memory-utilization 0.45`（cast 那一对为 0.40）。

提交参数：
- **GPQA:** `gpqa_diamond --num-examples 198 --concurrency 32 --max-tokens 32768 --temperature 0.6 --top-p 0.95`
- **RULER（13 个任务）:** `--num-examples 50 --concurrency 16 --max-tokens 2048 --max-length 8192 --tokenizer Qwen/Qwen3-8B --temperature 0.7 --top-p 0.8`

## 结果

### GPQA-Diamond（thinking 模式，198/198，0 条报错）

| 模型 | GPQA |
| --- | --- |
| Qwen3-8B w4a4-kvfp8       | 0.5455 |
| Qwen3-8B w4a16-kv16       | 0.5404 |
| Qwen3-8B w4a16-kvfp8-cast | 0.5505 |
| *(参考 001)* bf16          | 0.5808 |
| *(参考 001)* NVFP4 (w4a4-kv16) | 0.5051 |

### RULER @ 8k（非 thinking，每任务 50 条，0 条报错）— acc = `accuracy/8192`

| 任务 | w4a4-kvfp8 | w4a16-kv16 | w4a16-kvfp8-cast |
| --- | --- | --- | --- |
| niah_single_1   | 1.000 | 1.000 | 1.000 |
| niah_single_2   | 1.000 | 1.000 | 1.000 |
| niah_single_3   | 1.000 | 1.000 | 1.000 |
| niah_multikey_1 | 0.840 | 0.880 | 0.940 |
| niah_multikey_2 | 0.380 | 0.180 | 0.400 |
| niah_multikey_3 | 0.980 | 0.940 | 0.960 |
| niah_multivalue | 1.000 | 1.000 | 1.000 |
| niah_multiquery | 0.970 | 1.000 | 0.990 |
| ruler_cwe       | 0.776 | 0.814 | 0.760 |
| ruler_fwe       | 0.867 | 0.827 | 0.820 |
| ruler_vt        | 0.000 | 0.000 | 0.000 |
| ruler_qa_hotpot | 0.600 | 0.600 | 0.580 |
| ruler_qa_squad  | 0.568 | 0.648 | 0.655 |
| **均值（13 个任务）** | **0.768** | **0.761** | **0.777** |

RULER 13 任务均值参考（001）：bf16 **0.795**，NVFP4 **0.775**。

## 结论与注意事项

- **GPQA:** 三个 QAD student 都落在 0.540–0.551 区间，明显高于 001 中朴素 NVFP4 的水平（0.505），低于 bf16（0.581）。QAD（quantization-aware distillation，量化感知蒸馏）把朴素 NVFP4 损失掉的推理能力找回了相当一部分。GPQA 最佳：`w4a16-kvfp8-cast`（0.5505）。
- **RULER（13 任务均值）:** 三者很接近（0.761–0.777），都在 001 的 NVFP4 水平（0.775）附近，略低于 bf16（0.795）。`w4a16-kvfp8-cast` 在这里也是最高的（0.777）。
- **所有模型的 `ruler_vt` 都是 0.000**（本次和 001 均如此）。在 bf16/NVFP4/全部 QAD 变体上表现一致 ⇒ 这是非 thinking 模式下 Qwen3-8B 在 ruler_vt 上的系统性行为（答案格式 / 变量追踪问题），不是量化造成的。该任务仍计入 13 任务均值（报告的均值一律不剔除子任务）。
- **`niah_multikey_2`** 是另一个持续偏低的任务（各模型 0.18–0.40）——属于任务本身难度，不是量化带来的差异。
- 全部 42 个 job 都跑满了预期样本数，0 条报错（已与评测服务核对）。

## 运维备注

运行途中出现过一次瞬时 ENOSPC（6 个 vLLM 并发编译时 overlay /tmp + wekafs writeback 缓存飙升），本地轮询进程短暂卡住；评测服务侧的 job 均正常完成，最终数字直接从服务读回（`/v1/evals/list`）。PVC 实际从未写满（峰值约 127G，远低于配额）。

## Job id

原始 run_eval 日志和 job id 见：`~/qwen3-eval-run/eval_logs/<tag>__<benchmark>.log`
（tag：`w4a4kvfp8`、`w4a16kv16`、`w4a16cast`）。Serving/提交脚本及 server 日志在 `~/qwen3-eval-run/` 下。
