# 014 — NVFP4 KV cache fake-quant 损失矩阵：A/D/E 三臂 × GPQA + RULER（004 plan 执行结果）

- **日期：** 2026-07-04
- **计划：** [qat-for-kv/004-fp4kv-fakequant-eval-plan.md](qat-for-kv/004-fp4kv-fakequant-eval-plan.md)。三臂定义：**A** = 原版 checkpoint 原样（Qwen 系 = BF16 全链路）；**E** = NVFP4 checkpoint 原样（`--kv-cache-dtype auto`）；**D** = E 的权重 + **KV fake-quant 到 NVFP4**（存储 bf16，误差与真 NVFP4 一致）。读数：D−A = 全链路损失，E−A = 生产现状损失，D−E = KV 压到 FP4 的边际代价。
- **机器 / 软件栈：** 4x B200，SGLang 0.5.9 **editable 源码安装**（`/home/c3-debug/workspace/low-precision-project/sglang`，改动可 `git diff` 审阅），turbogate + turboeval，协议同 005/006（GPQA thinking 3-run；RULER 非 thinking 13 任务 × 8K/32K，`temperature=0.7, top_p=0.8`，tokenizer Qwen3 系共用）。

## 1. fake-quant 实现与验证（D0）

- **实现**：`sglang/python/sglang/srt/mem_cache/kv_fakequant.py`（新文件）+ `memory_pool.py` 的 `MHATokenToKVPool.set_kv_buffer` 挂 hook（+7 行）。NVFP4 两级 scale 完全对齐 TRT-LLM 口径：E2M1 值 + per-16 块 E4M3 scale + FP32 per-tensor 全局 scale；纯 tensor 运算，CUDA-graph capture 安全；env 开关（`KV_FAKEQUANT` / `KV_FAKEQUANT_SCALE_FILE`），另有 `calib` 模式记录逐层 amax。
- **单元测试**：E2M1 网格往返、已知值舍入、块隔离（head_dim=128 → 8 块/头不跨头）、高斯相对误差 9.4%（标准 FP4 量级）。**发现并修复一个真 bug**：块 scale 超出 E4M3 范围时 torch 的 float8 cast 溢出为 NaN（非饱和），修复为 cast 前 clamp 448 模拟硬件饱和行为。
- **s_global 来源的重要发现**：nvidia 8B/30B checkpoint 的 `k_scale`/`v_scale` **全部恰好 = 1.0**（未真标定的占位值，FP8 浮点格式下够用）→ 推导 s_global = 1/6 均匀。实测 amax 对照（30K token 校准）：8B K amax 最大 248 vs 隐含 448，ratio 1.8×（<2× 阈值）→ 无截断风险，风险备忘关闭。4B（llmat，无任何 KV scale）自标定：36 层，K amax 最大 322，scale 文件含 1.5× 余量。
- **Hook 活性验证（K3）**：BF16 采样 → fake-quant 服务器打分（同 BF16 权重，只差 KV fake-quant），三个模型的**纯 KV-FP4 分布偏移**：8B K3 = **0.0133**（噪声底 0.00027 的 49×）、4B = **0.0185**、30B-A3B = **0.0229**（30B 噪声底 0.0013，MoE 路由数值噪声更大）。对照 8B 全 w4a4 的 K3=0.045，KV-FP4 单独贡献约 0.013–0.023 的分布偏移，随模型不同略有差异。
- **30B 专属大坑（fused KV 写入路径）**：`qwen3_moe.py` 走 `fused_set_kv_buffer`（RoPE+KV 写入融合 kernel），**绕过了 `MHATokenToKVPool.set_kv_buffer`**，导致 30B 的 fake-quant hook 完全不生效（贪心序列与 base 逐 token 一致、K3 恰好等于噪声底才暴露）；dense 的 `qwen3.py` 无此路径，8B/4B 不受影响。修复：`models/utils.py::enable_fused_set_kv_buffer` 在 fake-quant 激活时返回 False，强制走标准路径（仅 eval 场景，性能无所谓）。修复后验证：贪心序列 token 67 处分叉、first-call 日志出现、K3 = 0.0229 ≫ 噪声底。
- **Tony 交叉诊断**（BF16+fakeKV @32K）：niah_multikey_2 base 0.98 vs fake 0.98（Δ=0，与其 Exp3 的 −0.2pp 一致；未复现其 Exp1 的 −5pp，如实记录）。turboeval 的 ruler_vt 对本系模型系统性失效（004 已知 artifact），弃用该子任务作解读。

