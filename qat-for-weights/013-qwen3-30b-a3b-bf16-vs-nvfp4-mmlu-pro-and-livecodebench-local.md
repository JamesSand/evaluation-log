# 013 — Qwen3-30B-A3B BF16 vs NVFP4：MMLU-Pro + LiveCodeBench（本地评测，thinking）

- **日期：** 2026-07-03
- **背景：** turboeval 托管服务与 tore-eval-plus 后端都**未注册**这两个 benchmark——MMLU-Pro（服务目录里的 `mmmu`/`mmmu_pro` 是多模态 MMMU，非本 benchmark）与 LiveCodeBench（代码类只有 humaneval + agentic 的 swebench/terminal-bench）。故均在本地测：MMLU-Pro 用 evalscope 1.8.1（内置 `mmlu_pro`，纯规则打分），LiveCodeBench 用 tore-eval 的 `livecodebench` framework（内含 `lcb_runner`，自带代码执行判分），都直连本机 sglang endpoint。
- **机器 / 软件栈：** 4x B200（GPU0 按要求空闲），SGLang 0.5.9（`/home/c3-debug/venvs/sglang`）。BF16 在 GPU2:30000，NVFP4 在 GPU1:30001（`--context-length 40960`，`--mem-fraction-static 0.85`）。评测 harness：evalscope 1.8.1（`/home/c3-debug/venvs/evalscope`）、tore-eval 源码 `-e` 装（`/home/c3-debug/venvs/tore-eval`）。
- **采样（Qwen3 官方 thinking）：** `temperature=0.6, top_p=0.95, top_k=20, min_p=0`，非贪心。请求侧传 temp/top_p，`top_k/min_p` 在 sglang 服务端用 `--preferred-sampling-params` pin 死（`/get_server_info` 已验证）；`max_tokens=32768`，thinking 模式默认开启，冒烟确认 `<think>` 在 content 内、抽取正常。

---

## Part 1 — MMLU-Pro（evalscope，full 12032 题 / 14 学科）

### 结果（overall accuracy）

| 模型 | MMLU-Pro accuracy |
| --- | --- |
| **Qwen/Qwen3-30B-A3B (BF16)** | **0.7794** |
| **nvidia/Qwen3-30B-A3B-NVFP4 (w4a4, KV FP8)** | **0.7616** |
| **Δ NVFP4 − BF16** | **−0.0178（−1.8 分）** |

### 分学科（按量化损失从大到小）

| 学科 | n | BF16 | NVFP4 | Δ |
| --- | --- | --- | --- | --- |
| philosophy | 499 | 0.6874 | 0.6333 | −0.0541 |
| business | 789 | 0.8365 | 0.8137 | −0.0228 |
| physics | 1299 | 0.8676 | 0.8499 | −0.0177 |
| biology | 717 | 0.9024 | 0.8870 | −0.0154 |
| other | 924 | 0.7067 | 0.6916 | −0.0151 |
| economics | 844 | 0.8341 | 0.8199 | −0.0142 |
| history | 381 | 0.6352 | 0.6220 | −0.0132 |
| psychology | 798 | 0.7744 | 0.7732 | −0.0012 |
| health | 818 | 0.7274 | 0.7335 | +0.0061 |

（其余学科同量级，overall 已含全部 14 类。）

### Run 间方差（复跑 2 次，独立采样 temp 0.6）

| 模型 | run1 | run2 | 极差 |
| --- | --- | --- | --- |
| BF16 | 0.7794 | 0.7770 | 0.0024 |
| NVFP4 | 0.7616 | 0.7612 | 0.0004 |

方差**极小**（BF16 ±0.24 分、NVFP4 ±0.04 分）——12032 题大样本把采样噪声几乎平均掉，−1.8 分的量化损失远大于 run 间波动，是稳固信号。

### 发现

