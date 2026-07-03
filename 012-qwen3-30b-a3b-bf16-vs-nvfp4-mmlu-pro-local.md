# 011 — Qwen3-30B-A3B BF16 vs NVFP4：MMLU-Pro（本地 evalscope，thinking）

- **日期：** 2026-07-03
- **背景：** turboeval 托管服务与 tore-eval-plus 后端都**未注册** MMLU-Pro（服务目录里的 `mmmu`/`mmmu_pro` 是多模态 MMMU，非本 benchmark）。故本地用 evalscope 1.8.1（其内置 `mmlu_pro`，纯规则打分）直连本机 sglang endpoint 测量。
- **机器 / 软件栈：** 4x B200（GPU0 按要求空闲），SGLang 0.5.9（`/home/c3-debug/venvs/sglang`）。BF16 在 GPU2:30000，NVFP4 在 GPU1:30001（`--served-model-name` 各自命名，`--context-length 40960`，`--mem-fraction-static 0.85`）。评测 harness：evalscope 1.8.1（`/home/c3-debug/venvs/evalscope`）。
- **采样（Qwen3 官方 thinking）：** `temperature=0.6, top_p=0.95, top_k=20, min_p=0`，非贪心。请求侧传 temp/top_p，`top_k/min_p` 在 sglang 服务端用 `--preferred-sampling-params` pin 死（`/get_server_info` 已验证）。`max_tokens=32768`，thinking 模式默认开启（chat template），冒烟确认 `<think>` 在 content 内、答案抽取正常。
- **数据：** MMLU-Pro 完整 **12032 题**（14 学科），evalscope 从 HuggingFace 加载；`--eval-batch-size 64`。

## 结果（overall accuracy，full 12032）

| 模型 | MMLU-Pro accuracy |
| --- | --- |
| **Qwen/Qwen3-30B-A3B (BF16)** | **0.7794** |
| **nvidia/Qwen3-30B-A3B-NVFP4 (w4a4, KV FP8)** | **0.7616** |
| **Δ NVFP4 − BF16** | **−0.0178（−1.8 分）** |

## 分学科（按量化损失从大到小）

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

## 发现

1. **NVFP4 在 MMLU-Pro 上仅掉 1.8 分**——与 GPQA-Diamond 上的 −3.7 分（005）同一梯度，再次印证 **30B-A3B MoE 对 NVFP4 量化相当鲁棒**（对比 8B dense 在 GPQA 掉 8.6 分）。
2. 损失在各学科间分布均匀、都很小；philosophy 掉得最多（−5.4 分），health 甚至略升（+0.6，噪声范围内）——没有某个学科被量化"打崩"，是全局的轻微钝化。
3. 两模型 serving 全程正常（NVFP4 官方 checkpoint 跨引擎行为稳定，不涉及 006/008 的 sglang-QAD serving bug）。

## 命令（可复现）

```bash
evalscope eval --model qwen3-30b-a3b --eval-type openai_api \
  --api-url http://127.0.0.1:30000/v1/chat/completions --api-key EMPTY \
  --datasets mmlu_pro --dataset-hub huggingface --eval-batch-size 64 \
  --generation-config '{"temperature":0.6,"top_p":0.95,"max_tokens":32768,"timeout":3600}'
```

NVFP4 同命令改 `--model qwen3-30b-a3b-nvfp4` + `--api-url .../30001/...`。

## 运维备注

- 报告落盘：`/home/c3-debug/eval-runs/mmlu-pro/full-{bf16,nvfp4}/<ts>/reports/`（含 HTML + 分学科 JSON）。
- MMLU-Pro 是纯规则打分（选项抽取），不需要代码执行沙箱——这也是它比 livecodebench 更容易加进托管服务的原因（evalscope 已内置，只差在 tore-eval-plus 注册一条）。