## 2. Qwen3-8B 三臂结果

### 2.1 GPQA-Diamond（thinking，198 题 × 3 runs，全部 0 错误）

| 臂 | run1 / run2 / run3 | 均值 |
| --- | --- | --- |
| A（BF16 全链路） | 0.6061 / 0.5909 / 0.5960 | **0.5977** |
| D（w4a4 + FP4-KV） | 0.4646 / 0.4545 / 0.5000 | **0.4731** |
| E（w4a4 + FP8-KV，生产现状） | 0.5000 / 0.5354 / 0.4949 | **0.5101** |

读数：**E−A = −8.8pp**（与 006 的 −8.6pp 交叉验证 ✓）；**D−E = −3.7pp**（KV 从 FP8 压到 FP4 的边际代价）；D−A = −12.5pp。

### 2.2 RULER（非 thinking，50 题/任务；vt 因 artifact 排除于均值解读）

| 均值（12 任务，去 vt） | A | D | E | **D−E** | E−A |
| --- | --- | --- | --- | --- | --- |
| @8K | 0.9125 | 0.8643 | 0.8709 | **−0.7pp** | −4.2pp |
| @32K | 0.8843 | 0.8229 | 0.8432 | **−2.0pp** | −4.1pp |

- **KV-FP4 边际代价随上下文放大**（−0.7 → −2.0pp），与"KV 误差在长上下文被 attention 反复消费"的机理一致。
- 重灾子任务 @32K（D−E）：`niah_multikey_3` **−16pp**、`ruler_cwe` **−12.2pp**——检索型多 key 任务对 K 量化最敏感。
- niah_single 系列全程无损（1.000）——单针检索对 KV-FP4 完全鲁棒。

## 3. Qwen3-4B 三臂结果

注意 4B 的 E 臂（llmat checkpoint 原样）本就是 **BF16 KV**（该 checkpoint 未做 KV 量化），故 4B 的 D−E = 纯 KV-FP4 代价（vs BF16-KV）。

### 3.1 GPQA-Diamond（thinking，198 × 3 runs，全部 0 错误）

| 臂 | run1 / run2 / run3 | 均值 |
| --- | --- | --- |
| A（BF16 全链路） | 0.5000 / 0.5253 / 0.5253 | **0.5168** |
| D（w4a4 + FP4-KV） | 0.4697 / 0.4848 / 0.4293 | **0.4613** |
| E（w4a4 + BF16-KV，原样） | 0.4596 / 0.4646 / 0.4848 | **0.4630** |

读数：**D−E = −0.2pp（纯 KV-FP4 代价 ≈ 0，噪声内）**；E−A = −5.4pp（llmat w4a4 的权重/激活代价）；D−A = −5.6pp。

### 3.2 RULER（去 vt 的 12 任务均值）

| @长度 | A | D | E | **D−E（纯 KV-FP4）** | E−A |
| --- | --- | --- | --- | --- | --- |
| 8K | 0.8720 | 0.8791 | 0.8854 | **−0.6pp** | +1.3pp（噪声内） |
| 32K | 0.8177 | 0.7879 | 0.8128 | **−2.5pp** | −0.5pp |

- 与 8B 同款模式：**KV-FP4 代价随上下文放大**（−0.6 → −2.5pp @32K）；重灾任务也相同（@32K：`niah_multikey_3` −12pp、`ruler_cwe` −9.4pp）。
- 单任务数值噪声大（50 题/任务，±5–10pp；如 niah_mk2 出现 D>E 的反向波动），解读以 12 任务均值为准。
- 4B 的 GPQA D−E ≈ 0 而 RULER 32K D−E = −2.5pp：**KV-FP4 的代价主要表现在长上下文，短上下文近乎免费**。

## 4. Qwen3-30B-A3B 三臂结果

### 4.1 GPQA-Diamond（thinking，198 × 3 runs，全部 0 错误）

| 臂 | run1 / run2 / run3 | 均值 |
| --- | --- | --- |
| A（BF16 全链路） | 0.6010 / 0.5859 / 0.6566 | **0.6145** |
| D（w4a4 + FP4-KV，修复后 07-05 重跑） | 0.5354 / 0.5303 / 0.5303 | **0.5320** |
| E（w4a4 + FP8-KV，生产现状） | 0.5707 / 0.6111 / 0.5758 | **0.5859** |