1. **NVFP4 在 MMLU-Pro 上仅掉 1.8 分**——与 GPQA-Diamond 上的 −3.7 分（005）同一梯度，再次印证 **30B-A3B MoE 对 NVFP4 量化相当鲁棒**（对比 8B dense 在 GPQA 掉 8.6 分）。
2. 损失在各学科间分布均匀、都很小；philosophy 掉得最多（−5.4 分），health 甚至略升（+0.6，噪声范围内）——没有某个学科被量化"打崩"，是全局的轻微钝化。

### 命令（可复现）

```bash
evalscope eval --model qwen3-30b-a3b --eval-type openai_api \
  --api-url http://127.0.0.1:30000/v1/chat/completions --api-key EMPTY \
  --datasets mmlu_pro --dataset-hub huggingface --eval-batch-size 64 \
  --generation-config '{"temperature":0.6,"top_p":0.95,"max_tokens":32768,"timeout":3600}'
```

NVFP4 同命令改 `--model qwen3-30b-a3b-nvfp4` + `--api-url .../30001/...`。报告落盘：`/home/c3-debug/eval-runs/mmlu-pro/full-{bf16,nvfp4}/<ts>/reports/`。

---

## Part 2 — LiveCodeBench（tore-eval，release_v5，279 题）

口径：`scenario=codegeneration`，`release_version=release_v5`，时间窗 **2024-08-01 → 2025-02-01**，`n=1`（Pass@1），`--openai_reason_mode deepseek`（thinking 模型的正确解析模式）。

### 结果（Pass@1）

| 模型 | Pass@1 | Easy | Medium | Hard |
| --- | --- | --- | --- | --- |
| **Qwen/Qwen3-30B-A3B (BF16)** | **0.6344** | 1.0000 | 0.7978 | 0.3171 |
| **nvidia/Qwen3-30B-A3B-NVFP4 (w4a4, KV FP8)** | **0.6093** | 0.9851 | 0.7303 | 0.3171 |
| **Δ NVFP4 − BF16** | **−0.0251（−2.5 分）** | −0.0149 | −0.0674 | 0.0000 |

### Run 间方差（复跑 2 次，独立采样 temp 0.6）

| 模型 | run1 Pass@1 | run2 Pass@1 | 极差 |
| --- | --- | --- | --- |
| BF16 | 0.6344 | 0.6201 | 0.0143 |
| NVFP4 | 0.6093 | 0.5950 | 0.0143 |

**方差明显大于 MMLU-Pro**（±1.4 分 vs ±0.2 分）——只有 279 题，单题权重 0.36 分，采样噪声更突出。**关键：两次运行中 BF16 都高于 NVFP4，且 Δ 稳定**（run1 −2.5 分 / run2 −2.5 分，两次极差同为 0.0143 且方向一致）。所以 −2.5 分的量化损失是真实的，但**单次 LCB 绝对分带 ±1.5 分噪声，比较量化损失时至少要 2 次、看 Δ 而非单点**。

### 发现

1. **NVFP4 在 LiveCodeBench 上掉 2.5 分**，量级介于 GPQA（−3.7）和 MMLU-Pro（−1.8）之间。
2. **损失几乎全部集中在 Medium 难度**（−6.7 分）；Easy 已近满分两者持平，**Hard 两者完全相同（0.3171）**——量化不改变模型"做不出的难题依旧做不出"，只在中等题上偶尔因数值扰动翻车。这与 007 的 K3 画像自洽：量化是重尾偶发翻转，不是能力整体下移。

### 命令（可复现）

```bash
python -m tore_eval.eval --framework livecodebench \
  --model_name_or_path qwen3-30b-a3b \
  --provider custom --base_url http://127.0.0.1:30000/v1 --api_key EMPTY \
  --num_workers 32 --scenario codegeneration --release_version release_v5 \
  --start_date 2024-08-01 --end_date 2025-02-01 \
  --max_tokens 32768 --temperature 0.6 --top_p 0.95 --n 1 \
  --openai_reason_mode deepseek
```

NVFP4 同命令改 `--model_name_or_path qwen3-30b-a3b-nvfp4` + `--base_url .../30001/v1`。报告落盘：`/home/c3-debug/eval-runs/lcb/full-{bf16,nvfp4}/`。

