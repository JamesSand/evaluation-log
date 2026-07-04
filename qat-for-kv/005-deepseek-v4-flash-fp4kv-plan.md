# Plan：DeepSeek-V4-Flash 的 NVFP4-KV fake-quant 实验（从 004 单独拆出）

- **日期：** 2026-07-04
- **为什么单独立项：** DeepSeek-V4-Flash 在架构和量化口径上每一层都与 Qwen 系不同（FP8 原生、NVFP4 版仅 experts 量化、MLA latent KV、稀疏 attention indexer、三档 thinking），混在 [004](004-fp4kv-fakequant-eval-plan.md) 里会污染主线结论。本 plan 沿用 004 的三臂哲学与评测协议，只重新定义臂的内容并列出专属工程项。
- **状态：** 📋 计划，等 004 的 Qwen 主线跑完、fake-quant 基建验证过之后再启动。

## 1. 三臂定义（与 004 同一哲学：A/E 是两个现状端点，D 是唯一实验操作）

| 臂 | checkpoint | linear 权重 | KV **数值精度** | KV **物理存储** | 含义 |
| --- | --- | --- | --- | --- | --- |
| A′（基线） | `deepseek-ai/DeepSeek-V4-Flash` **原样跑法** | FP8 原生（E4M3 + E8M0 块 scale，训练即 FP8） | BF16（无 KV 量化） | `torch.bfloat16`（latent） | 原版现状 = 上限 |
| **D′（实验）** | `nvidia/DeepSeek-V4-Flash-NVFP4` + **fake-NVFP4 KV** | experts NVFP4，attention FP8（该 checkpoint 原生配置） | **NVFP4**（fake-quant 两级 scale，同 004 §3.1） | `torch.bfloat16`（假量化后写入） | experts-FP4 + KV-FP4 组合 |
| E′（参照） | `nvidia/DeepSeek-V4-Flash-NVFP4` **原样跑法** | 同 D′ | BF16（该 checkpoint 未做 KV 量化，`kv_cache_quant_algo: null`） | `torch.bfloat16`（latent） | NVFP4 版现状 |

读数：**E′−A′** = experts-FP4 的现有代价；**D′−E′** = 纯 KV-FP4 边际代价（与 004 里 4B 的 D−E 同语义，可跨模型对照"latent KV vs 普通 KV 对 FP4 的敏感度"——这是本实验最有价值的一个对比）。

**口径警告：D′ 不是 w4a4 全链路**（attention 权重仍 FP8），结果表中不得与 Qwen 的 D 臂直接并列，必须分开呈现。

## 2. 架构特殊性（fake-quant 实现必须处理的）

1. **MLA latent KV**：cache 里不是 K/V 头，是 `kv_lora_rank=512` 的压缩 latent c_KV + RoPE key 分量。fake-quant 作用对象要决策：只量 latent、还是 latent + rope-k 分量（TRT 的 NVFP4-KV 对 MLA 怎么处理可参照 Tony 笔记的 TRT 数据流）。latent 信息密度高，预期比 GQA 的 K/V 敏感——这正是要测的。
2. **稀疏 attention indexer**（config 实证：`index_topk=512, index_n_heads=64, index_head_dim=128`）：attention 只对 indexer 选出的 top-512 token 计算，**cache 有两个消费者**（indexer 选谁 + attention 算什么）。KV 量化会同时扰动两者；且要查清 sglang 里 indexer 的 key 缓冲区是否独立、是否也应量化。
3. **sglang 挂载点不同**：MLA 走 `MLATokenToKVPool`（非 004 用的 MHA pool），fake-quant hook 需单独适配。
4. **chat template 坑**：原版 checkpoint 的 `tokenizer_config.json` 里 chat_template 为空；NVFP4 版带 `chat_template.jinja`。serving 原版时需显式指定 template（可复用 NVFP4 版的，需先 diff 确认一致性）。

## 3. thinking 模式（002 遗留问题，已查证）

README 实证：V4-Flash 支持**三档 reasoning effort**——

| 模式 | 输出格式 |
| --- | --- |
| Non-think | `</think>` + summary（**non-think 也输出 `</think>` 标记**，抽取逻辑要适配） |
| Think High | `<think>` thinking `</think>` summary |
| Think Max | 特殊 system prompt + `<think>`…`</think>` summary |

RULER 用 **Non-think**（对齐 Qwen 惯例），GPQA 用 Think High；采样按 002 给定：`temperature=1.0, top_p=1.0`（GLM/DeepSeek 口径，三臂一致即可比）。

## 4. 工程与资源

- 149G FP8 权重：单卡 180G 放得下但 32K/128K 上下文 KV 余量紧 → 预设 **TP2**（MLA latent 的 per-token cache 很小，128K 反而可能可行，起服务后按实测 KV 池大小定）。
- 先验证 sglang 0.5.9 对 `deepseek_v4` 架构（attn_sink / indexer / hyper-connection 字段）的支持；不行则试 0.5.14 venv（已装）或 vLLM 0.24.0。
- 评测协议、验证纪律、决策门全部沿用 004 §4–§7。

## 5. 前置依赖

1. 004 的 fake-quant 基建（MHA 版）已实现并通过 Tony 交叉验证；
2. sglang 能正常 serve 两个 DeepSeek checkpoint（不带 fake-quant 先冒烟）；
3. MLA pool 的 fake-quant 适配（§2.3）。