读数（turboeval 正式）：**D−E = −5.4pp**；E−A = −2.9pp（与 005 的 −3.7pp 交叉吻合）；D−A = −8.3pp。全部 0 错误、提交前过 gate-200 + hook first-call 双自检。
注意 D−E 与本地 evalscope 复现（§4.1.1，D−E = 0.0pp）**跨 harness 不一致**，两个候选解释：① E 臂 3-run 方差大（0.571–0.611，极差 4pp）而 D 臂极稳（0.530–0.535），−5.4pp 里有相当一部分可能是 E 的向上波动；② turboeval 是 \boxed 抽取 + 长 thinking 自由生成（KV 误差在数千 token 的思维链上累积、且退化模型更易破坏格式），evalscope 是 MCQ "ANSWER: X" 抽取（更宽容）——两者测的"GPQA 能力"并不完全同一。**保守解读：30B GPQA 的 KV-FP4 边际代价在 0 到 −5pp 之间，敏感度低于 RULER 长上下文。**

> ⚠️ **首轮 D 臂 3 个 run（0.5657/0.5606/0.6162，均值 0.5808）作废**：跑的时候 fused-path bug（见 §1）导致 fake-quant 未生效，实际测的是 w4a4 + BF16-KV。作废数据本身仍有参考价值：它是 30B 的"w4a4 + BF16-KV"读数，与 E（FP8-KV）0.5859 相差 −0.5pp（噪声内），说明 **FP8-KV vs BF16-KV 对 30B GPQA 无损**。

#### 4.1.1 本地复现（evalscope 0-shot MCQ；harness 不同，分数只做臂间比较，不与上表混排）

D 臂重跑改用本地 evalscope（`gpqa_diamond`，198 题 × repeats 3 = 594 次生成，0-shot，选项随机置换 seed=42 三臂一致，temperature 0.6 / top_p 0.95，top_k/min_p 服务器端 pin，打本地 sglang 端口，零 turboeval 配额）。**三臂全部用同一 harness 重跑**保内部可比；开跑前带 D 臂 fake-quant 活性自检（first-call 日志必须出现，否则 abort）。

| 臂 | evalscope 本地（594 均值） | turboeval 参照（3-run 均值） |
| --- | --- | --- |
| A（BF16 全链路） | 0.6128 | 0.6145（Δ −0.2pp，两 harness 高度吻合） |
| **D（w4a4 + FP4-KV，修复后）** | **0.5909** | —（无有效数据） |
| E（w4a4 + FP8-KV） | 0.5909 | 0.5859（Δ +0.5pp，吻合） |

读数：**D−E = 0.0pp——30B 在 GPQA 上 KV 从 FP8 压到 FP4 零边际代价**（与作废数据推出的"KV 格式对 30B GPQA 不敏感"互相印证）；E−A = −2.2pp（turboeval 口径 −2.9pp，同量级）。A/E 两臂跨 harness 偏差 ≤0.5pp，本地复现协议可信。

### 4.2 RULER

#### 4.2.0 turboeval 正式结果（2026-07-05 补齐，与 §2/§3 严格同 harness）

隧道加固后重跑（见 §6 事故记录），78/78 job 全部 completed、全部 0 trial 错误。12 任务均值（去 vt，口径同 §2/§3）：

| 均值 | A | D | E | **D−E** | E−A |
| --- | --- | --- | --- | --- | --- |
| @8K | 90.75 | 89.77 | 90.76 | **−1.0pp** | 0.0 |
| @32K | 87.44 | 78.83 | 85.42 | **−6.6pp** | −2.0pp |

- 重灾任务 @32K（D−E）：`niah_multikey_3` **−20pp**、`ruler_qa_squad` −17.8pp、`ruler_cwe` −9.0pp。
- 与本地官方 RULER（§4.2.1）方向与量级一致（本地 D−E @32K = −9.3pp，略更悲观），**两个独立 harness 双重确认"KV-FP4 代价随上下文急剧放大"**；重灾任务重合（multikey_3 两边都 −20pp、cwe 两边都重伤）。
- 跨模型同表比较（严格同 harness）：D−E @32K 为 8B −2.0pp / 4B −2.5pp / **30B −6.6pp**——30B 的 KV-FP4 敏感度确认显著高于 dense 小模型。

#### 4.2.1 本地复现（官方 NVIDIA RULER 数据 + 官方 evaluate.py；先于 turboeval 补齐完成，留作交叉验证）