---

## 汇总：30B-A3B BF16 vs NVFP4 量化损失（跨 benchmark）

| benchmark | BF16 | NVFP4 | Δ |
| --- | --- | --- | --- |
| GPQA-Diamond（005，3-run 均值） | 0.6195 | 0.5825 | −3.7 分 |
| MMLU-Pro（本文 Part 1，full 12032） | 0.7794 | 0.7616 | −1.8 分 |
| LiveCodeBench（本文 Part 2，279） | 0.6344 | 0.6093 | −2.5 分 |


### 与 NVIDIA model card 报告数字的对照（"AA Ref" 是什么）

[nvidia/Qwen3-30B-A3B-NVFP4](https://huggingface.co/nvidia/Qwen3-30B-A3B-NVFP4) 的 model card 报告：

| Precision | MMLU Pro | GPQA Diamond | HLE | LiveCodeBench | SciCode | MATH-500 | AIME 2024 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| BF16 (AA Ref) | 0.78 | 0.62 | 0.07 | 0.51 | 0.28 | 0.96 | 0.75 |
| FP4 | 0.77 | 0.61 | 0.05 | **0.65** | 0.32 | 0.96 | **0.80** |

- **"AA Ref" = Artificial Analysis Reference**：BF16 基线行不是 NVIDIA 自己跑的，而是直接引用第三方评测机构
  [Artificial Analysis](https://artificialanalysis.ai/evaluations/artificial-analysis-intelligence-index) 排行榜上原版
  Qwen3-30B-A3B 的公开分数（那组 benchmark 列——含 HLE、SciCode——正是 AA Intelligence Index 的评测套件）。
  FP4 行才是 NVIDIA 自己测的（TensorRT-LLM + B200，见 card 的 Inference 一节）。
- **因此该表是跨 harness 对比，不能用来读量化损失**：FP4 比 BF16"高 14 分的 LiveCodeBench"（0.65 vs 0.51）和
  "高 5 分的 AIME"是 harness/题目版本/采样设置不同造成的伪影——量化不会让模型变强。LCB 对题目时间窗尤其敏感。
- **交叉验证我们自己的口径**：我们同 harness 测的 BF16（MMLU-Pro 0.7794、GPQA 0.6195）与 AA Ref（0.78、0.62）
  几乎完全一致，说明本文与 005 的 harness 和 AA 口径对齐；而本文的量化损失（同 harness 的 −1.8/−3.7/−2.5）
  才是干净的量化损失读数。

**结论：Qwen3-30B-A3B 的官方 NVFP4 量化在推理/知识/代码三个维度上损失稳定落在 2–4 分，MoE 架构对 4-bit 量化明显比同量级 dense 模型鲁棒。**

## 运维备注（踩坑）

- 两个 benchmark 均确认 turboeval 托管服务不支持（读 tore-eval-plus 后端源码核实，非猜测）。MMLU-Pro 是纯规则打分，理论上往 tore-eval-plus 的 `EVALSCOPE_TASKS` + tasks.json 各加一行即可上线（无沙箱需求）；LiveCodeBench 需要代码执行沙箱，是它没进托管服务的原因。
- tore-eval：`pip install git+...` 装出来是空壳（无 `tore_eval` 模块），必须 clone 后 `pip install -e .` 并补 `requirements.txt`；`--framework preset --preset_name livecodebench_think` 不接受 `--num_examples`，改用 `--framework livecodebench` 直接展开 preset 字段即可。
- 两模型 serving 全程正常（NVFP4 官方 checkpoint 跨引擎稳定，不涉及 006/008 的 sglang-QAD serving bug）。

> **注：** 本文合并自原 012（MMLU-Pro）与 013（LiveCodeBench）。两个 benchmark 各复跑 2 次的 run 间方差已补入各 Part（MMLU-Pro ±0.2 分、LCB ±1.4 分；两次的量化损失 Δ 方向与量级均一致）。
