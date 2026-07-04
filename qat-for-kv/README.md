# qat-for-kv — 索引

研究方向：**KV cache 的 NVFP4 量化 + QAT**（目标配置 w4·a4·kv4）。本目录按 001/002/… 编号记录任务、调研与计划；执行产出的正式实验记录仍写到上级 `evaluation-log/` 的编号序列里。

| # | 文件 | 内容 | 状态 |
| --- | --- | --- | --- |
| 001 | [001-instruction.md](001-instruction.md) | **NVFP4 模型 KV 格式验证 + 知识库**：三个 NVFP4 checkpoint（llmat 4B / nvidia 30B / DeepSeek-V4-Flash）的 QKV 权重与 KV cache 格式逐一验证（结论：没有一个 KV 是 NVFP4）；FP8-KV × NVFP4 权重的计算逻辑图；SageAttention3 精读（attention 的 FP4 计算）；Tony 的 TRT-LLM NVFP4-KV 调查笔记摘要；w4a4+NVFP4-KV 方向可行性评估（§6） | ✅ 验证完成，知识库持续更新 |
| 002 | [002-eval-instruction.md](002-eval-instruction.md) | **任务：RULER 128K 对比评测**——三对 BF16 vs NVFP4 模型（Qwen3-4B / Qwen3-30B-A3B / DeepSeek-V4-Flash）用 turboeval 测 RULER @128K；需先确认 DeepSeek-V4-Flash 有无 thinking 模式之分 | ⏳ 待执行 |
| 003 | [003-oscar-paper-reading.md](003-oscar-paper-reading.md) | **OSCAR 精读**（arXiv 2605.17757，Together AI）：INT2 KV cache 量化——attention-aware 协方差旋转 + sink/recent 混合精度布局 + 自定义 kernel；含离线/在线完整流程图与 Q/K/V 精度清单 | ✅ 完成 |
| 004 | [004-fp4kv-fakequant-eval-plan.md](004-fp4kv-fakequant-eval-plan.md) | **Plan：fake-quant 模拟 NVFP4-KV 损失实验（Qwen 系）**——三臂 A/D/E（A=原版原样、E=NVFP4 checkpoint 原样、D=E+假量化 KV），Qwen3-4B/8B/30B-A3B，GPQA + RULER 8K/32K，含 Tony 交叉验证与 QAT 决策门 | 📋 计划就绪，待开工 |
| 005 | [005-deepseek-v4-flash-fp4kv-plan.md](005-deepseek-v4-flash-fp4kv-plan.md) | **Plan：DeepSeek-V4-Flash 的 NVFP4-KV 实验（从 004 拆出）**——A′/D′/E′ 三臂（FP8 原生基线），MLA latent KV + 稀疏 indexer 的专属适配项；含 thinking 三档模式查证（002 遗留问题已解决） | 📋 计划，依赖 004 基建 |

## 附属资产

- [`tony-fp4kv-extracted/`](tony-fp4kv-extracted/) — Tony 的 Notion 调查笔记《Investigating TRT-LLM's NVFP4 KV Cache Accuracy Loss》原文 + 6 张图（TRT 数据流图、跨引擎激活对比）；来源 `tony-fp4kv.zip`
- 相关模型均已下载至 `../../model-and-data/`：`Qwen3-4B-NVFP4-llmat`、`Qwen3-30B-A3B-NVFP4`、`DeepSeek-V4-Flash-NVFP4` 等

## 上级 evaluation-log 中的相关记录

- [007](../007-k3-kl-divergence-bf16-vs-nvfp4-vs-qad.md) K3 KL 方法学与 harness（fake-quant 实验的预警指标）
- [008](../008-qad-s500-vllm-repro-sglang-serving-bug-confirmed.md) SGLang serving bug 教训（验证纪律的由来）
- [011](../011-qad-w4a4-w4a16-step-sweep-k3-curves.md) QAD step sweep（"前 50 步定乾坤"——KV-QAT 成本预期的依据）
