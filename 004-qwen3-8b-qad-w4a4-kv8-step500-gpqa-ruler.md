# 004 — Qwen3-8B modelopt-QAD w4a4-kv8 step500：GPQA + RULER@8k

- **日期：** 2026-07-01
- **机器：** zhizhousha 4x NVIDIA B200 (sm100)
- **Serving 栈：** `/home/zhizhousha/vllm-venv` — vLLM 0.24.0、torch 2.11.0+cu130、flashinfer
- **评测服务：** 托管版 TURBO-EVAL（`eval.together-turbo.com`），通过 `run_eval.py` 提交，走 cloudflared 隧道
- **模型（HF `togethercomputer/`，`step500` checkpoint）：**
  - `Qwen3-8B-qad-w4a4-kv8-exported/step500` — quant_algo **NVFP4 (w4a4)**，KV FP8（`kv_cache_quant_algo=FP8`）
- **实验设计：** 1 个模型 x {GPQA, RULER@8k} = 2 组实验（14 个 job），2 个 server（thinking 模式 + 非 thinking 模式）分别占 2 张 GPU。
- **动机：** 这是唯一一个 w4a4 的 QAD 学生模型（权重和激活都量化到 4-bit）；002/003 里的 QAD 模型全是 w4a16。本次实验用来衡量激活也量化到 4-bit 时会损失多少。

## 配置

Serving（TP=1、`--max-model-len 40960`、`--gpu-memory-utilization 0.85`、`--trust-remote-code`、不加 `--enforce-eager`、量化自动识别为 `modelopt_fp4`、`--kv-cache-dtype fp8`、每个 server 用不同的 `VLLM_PORT`）：
- Thinking 模式（GPQA）：`--override-generation-config {"temperature":0.6,"top_p":0.95,"top_k":20,"min_p":0}`。
- 非 thinking 模式（RULER）：`--default-chat-template-kwargs '{"enable_thinking": false}'` + override `{0.7,0.8,20,0}`。

提交参数：GPQA `gpqa_diamond` 198 题，并发 32，max-tokens 32768，T0.6/top_p0.95。
RULER 13 个子任务，每个 50 条样本，并发 16，max-tokens 2048，`--max-length 8192 --tokenizer Qwen/Qwen3-8B`，T0.7/top_p0.8。

## 结果（14 个 job 全部完成，0 个报错；已与评测服务核对）

### GPQA-Diamond（thinking 模式，198/198）

| 模型 | GPQA |
| --- | --- |
| Qwen3-8B w4a4-kv8 step500 | **0.5808** |

### RULER @ 8k（非 thinking 模式，每任务 50 条）— acc = `accuracy/8192`

| 任务 | acc |
| --- | --- |
| niah_single_1   | 1.000 |
| niah_single_2   | 1.000 |
| niah_single_3   | 1.000 |
| niah_multikey_1 | 1.000 |
| niah_multikey_2 | 0.980 |
| niah_multikey_3 | 1.000 |
| niah_multivalue | 1.000 |
| niah_multiquery | 1.000 |
| ruler_cwe       | 0.802 |
| ruler_fwe       | 0.880 |
| ruler_vt        | 0.000 |
| ruler_qa_hotpot | 0.580 |
| ruler_qa_squad  | 0.642 |
| **均值（13 个任务）** | **0.837** |

## 跨轮次汇总（截至目前所有模型）

| 轮次 | 模型 | GPQA | RULER@8k（13 任务均值） |
| --- | --- | --- | --- |
| 001 | Qwen3-8B bf16                    | 0.5808 | 0.795 |
| 001 | Qwen3-8B NVFP4 (w4a4-kv16)       | 0.5051 | 0.775 |
| 002 | w4a4-kvfp8（QAD 学生模型）         | 0.5455 | 0.768 |
| 002 | w4a16-kv16（QAD 学生模型）         | 0.5404 | 0.761 |
| 002 | w4a16-kvfp8-cast（QAD 学生模型）   | 0.5505 | 0.777 |
| 004 | **w4a4-kv8 step500**             | **0.5808** | **0.837** |
| 003 | w4a16-kv16 step500               | 0.5354 | 0.773 |
| 003 | w4a16-kv8-cast step500           | 0.5202 | 0.814 |

