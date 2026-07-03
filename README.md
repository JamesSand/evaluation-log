# 评估日志

每次实验运行对应一个编号的 md 文件。全部在 4x B200 机器上完成；001–004 用 vLLM + cloudflared 提供服务，005 及之后用 SGLang + turbogate。

| # | 文件 | 内容 |
| --- | --- | --- |
| 001 | [001-qwen3-8b-bf16-vs-nvfp4-gpqa-ruler.md](001-qwen3-8b-bf16-vs-nvfp4-gpqa-ruler.md) | 基线对照：Qwen3-8B BF16 vs nvidia NVFP4，GPQA + RULER@8k（vLLM）。bf16 GPQA 0.581，朴素 NVFP4 0.505。 |
| 002 | [002-qwen3-8b-qad-students-gpqa-ruler.md](002-qwen3-8b-qad-students-gpqa-ruler.md) | 第一批 QAD 量化 student 模型（w4a4-kvfp8、w4a16 变体），GPQA + RULER@8k。 |
| 003 | [003-qwen3-8b-qad-step500-variants-gpqa-ruler.md](003-qwen3-8b-qad-step500-variants-gpqa-ruler.md) | QAD step500 checkpoint（w4a16-kv16、w4a16-kv8-cast），GPQA + RULER@8k。 |
| 004 | [004-qwen3-8b-qad-w4a4-kv8-step500-gpqa-ruler.md](004-qwen3-8b-qad-w4a4-kv8-step500-gpqa-ruler.md) | QAD w4a4-kv8 step500 —— 最强的量化模型（GPQA 0.581，与 bf16 持平）；含 RULER `niah_multikey_2` 反转现象的分析。 |
| 005 | [005-qwen3-30b-a3b-bf16-vs-nvfp4-gpqa-sglang-turboeval.md](005-qwen3-30b-a3b-bf16-vs-nvfp4-gpqa-sglang-turboeval.md) | Qwen3-30B-A3B BF16 vs nvidia NVFP4，经 SGLang + turbogate + turboeval 跑 GPQA，3 次运行均值：0.6195 vs 0.5825（−3.7 分 —— MoE 对 NVFP4 较为鲁棒）。 |
| 006 | [006-qwen3-8b-bf16-nvfp4-qad-gpqa-sglang-plus-qad-diagnosis.md](006-qwen3-8b-bf16-nvfp4-qad-gpqa-sglang-plus-qad-diagnosis.md) | Qwen3-8B BF16（0.579）vs NVFP4（0.493，−8.6 分）vs QAD s500，经 SGLang 跑 GPQA 3 次。**QAD 各行已被 008 更正**：~0.15 的分数/退化是 SGLang 的 serving bug，不是 checkpoint 本身的问题。BF16/NVFP4 的数字仍然有效。 |
| 007 | [007-k3-kl-divergence-bf16-vs-nvfp4-vs-qad.md](007-k3-kl-divergence-bf16-vs-nvfp4-vs-qad.md) | 跨模型 K3 KL（xorl-gate 公式，vLLM 栈，双向 + sanity 校验）：NVFP4 ≈ 0.045–0.049，**QAD ≈ 0.031–0.033（对称，低于 NVFP4）**——K3 排序正确预测 GPQA 排序。早期被 SGLang serving bug 污染的测量已废弃（见 008）。harness 和原始 JSON 附在旁边。 |
| 008 | [008-qad-s500-vllm-repro-sglang-serving-bug-confirmed.md](008-qad-s500-vllm-repro-sglang-serving-bug-confirmed.md) | 用 vLLM 0.24.0 复现 QAD s500 的 GPQA 运行：输出干净收尾，~0.52+ —— 确认 006/007 里 QAD 的"退化"是 SGLang 0.5.9 的 serving 缺陷；另记录了昨天 0.5808 的任务（按完整分母计算，未做 scored-only 过滤）以及 vLLM 的 `KVCacheScaleParameter` 补丁。 |
| 009 | [009-veomni-qad-step400-w4-vs-w4a4-gpqa-vllm.md](009-veomni-qad-step400-w4-vs-w4a4-gpqa-vllm.md) | VeOmni QAD step400 一对（w4=事后校准 act scale vs w4a4=训练学到的 act amax，推理均为 NVFP4 w4a4），vLLM 跑 GPQA 各 3 次：**w4 0.5657 vs w4a4 0.5236**（参照 BF16 0.5791、PTQ 0.4933）。两者都无截断/格式问题；同时在这批 export 上再次复现了 SGLang 的 w4a4 serving bug（冒烟 0/5、live acc 低于随机，6 个 sglang job 已取消并记录）。 |
| 010 | [010-qad-w4a4-step-sweep-k3-curve.md](010-qad-w4a4-step-sweep-k3-curve.md) | QAD w4a4-kv8 **step0–500 sweep**（11 个 checkpoint，vLLM）：step0（蒸馏前 PTQ student）实测 0.045/0.047 恰在 NVFP4 PTQ 带内；~90% 收敛发生在前 50 步（→≈0.032），之后 450 步只有边际改善；双向全程对称。曲线图 `k3-vs-step-qad-w4a4.png` + 原始数据附于本目录。 |
| 011 | [011-qad-w4a16-step-sweep-k3-curve.md](011-qad-w4a16-step-sweep-k3-curve.md) | QAD **w4a16-kv16** step0–500 sweep（11 个 checkpoint，vLLM）：PTQ 起点 0.022（w4a4 的一半），同样"前 50 步定乾坤"，平台 ≈0.015（w4a4 的一半）；step500 距 0.01 gate 仅 45%。含两家族对比图 `k3-vs-step-qad-families.png`。**注意口径**：w4a16 走 Marlin weight-only（激活 BF16，export 无 act scale），与 w4a4 的真 FP4 推理是不同推理模式，对比含此混合因素（详见 011 口径说明）。 |
| 012 | [012-qwen3-30b-a3b-bf16-vs-nvfp4-mmlu-pro-local.md](012-qwen3-30b-a3b-bf16-vs-nvfp4-mmlu-pro-local.md) | Qwen3-30B-A3B BF16 vs NVFP4，**MMLU-Pro** full 12032（本地 evalscope，thinking；turboeval 无此 benchmark）：**0.7794 vs 0.7616（−1.8 分）**，损失在 14 学科间均匀。 |
| 013 | [013-qwen3-30b-a3b-bf16-vs-nvfp4-livecodebench-local.md](013-qwen3-30b-a3b-bf16-vs-nvfp4-livecodebench-local.md) | Qwen3-30B-A3B BF16 vs NVFP4，**LiveCodeBench** release_v5 279 题（本地 tore-eval，thinking；turboeval 无此 benchmark）：Pass@1 **0.6344 vs 0.6093（−2.5 分）**，损失集中在 Medium、Hard 持平。含 30B 三 benchmark 量化损失汇总（GPQA −3.7 / MMLU-Pro −1.8 / LCB −2.5）。 |

辅助文件：

- [`k3_cross_model.py`](k3_cross_model.py) / [`k3_cross_model_results.json`](k3_cross_model_results.json) —— 007 的 harness 及按 split/按 prompt 的原始统计。
- [`samples/`](samples/) —— BF16 / NVFP4 / QAD 在同样 5 道题上的完整 GPQA 回复（thinking + 答案），并排整理在 `gpqa-samples-8b-all-models.md` 中。
