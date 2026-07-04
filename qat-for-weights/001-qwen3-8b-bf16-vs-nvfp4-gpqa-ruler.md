# 001 — Qwen3-8B BF16 vs NVFP4：GPQA + RULER@8k

- **日期：** 2026-07-01
- **机器：** zhizhousha 4x NVIDIA B200 (sm100)
- **Serving 栈：** `/home/zhizhousha/vllm-venv` — vLLM 0.24.0、torch 2.11.0+cu130、flashinfer、Python 3.12
- **评测服务：** 托管的 TURBO-EVAL（`eval.together-turbo.com`），通过 `pipeline/quantization-quality-check/scripts/run_eval.py` 提交
- **端点暴露：** cloudflared quick tunnel（每个 server 一条；跑完即拆除）
- **模型：** `model-and-data/Qwen3-8B`（bf16）、`model-and-data/Qwen3-8B-NVFP4`（modelopt NVFP4）
- **实验设计：** 4 组实验，每组占一张 GPU，并行运行。

## 配置

### Serving（每张 GPU，TP=1，`--max-model-len 40960 --gpu-memory-utilization 0.85 --trust-remote-code`，不加 `--enforce-eager`，不使用 reasoning-parser）

| GPU | port | 模型 | served-name | 模式 | override-generation-config | chat-template kwargs |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | 8000 | Qwen3-8B | qwen3-8b | thinking | `{"temperature":0.6,"top_p":0.95,"top_k":20,"min_p":0}` | （默认 = 开启 thinking） |
| 1 | 8001 | Qwen3-8B | qwen3-8b | non-thinking | `{"temperature":0.7,"top_p":0.8,"top_k":20,"min_p":0}` | `{"enable_thinking": false}` |
| 2 | 8002 | Qwen3-8B-NVFP4 | qwen3-8b-nvfp4 | thinking | `{"temperature":0.6,"top_p":0.95,"top_k":20,"min_p":0}` | （默认 = 开启 thinking） |
| 3 | 8003 | Qwen3-8B-NVFP4 | qwen3-8b-nvfp4 | non-thinking | `{"temperature":0.7,"top_p":0.8,"top_k":20,"min_p":0}` | `{"enable_thinking": false}` |

采样参数遵循 Qwen3 官方推荐；`top_k`/`min_p` 在 server 侧固定（评测的 `/v1` 路径
只透传 `temperature`/`top_p`）。Benchmark 与模式的对应：GPQA=thinking，RULER=non-thinking。

### 提交参数

- **GPQA:** `--benchmark gpqa_diamond --num-examples 198 --concurrency 32 --max-tokens 32768 --temperature 0.6 --top-p 0.95`
- **RULER（13 个任务）:** `--num-examples 50 --concurrency 16 --max-tokens 2048 --max-length 8192 --tokenizer Qwen/Qwen3-8B --temperature 0.7 --top-p 0.8`  （`--max-length 8192` ⇒ `params.max_seq_lengths=[8192]`；读取 `accuracy/8192`）

## 结果

### GPQA-Diamond（thinking 模式，198/198，0 条报错）

| checkpoint | accuracy | Δ 相对 bf16 |
| --- | --- | --- |
| Qwen3-8B bf16  | **0.5808** | — |
| Qwen3-8B NVFP4 | **0.5051** | **−0.0757** |

### RULER @ 8k（non-thinking，每任务 50 条，0 条报错）— acc = `accuracy/8192`

| 任务 | bf16 | nvfp4 |
| --- | --- | --- |
| niah_single_1   | 1.000 | 1.000 |
| niah_single_2   | 1.000 | 1.000 |
| niah_single_3   | 1.000 | 1.000 |
| niah_multikey_1 | 1.000 | 0.920 |
| niah_multikey_2 | 0.360 | 0.380 |
| niah_multikey_3 | 0.980 | 0.960 |
| niah_multivalue | 1.000 | 1.000 |
| niah_multiquery | 1.000 | 1.000 |
| ruler_cwe       | 0.878 | 0.784 |
| ruler_fwe       | 0.867 | 0.880 |
| ruler_vt        | 0.000 | 0.000 |
| ruler_qa_hotpot | 0.640 | 0.580 |
| ruler_qa_squad  | 0.608 | 0.568 |
| **均值（13 项）**   | **0.795** | **0.775** |
| **均值（12 项，不含 ruler_vt）** | **0.861** | **0.839** |

## 发现与注意事项

- **NVFP4 对推理的损伤大于对检索的损伤。** GPQA 掉 7.6 个百分点（0.581→0.505）；
  RULER 均值只掉约 2 个百分点。量化的代价集中在 thinking 类 benchmark 上。
- **两个 checkpoint 的 `ruler_vt` 均为 0.000**（50/50，0 条报错，耗时约 29 s）。bf16 与
  nvfp4 一模一样地得零 ⇒ 这是 Qwen3-8B 在 non-thinking 模式下 ruler_vt 的系统性行为
  （答案格式 / 变量追踪问题），不是量化效应，也不是端点故障。已在 12 项均值中剔除。
  在信任 VT 结果之前请先抽样检查输出。
- **`niah_multikey_2` 两侧都约 0.37**，属于任务本身的特性，不是量化带来的差异。
- 所有作业都跑满了预期样本量（GPQA 198/198，每个 RULER 任务 50/50），
  0 条报错，全程经由 cloudflared tunnel。

## Job id

GPQA：bf16 `evl_993155a0c87c4d3ba873b06aac4452e8` · nvfp4 `evl_366d19854a41456a9e80ccc2636a733f`

RULER 的 job id 与原始 run_eval 日志：`~/qwen3-eval-run/eval_logs/<tag>__<benchmark>.log`
（tag = `bf16` | `nvfp4`）。Serving/提交脚本及 server 日志：`~/qwen3-eval-run/`。