### 回复长度 — GPQA（thinking 模式），输出 token 数

按模型统计，来自单个 198 题的 GPQA job（数据取自 results-api 的 `gateway/output-length`）。

| 轮次 | 模型 | avg | p50 | p99 |
| --- | --- | --- | --- | --- |
| 001 | bf16                    | 5965 | 5515 | 16271 |
| 001 | NVFP4 (w4a4-kv16)       | 7436 | 6648 | 32768 |
| 002 | w4a4-kvfp8              | 7224 | 6687 | 21543 |
| 002 | w4a16-kv16              | 7318 | 6963 | 19492 |
| 002 | w4a16-kvfp8-cast        | 7762 | 6650 | 27546 |
| 004 | **w4a4-kv8-step500**    | 6277 | 5780 | 18305 |
| 003 | kv16-step500            | 6480 | 5311 | 30003 |
| 003 | kv8-cast-step500        | 6779 | 6259 | 19609 |

### 回复长度 — RULER（非 thinking 模式），输出 token 数

按模型汇总 13 个 RULER 子任务下全部约 650 条请求（原始 `output_tokens`，取自 results-api 的 `gateway/requests`）。

| 轮次 | 模型 | avg | p50 | p99 |
| --- | --- | --- | --- | --- |
| 001 | bf16                    | 39.0 | 28 | 120 |
| 001 | NVFP4 (w4a4-kv16)       | 41.9 | 28 | 128 |
| 002 | w4a4-kvfp8              | 40.8 | 28 | 128 |
| 002 | w4a16-kv16              | 39.1 | 14 | 128 |
| 002 | w4a16-kvfp8-cast        | 40.2 | 27 | 128 |
| 004 | **w4a4-kv8-step500**    | 45.7 | 29 | 122 |
| 003 | kv16-step500            | 46.4 | 29 | 128 |
| 003 | kv8-cast-step500        | 47.7 | 30 | 127 |

说明：NVFP4 的 GPQA p99 打到了 32768 的 max-tokens 上限（部分 thinking 轨迹被截断）；RULER 的 p99 在多数模型上停在由 `max-length` 决定的 128 token 答案上限。step500 的 checkpoint（003/004）RULER 答案略长（avg 约 46 vs 约 40）——这与下文的反常结果分析相关。

## 结论 / 注意事项

- **这个 w4a4-kv8 step500 是两项指标上最强的量化模型。** 它的 GPQA（0.5808）*追平 bf16*，超过所有其他量化变体；RULER 13 任务均值（0.837）是目前所有模型中最高的。考虑到它是量化最激进的配置（激活也是 4-bit），这一点尤其值得注意。在这两个 benchmark 上，该 checkpoint 的 QAD 训练把 w4a4 的 accuracy 差距完全补了回来——对比 001 中未经 QAD 的朴素 w4a4 NVFP4（GPQA 0.505 / RULER 0.775）。
- **`niah_multikey_2` = 0.980**，而其他所有模型在 0.18–0.80 之间——RULER 均值高完全归因于这一个子任务（见下文反常结果分析）。这是行为/答案完整性层面的效应，并不是 w4a4 量化提升了长上下文检索能力的证据。
- **`ruler_vt` = 0.000** 再次出现（所有模型、所有轮次都是）——这是非 thinking 模式下系统性的任务/格式 artifact，与量化无关。仍计入 13 任务均值。
- 14 个 job 全部跑满预定题量，0 条报错 trial。

## 为什么 w4a4-kv8 的 RULER（0.837）能超过 bf16（0.795）？

这个结果看起来很荒谬——量化最激进的模型在长上下文 RULER 上反超了全精度。但它**不是**"量化让模型变好了"。三点原因，按重要性排序：

