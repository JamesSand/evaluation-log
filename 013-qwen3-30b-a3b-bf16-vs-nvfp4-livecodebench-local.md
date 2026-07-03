# 013 — Qwen3-30B-A3B BF16 vs NVFP4：LiveCodeBench（本地 tore-eval，thinking）

- **日期：** 2026-07-03
- **背景：** turboeval 托管服务与 tore-eval-plus 后端都**未注册** LiveCodeBench（其代码类任务只有 humaneval + agentic 的 swebench/terminal-bench）。故本地用 tore-eval 的 `livecodebench` framework（内含 `lcb_runner`，自带代码执行判分）直连本机 sglang endpoint 测量。
- **机器 / 软件栈：** 同 012——4x B200（GPU0 空闲），SGLang 0.5.9，BF16 GPU2:30000 / NVFP4 GPU1:30001。评测 harness：tore-eval（源码 `-e` 装于 `/home/c3-debug/venvs/tore-eval`）。
- **采样（Qwen3 官方 thinking）：** `temperature=0.6, top_p=0.95, top_k=20, min_p=0`（top_k/min_p 服务端 pin），`max_tokens=32768`，`--openai_reason_mode deepseek`（thinking 模型的正确解析模式）。
- **数据集口径：** `scenario=codegeneration`，`release_version=release_v5`，时间窗 **2024-08-01 → 2025-02-01**，共 **279 题**，`n=1`（Pass@1）。

## 结果（Pass@1，279 题）

| 模型 | Pass@1 | Easy | Medium | Hard |
| --- | --- | --- | --- | --- |
| **Qwen/Qwen3-30B-A3B (BF16)** | **0.6344** | 1.0000 | 0.7978 | 0.3171 |
| **nvidia/Qwen3-30B-A3B-NVFP4 (w4a4, KV FP8)** | **0.6093** | 0.9851 | 0.7303 | 0.3171 |
| **Δ NVFP4 − BF16** | **−0.0251（−2.5 分）** | −0.0149 | −0.0674 | 0.0000 |

## 发现

1. **NVFP4 在 LiveCodeBench 上掉 2.5 分**，量级介于 GPQA（−3.7，005）和 MMLU-Pro（−1.8，012）之间——三个 benchmark 一致指向"30B-A3B MoE 对 NVFP4 的损失在 2–4 分区间，属可接受"。
2. **损失几乎全部集中在 Medium 难度**（−6.7 分）；Easy 已近满分两者持平，**Hard 两者完全相同（0.3171）**——量化不改变模型"做不出的难题依旧做不出"，只在中等题上偶尔因数值扰动翻车。这与 007 的 K3 画像自洽：量化是重尾偶发翻转，不是能力整体下移。
3. 两模型 serving 全程正常，无截断/退化（NVFP4 官方 checkpoint 跨引擎稳定，不涉及 sglang-QAD serving bug）。

## 命令（可复现）

```bash
python -m tore_eval.eval --framework livecodebench \
  --model_name_or_path qwen3-30b-a3b \
  --provider custom --base_url http://127.0.0.1:30000/v1 --api_key EMPTY \
  --num_workers 32 --scenario codegeneration --release_version release_v5 \
  --start_date 2024-08-01 --end_date 2025-02-01 \
  --max_tokens 32768 --temperature 0.6 --top_p 0.95 --n 1 \
  --openai_reason_mode deepseek
```

NVFP4 同命令改 `--model_name_or_path qwen3-30b-a3b-nvfp4` + `--base_url .../30001/v1`。

## 运维备注（本次踩坑）

- `pip install git+...tore-eval` 装出来是空壳（无 `tore_eval` 模块）——必须 clone 后 `pip install -e .`（源码装），并补 `requirements.txt`。
- `--framework preset --preset_name livecodebench_think` 这条 preset 路径**不接受 `--num_examples`**（HfArgumentParser 报未知参数）；改用 `--framework livecodebench` 直接展开 preset 里的字段（scenario/release_version/dates/reason_mode）即可，功能等价。
- LCB 的判分要在本地**执行生成的代码跑测试用例**（这正是它没进托管服务的原因：服务端 image 需要代码执行沙箱）。本地跑无此限制。
- 报告落盘：`/home/c3-debug/eval-runs/lcb/full-{bf16,nvfp4}/`。

## 30B-A3B BF16 vs NVFP4 量化损失汇总（三 benchmark）

| benchmark | BF16 | NVFP4 | Δ |
| --- | --- | --- | --- |
| GPQA-Diamond（005，3-run 均值） | 0.6195 | 0.5825 | −3.7 分 |
| MMLU-Pro（012，full 12032） | 0.7794 | 0.7616 | −1.8 分 |
| LiveCodeBench（013，279） | 0.6344 | 0.6093 | −2.5 分 |

**结论：Qwen3-30B-A3B 的官方 NVFP4 量化在推理/知识/代码三个维度上损失稳定落在 2–4 分，MoE 架构对 4-bit 量化明显比同量级 dense 模型鲁棒。**