turboeval 配额卡死后改本地栈：**官方 RULER 仓库**（clone 至 `low-precision-project/RULER`，唯一代码改动是 evaluate.py 的 nemo 依赖兜底，git diff 可查）生成数据（13 任务 × 8K/32K × 50 条，Qwen3 tokenizer，seed 42，三臂共用同一份数据），自写轻量 OpenAI 客户端打本地 sglang 端口（nonthink 模板 + `temperature=0.7, top_p=0.8, top_k=20, min_p=0` 服务器端 pin，协议同 turboeval 口径），**官方 evaluate.py** 评分。全部 3900 条预测 0 请求错误、0 空输出；开跑前同样过 D 臂 fake-quant 活性自检。与 turboeval 仅有的 2 个有效 job（A 臂 niah_single_1/2@8K = 1.0）交叉一致（本地同为 100.0）。

顺带确认：**turboeval 的 ruler_vt 失效是其 harness 的 artifact**——官方 RULER 的 vt 本地三臂 84.8–100 全部正常，因此本节均值给 13 任务全量与 12 任务（去 vt，与 §2/§3 口径对齐）两版。

| 均值 | A | D | E | **D−E** | E−A |
| --- | --- | --- | --- | --- | --- |
| @8K（13 任务） | 93.07 | 90.57 | 91.87 | **−1.3pp** | −1.2pp |
| @8K（12 任务，去 vt） | 92.49 | 90.22 | 92.39 | **−2.2pp** | −0.1pp |
| @32K（13 任务） | 89.64 | 77.64 | 86.38 | **−8.7pp** | −3.3pp |
| @32K（12 任务，去 vt） | 89.04 | 77.04 | 86.31 | **−9.3pp** | −2.7pp |

- **30B 的 KV-FP4 边际代价在 32K 急剧放大**：−1.3～−2.2pp（8K）→ **−8.7～−9.3pp（32K）**，远超 8B（−2.0pp）和 4B（−2.5pp）的同长度读数（注意：跨模型比较隔着 harness 差异，量级参考、方向可信）。
- 重灾任务 @32K（D−E）：`cwe` **−27.6pp**（19.8 vs 47.4，几乎崩塌）、`niah_multikey_2` **−20pp**、`niah_multikey_3` **−20pp**、`niah_multikey_1` −14pp。与 8B/4B 的重灾任务（multikey_3、cwe）完全同款，只是幅度更大。
- `niah_single` 系列全程 96–100——单针检索依旧鲁棒；qa_1/qa_2 三臂都低（46–58），是任务本身难度，D−E 在其上只有 0～−4pp。
- E−A @32K = −2.7～−3.3pp:FP8-KV 在长上下文也不是免费的（cwe：E 47.4 vs A 71.8）。
- 一个可能的机理注脚：Qwen3-30B-A3B 的 GQA 只有 **4 个 KV head**（8B/4B 为 8 个），单 head 的 KV 信息密度更高，对 KV 量化误差可能更敏感——待后续实验验证，仅作假设记录。

## 5. 结论与决策门判定

**损失矩阵总览（D−E = KV 压到 FP4 的边际代价）：**

| 模型 | GPQA | RULER @8K | RULER @32K | 备注 |
| --- | --- | --- | --- | --- |
| Qwen3-8B | −3.7pp | −0.7pp | −2.0pp | turboeval，12 任务口径 |
| Qwen3-4B | ≈0 | −0.6pp | −2.5pp | turboeval;E 臂为 BF16-KV |
| Qwen3-30B-A3B | **−5.4pp**（本地 MCQ 口径 0.0，保守取 0～−5pp 区间） | −1.0pp | **−6.6pp** | **turboeval（严格同 harness，07-05 补齐）**；本地 harness 交叉验证 @32K 为 −9.3pp，方向量级一致 |

**对照 004 §6 决策门：**

1. **D−E @32K RULER ≥3pp 的门被 30B 决定性触发**（turboeval −6.6pp / 本地 −9.3pp 双 harness 确认），8B/4B 在 2–2.5pp 的门槛边缘 → **Phase 1 KV-QAT 必要且收益明确，且优先级最高的目标模型是 30B-A3B（MoE/少 KV head）**。
2. **"短上下文掉点小、RULER 32K 掉点大"的模式在三个模型上全部成立**（30B：8K −1.0pp vs 32K −6.6pp）→ 主战场在长上下文，**Phase 1 的训练与评测重心应放 RULER 32K–128K**,短上下文能力基本免费（30B GPQA 的跨 harness 分歧见 §4.1，保守区间 0～−5pp,仍小于长上下文档）。
3. 敏感任务收敛一致（cwe、niah_multikey 系）→ KV-QAT 训练数据/评测应重点覆盖多 key 检索与词频聚合类长上下文任务。
4. **未决风险（已记录、本轮未消解）**：nvidia checkpoint 的 k/v_scale=1.0 是未标定占位值，本轮 s_global=1/6 直接继承该口径。若真标定（per-layer amax 校准）能显著收窄 D−E,则部分损失可用"校准"而非"QAT"收回——**上 QAT 前应先做这个便宜的 ablation**（scales 目录里 calib 产物已备好）。

