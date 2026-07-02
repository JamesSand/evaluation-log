# 003 — Qwen3-8B modelopt-QAD step500 checkpoint：GPQA + RULER@8k

- **日期：** 2026-07-01
- **机器：** zhizhousha 4x NVIDIA B200 (sm100)
- **Serving 栈：** `/home/zhizhousha/vllm-venv` — vLLM 0.24.0, torch 2.11.0+cu130, flashinfer
- **评测服务：** 托管的 TURBO-EVAL（`eval.together-turbo.com`），通过 `run_eval.py` 提交，经 cloudflared 隧道访问
- **模型（HF `togethercomputer/`，仅评测 `step500` checkpoint）：**
  - `Qwen3-8B-qad-w4a16-kv16-exported/step500` — quant_algo W4A16_NVFP4，KV bf16
  - `Qwen3-8B-qad-w4a16-kv8-cast-exported/step500` — quant_algo W4A16_NVFP4，KV FP8（配置为 `kv_cache_quant_algo=FP8`）
- **实验设计：** 2 个模型 x {GPQA, RULER@8k} = 4 组实验，每张 GPU 各起一个 server（共 4 张 GPU）。

## 配置

Serving（TP=1，`--max-model-len 40960`，`--gpu-memory-utilization 0.85`，`--trust-remote-code`，不加 `--enforce-eager`，量化自动识别为 `modelopt_fp4`）：
- Thinking 模式（GPQA）：`--override-generation-config {"temperature":0.6,"top_p":0.95,"top_k":20,"min_p":0}`，使用默认模板。
- 非 thinking 模式（RULER）：`--default-chat-template-kwargs '{"enable_thinking": false}'`，并 override 为 `{0.7,0.8,20,0}`。
- KV：kv8-cast 使用 `--kv-cache-dtype fp8`；kv16 使用默认 bf16。
- 每个 server 分配了各自不同的 `VLLM_PORT`（见运维备注），以规避内部端口冲突。

提交参数：GPQA 为 `gpqa_diamond`，198 条样例，并发 32，max-tokens 32768，T0.6/top_p0.95。
RULER 为 13 个任务，每任务 50 条，并发 16，max-tokens 2048，`--max-length 8192 --tokenizer Qwen/Qwen3-8B`，T0.7/top_p0.8。

## 结果

### GPQA-Diamond（thinking 模式，198/198，0 条报错）

| 模型 | GPQA |
| --- | --- |
| Qwen3-8B kv16-step500      | 0.5354 |
| Qwen3-8B kv8cast-step500   | 0.5202 |

### RULER @ 8k（非 thinking 模式，每任务 50 条，0 条报错）— acc = `accuracy/8192`

| 任务 | kv16-step500 | kv8cast-step500 |
| --- | --- | --- |
| niah_single_1   | 1.000 | 1.000 |
| niah_single_2   | 1.000 | 1.000 |
| niah_single_3   | 1.000 | 1.000 |
| niah_multikey_1 | 1.000 | 1.000 |
| niah_multikey_2 | 0.240 | 0.800 |
| niah_multikey_3 | 1.000 | 1.000 |
| niah_multivalue | 1.000 | 1.000 |
| niah_multiquery | 1.000 | 1.000 |
| ruler_cwe       | 0.808 | 0.790 |
| ruler_fwe       | 0.793 | 0.793 |
| ruler_vt        | 0.000 | 0.000 |
| ruler_qa_hotpot | 0.560 | 0.580 |
| ruler_qa_squad  | 0.648 | 0.615 |
| **13 任务均值** | **0.773** | **0.814** |

## 跨轮次汇总（迄今全部模型）

| 轮次 | 模型 | GPQA | RULER@8k（13 任务均值） |
| --- | --- | --- | --- |
| 001 | Qwen3-8B bf16                    | 0.5808 | 0.795 |
| 001 | Qwen3-8B NVFP4 (w4a4-kv16)       | 0.5051 | 0.775 |
| 002 | w4a4-kvfp8（QAD student）        | 0.5455 | 0.768 |
| 002 | w4a16-kv16（QAD student）        | 0.5404 | 0.761 |
| 002 | w4a16-kvfp8-cast（QAD student）  | 0.5505 | 0.777 |
| 003 | w4a16-kv16 step500               | 0.5354 | 0.773 |
| 003 | w4a16-kv8-cast step500           | 0.5202 | 0.814 |

## 发现 / 注意事项

- **kv8cast-step500 的 RULER 是目前所有模型中最高的（0.814）** — 主要由 `niah_multikey_2` 拉动：0.80，对比 kv16-step500 的 0.24（该任务是主要区分项；其余任务大多已在 1.0 饱和）。但它的 GPQA（0.520）是各 QAD 变体中最低的，相当于用推理能力换了检索能力。
- **kv16-step500**：GPQA 0.535，RULER 0.773 — 处于中游水平。
- **两个模型的 `ruler_vt` 均为 0.000**（与 001/002 中所有模型一致）— 这是非 thinking 模式下系统性的任务/格式 artifact，并非量化所致。该任务仍计入 13 任务均值。
- 全部 28 个 job 都跑满了预期样本数，0 条 errored trial（已对照评测服务逐一核实）。

## 运维备注

- **vLLM 内部端口冲突：** 同时启动 4 个 server 时，其中一个（端口 8002）在启动阶段崩溃，报 `torch.distributed ... EADDRINUSE port 46841`（仅 API 的 `--port` 互不相同并不够）。改用不同的 `VLLM_PORT` 重启后解决。今后批量起 server 时，务必给每个 server 设置唯一的 `VLLM_PORT`。
- **评测服务提交限速为每分钟 20 个 job：** 一次性提交全部 28 个 job 时，有 7 个 kv8cast 的 RULER 任务被 HTTP 429 拒绝（`submit rate limit exceeded for minute window`）。改成小批量重新提交后全部完成。今后提交请分批控制在每分钟 20 个以内。

## Job ids

原始 run_eval 日志与 job id 见：`~/qwen3-eval-run/eval_logs/<tag>__<benchmark>.log`（tag 为 `kv16s500`、`kv8casts500`）。