**1. 差距全部来自一个子任务（`niah_multikey_2`）；其余任务要么打平要么 bf16 占优。**
RULER 分任务对比，bf16 vs w4a4-kv8-step500：

| 任务 | bf16 | w4a4-kv8 | Δ |
| --- | --- | --- | --- |
| single_1/2/3, multikey_1, multivalue, multiquery | 1.000 | 1.000 | 0 |
| **niah_multikey_2** | **0.360** | **0.980** | **+0.620** |
| niah_multikey_3 | 0.980 | 1.000 | +0.020 |
| ruler_cwe | 0.878 | 0.802 | −0.076 |
| ruler_fwe | 0.867 | 0.880 | +0.013 |
| ruler_vt | 0.000 | 0.000 | 0 |
| ruler_qa_hotpot | 0.640 | 0.580 | −0.060 |
| ruler_qa_squad | 0.608 | 0.642 | +0.034 |
| **均值（13）** | **0.795** | **0.837** | **+0.042** |

仅 `niah_multikey_2` 一项就为均值贡献了 +0.620/13 = **+0.048**；其余 12 个任务合计 **−0.005**。把 multikey_2 去掉，赢的就是 bf16。在真正难的任务上（cwe、qa_hotpot），bf16 仍然领先。

**2. 这是两个不同的模型，不是"同一个模型量化前后"的对比。**
001 的 bf16 是 Qwen3-8B 基座模型。w4a4-kv8-step500 是 QAD（量化感知蒸馏）的*学生模型*，还额外训练了 500 步。任何能力/行为差异都应归因于这段额外训练，而不是量化本身。（未经 QAD 的朴素 w4a4——即 001 的 NVFP4——RULER 只有 0.775，*低于* bf16，符合预期。）

**3. multikey_2 上的机制是答案完整性，回复长度可以佐证。**
`niah_multikey_2` 要求返回**多个**检索到的值；RULER 按字符串匹配打分。该 job 上的原始 `output_tokens`：

| 模型 | multikey_2 平均输出 token | p50 | 得分 |
| --- | --- | --- | --- |
| bf16 | 10.9 | 11 | 0.360 |
| w4a4-kv8-step500 | 24.4 | 24 | 0.980 |

bf16 基座模型**生成不足**——它只输出约 11 个 token（大致相当于一个值）就停了，因此漏掉了大部分要求的 needle，只得 0.36。QAD 学生模型学会了把多值列表完整输出（约 24 个 token），得 0.98。这是蒸馏带来的输出完整性/格式修正，**不是**量化带来的长上下文能力提升。（这与 step500 checkpoint 整体上 RULER 答案略长一致——avg 约 46 vs 约 40 个 token。）

**结论：** "w4a4 在 RULER 上超过 bf16" 这个标题性结果在数字上是真的，但解读上有误导性。它来自单个子任务，由两个不同模型之间训练导致的格式/完整性差异驱动，而不是 4-bit 量化让模型变强。

## 运维备注

- **w4a4 的 warmup 非常长：** server 花了约 17 分钟才 ready。仅 `fp4_gemm` autotune 就约 11 分钟（21 个 profile），之后还有 inductor 编译 + FULL cudagraph 捕获。autotune 期间 GPU 利用率显示为 0%（profiling 是 kernel 间隙/CPU 密集的），而且基于 `\r` 的进度条在 `tail` 里看起来像卡死——把 `\r` 转成 `\n` 就能看到真实进度。这是 w4a4 的正常现象，不是 hang。
- 每个 server 用不同的 `VLLM_PORT`（51000/51001）——避免了 003 中遇到的 torch.distributed 内部端口冲突。
- 14 个 job 一批提交，未超过评测服务 20 job/分钟的限速。

## Job ids

原始 run_eval 日志 + job id：`~/qwen3-eval-run/eval_logs/w4a4kv8s500__<benchmark>.log`。