**K3 佐证**：纯 KV-FP4 的分布偏移 8B 0.0133 / 4B 0.0185 / 30B 0.0229,与下游"短上下文近零、长上下文显著"的模式一致——K3 量级不大,但误差在长上下文被 attention 反复消费后放大为两位数掉点。

## 6. 运维备注

- turboeval **提交限流 20/分钟**：批量提交需 ≥4s 间隔，否则 create 静默返回空 job id（本次 8B RULER 两波共 12 个 job 因此重交）。脚本已内置限速 + 空 ID 自动重试。
- 隧道注册有 ~10s 延迟：起隧道后立即提交 job 会导致请求全部 err（诊断阶段踩过，job 快速烧完重试标记全错）；提交前先 curl 确认 gate 通。
- 两次 kill 自匹配事故（pgrep/pkill 模式匹配到自身命令行）→ 全部改为 `$!` 精确 PID 生命周期管理（`d2_model.sh`）。
- fake-quant 挂载点只覆盖 `MHATokenToKVPool`（bf16/fp16 KV 时激活）；E 臂 fp8 KV 走 fused 写入路径不受影响（by design）。
- **验证 hook 活性的教训（30B fused-path 事故）**：只看"服务器起得来、eval 出分数"完全不够——必须做**贪心分叉探针**（同 prompt 贪心解码，base vs fake-quant 序列必须分叉）+ **K3 显著高于噪声底** 两道检查。30B 首轮 GPQA D 臂就是漏了这两道检查直接跑分，白烧 3 个 job。不同模型实现可能走不同 KV 写入路径（dense vs MoE 就不同），**每换一个模型都要重新验 hook**。
- turboeval **日提交限额 200**：三臂 × 13 任务 × 2 长度的 RULER 一个模型就吃 78 个 job，加 GPQA smoke+3run 15 个，两个模型一天就撞墙。30B 的 RULER 因此被 429 挡住(等了 9+ 小时未恢复;07-05 服务端解除该限制)。
- **turbogate 隧道两连坑（07-05，共烧掉一轮 GPQA 3 job + 半轮 RULER）**：① 服务端维护后旧子域名被 **admin 回收**（`subdomain revoked by admin`），gate 全 403 但本地 health 正常——**提交前必须 curl gate `/v1/models` 验 200**，本地自检不够；② 换新子域名后，D 臂高负载时段隧道**每 40s 被服务端踢线重连**（默认 frpc 配置无心跳/池参数），job 表现为 `gateway recorded N/M request errors` + 完成 trial 全 0 分。修复：绕开 turbogate wrapper 直接跑 frpc,配置加固（`heartbeatInterval=10, heartbeatTimeout=120, poolCount=8, tcpMuxKeepaliveInterval=10`），并用 64 请求 × 强制 1500 token × 16 并发的持续压测（0 错误 0 重连）验收后才放行正式提交。加固后 8.1 全程 78+3 job 0 错误。
- **本地复现栈（零 turboeval 配额，本次 30B 全量数据来源）**：GPQA 用 evalscope 1.8.1（`gpqa_diamond`，ModelScope 数据源）；RULER 用官方 NVIDIA 仓库生成数据 + 自写 32 并发 OpenAI 客户端 + 官方 evaluate.py。速度远快于 turboeval（GPQA 三臂 594×3 次生成约 1 小时,RULER 三臂 3900 条预测约 25 分钟,4×B200 本地）。跨 harness 校验：GPQA 的 A/E 臂与 turboeval 偏差 ≤0.5pp;RULER 与 turboeval 仅有的 2 个有效 job 完全一致。局限：绝对分数不能与 turboeval 表混排（prompt/抽取逻辑不同），只做臂间差值解读。
- 本地 RULER 数据生成的坑（详见 RULER 仓库 git log/diff）：english_words.json 是 git-lfs pointer（机器无 git-lfs，需手动从 LFS API 取回，否则 **cwe 任务静默生成失败**）；HotpotQA 的 CMU 官方源已挂（用脚本内置的 HF 镜像）；prepare.py 会吞子进程错误恒报成功，**产物必须按行数硬验**。
