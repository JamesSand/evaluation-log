我现在想让你做一下这个事情

首先第一个事情是我需要你给我看一下这几个 model

llmat/Qwen3-4B-NVFP4

nvidia/Qwen3-30B-A3B-NVFP4

nvidia/DeepSeek-V4-Flash-NVFP4

的 weight q ，weight k，weight v 是不是 nvfp4 格式的

具体来说我想知道这三个 model 的 kv cache 是不是 nvfp4 的

如果这三个 model 没有的话。你就自己下载到 model-and-data folder

然后你去给我看验证一下上边的事情。验证完成之后把 report 写回这个 md 里边来

---

# 验证报告（2026-07-04）

**方法：** 三个模型均已在本地 `model-and-data/`（30B 此前已有；4B 与 DeepSeek 本次下载，后者 175.7GB/46 shards）。直接解析 safetensors **header**（tensor 名/dtype/shape，无需加载权重）+ 量化配置文件（`hf_quant_config.json` 或 config.json 的 `quantization_config`）。NVFP4 权重的存储特征：`U8` packed 权重（每 byte 两个 FP4）+ `F8_E4M3` 的 per-16-group `weight_scale` + `F32` 全局 `weight_scale_2`；KV cache 量化的特征：`kv_cache_quant_algo` 字段 + attention 层的 `k_scale`/`v_scale` 标量。

## 1. 结论总表

| 模型 | Q/K/V **权重**是否 NVFP4 | **KV cache** 是否 NVFP4 |
| --- | --- | --- |
| `llmat/Qwen3-4B-NVFP4` | ✅ 是（w4a4） | ❌ **否——完全未量化**（bf16 KV） |
| `nvidia/Qwen3-30B-A3B-NVFP4` | ✅ 是（w4a4） | ❌ **否——KV 是 FP8**，不是 NVFP4 |
| `nvidia/DeepSeek-V4-Flash-NVFP4` | ❌ **否——attention 权重全部保持 FP8**，未被 NVFP4 量化 | ❌ **否——完全未量化** |

我有一个问题，你说 weight q k v 都是 nvfp4 ，那为什么 kv cache 用的是 fp8 存的？那如果是这样的话，具体计算的时候是不是还是得把 fp8 的 kv cache quant 成 nvfp4 计算？你把这里具体的计算逻辑图给我画到这个 md 里边来

## 2. 解答：FP8 KV cache 从不会被转成 NVFP4——两种量化作用在不同的运算上

**一句话回答：不需要、也从不发生 FP8 → NVFP4 的转换。** NVFP4 只存在于 **linear 层的 GEMM**（权重 × 激活）里；KV cache 参与的是 **attention 运算**（QKᵀ 和 softmax·V，激活 × 激活），它在 **FP8 域**直接完成。两个量化域各管各的，中间永远以 BF16 交接。

以 `nvidia/Qwen3-30B-A3B-NVFP4`（w4a4 + KV FP8）为例，一个 attention 层的完整数据流：

```
                 hidden state X (BF16)
                        │
                        │ ① 动态量化：X → FP4
                        │    （per-16 block scale，checkpoint 的 input_scale 提供全局 scale）
                        ▼
        ┌──────────────────────────────────────┐
        │  q/k/v_proj GEMM： X_fp4 × W_nvfp4    │ ← W 即验证过的 NVFP4 权重：
        │  FP4 Tensor Core，FP32 累加           │    U8 packed + weight_scale(FP8,/16)
        └──────────────────────────────────────┘    + weight_scale_2(F32)
                        │
                        │ ② GEMM 输出解量化回 BF16 ← 注意：q/k/v 激活此刻是 BF16，
                        ▼                            不是 FP4！
          q, k, v (BF16) ── q_norm/k_norm + RoPE（BF16 域完成）
                        │
          ┌─────────────┴─────────────┐
          │ q (BF16)                  │ k, v (BF16)
          │                           │ ③ 用静态标定的 k_scale/v_scale
          │ ④ attention kernel 把 q   │    量化成 FP8 E4M3，写入 KV cache
          │    也量化到 FP8（vLLM 日志  ▼
          │    原话 "TRTLLM attention ┌─────────────────────────┐
          │    (query is quantized)"）│   KV cache（FP8 E4M3）   │ ← 常驻显存，2× 省
          │                           └─────────────────────────┘
          ▼                                       │
        ┌──────────────────────────────────────────┐
        │ ⑤ attention：QKᵀ → softmax → ·V           │
        │    在 FP8 域完成（FP8 MMA / kernel 内解量化，│
        │    scale 融合进 kernel，FP32 累加）         │
        │    ——整个过程没有任何 NVFP4 参与——           │
        └──────────────────────────────────────────┘
                        │ 输出 BF16
                        ▼
          o_proj GEMM（回到 ① 的 NVFP4 路径：动态量化 → FP4×FP4）
                        │
                        ▼  BF16 继续进 FFN（同样的 NVFP4 GEMM 路径）
```

拆开你的两个疑问：

**Q1：为什么权重是 NVFP4，KV cache 却用 FP8 存？**
因为它们量化的是不同性质的东西，约束完全不同：
- **权重**是静态的：可以离线校准、per-16-group 精细分 scale，FP4 的表示损失可控（我们 007/011 实测 w4a4 全模型 K3 ≈ 0.03–0.05）；
- **KV cache 是逐 token 的激活**：动态、有 outlier、且 attention 分数对 K/V 误差敏感。FP8 E4M3（约 2 位尾数 + 静态 per-tensor scale）对 KV 接近无损，FP4 E2M1（1 位尾数）风险大得多；
- **kernel 生态**：Blackwell 上 TRT-LLM/FlashInfer 的 attention kernel 原生支持 FP8 KV 读取；FP4-KV 的 attention kernel 不在主流路径上（sglang 有实验性的 `--kv-cache-dtype fp4_e2m1` 入口，但 ModelOpt 官方导出没有走）。
所以 "NVFP4 模型" 的准确含义是：**linear 层的权重和输入激活是 FP4，attention/KV 那一段是 FP8（或不量化）**。

**Q2：计算时要不要把 FP8 KV 转成 NVFP4？**
**不要。** NVFP4 Tensor Core 路径只服务于 "激活 × 权重" 的 GEMM；attention 的 QKᵀ/PV 是 "激活 × 激活"，引擎里根本没有 FP4 的 attention 计算路径。FP8 KV 直接被 attention kernel 消费（连 query 都被量化到 FP8 来配合，见图中 ④，这是我们自己 serving 日志里的实证）。唯一反复发生的格式转换是图中 ①：**每个 linear 层入口把 BF16 激活动态量化成 FP4**——这是 w4a4 推理的固定开销，与 KV cache 无关。

这也正是 qat-for-kv 的空间：如果想把 KV cache 也压到 FP4（再省 2× 显存），缺的是两样——能读 FP4 KV 的 attention kernel（推理侧），和让模型适应 FP4 KV 误差的训练（QAT 侧）；现有公开生态两样都还没有成品。

---

**直接回答核心问题：三个模型没有任何一个的 KV cache 是 NVFP4。** 目前公开 NVFP4 checkpoint 的 KV cache 至多到 FP8（ModelOpt 的标准导出路径），NVFP4 KV cache 在这三个代表性模型里均不存在。

## 3. 逐模型证据

### 3.1 llmat/Qwen3-4B-NVFP4（社区量化，compressed-tensors / llm-compressor）

- `config.json.quantization_config`：`format: nvfp4-pack-quantized`，weights 与 input_activations 均为 4-bit float group16（= w4a4），targets 全部 Linear、仅 ignore `lm_head`；**`kv_cache_scheme: null`**。
- layer-0 tensor 实证：`q/k/v_proj.weight_packed U8` + `weight_scale F8_E4M3 [.,160]` + `weight_global_scale F32` → **QKV 权重是 NVFP4** ✅；全模型**无任何 `k_scale`/`v_scale`** → **KV cache 未量化**（运行时 bf16）。

### 3.2 nvidia/Qwen3-30B-A3B-NVFP4（ModelOpt 官方）

- `hf_quant_config.json`：`quant_algo: NVFP4`（group 16），exclude 仅 MoE gates + lm_head（不含任何 attention 投影层）；**`kv_cache_quant_algo: FP8`**。
- layer-0 tensor 实证：`q/k/v_proj.weight U8` + `weight_scale F8_E4M3` + `weight_scale_2 F32` + `input_scale F32` → **QKV 权重是 NVFP4（w4a4）** ✅；`k_proj.k_scale` / `v_proj.v_scale`（F32 标量）存在 → **KV cache 是 FP8（E4M3 + 静态标定 scale），不是 NVFP4**。

### 3.3 nvidia/DeepSeek-V4-Flash-NVFP4（ModelOpt 官方，producer `dsv4-nvfp4-experts`）

- `hf_quant_config.json`：**`quant_algo: MIXED_PRECISION`**——`quantized_layers` 里**只有 `layers.N.ffn.experts` 是 NVFP4**；`exclude_modules` 含 **`*.attn.*`**（整个 attention 被排除）；**`kv_cache_quant_algo: null`**。
- tensor 实证（注意它是 MLA 架构，没有普通 q/k/v_proj，对应物是 `wq_a/wq_b`（query 低秩）、`wkv`（KV 压缩投影）、`wo_a/wo_b`）：这些 attention 权重全部是 **`F8_E4M3` + `F8_E8M0` 块 scale**（即沿用 DeepSeek 原生 FP8/MXFP8 格式，NVIDIA 只把 FFN experts 重量化成了 NVFP4）；对照组 `ffn.experts.*.w1.weight U8 + weight_scale F8_E4M3 + input_scale` 确为 NVFP4 w4a4。全模型**无 `k_scale`/`v_scale`** → **KV cache 未量化**（MLA 的 KV cache 本身是压缩 latent，以 bf16 存）。
- 顺带说明：模型名叫 "NVFP4" 但实际只有 experts 是 NVFP4——attention 和 KV 都不是。

## 4. 附：专攻 attention 的 NVFP4 —— SageAttention3 精读（2026-07-04 补）

> **SageAttention3: Microscaling FP4 Attention for Inference**（Tsinghua thu-ml，arXiv 2505.11594，NeurIPS 2025 Spotlight，[代码开源](https://github.com/thu-ml/SageAttention)）。
> 它填的正是上文数据流图里 ④⑤ 那一格：把 attention 的两个矩阵乘（QKᵀ 和 P·V）本身放到 **FP4 Tensor Core** 上算——RTX5090 上 1038 TOPS，比最快的 FlashAttention 快 ~5×。

### 4.1 方法拆解

**1. 微缩放 FP4 量化（选 NVFP4 而非 MXFP4）**
对矩阵按 **1×16 块**分 scale：`s = max(|X_block|)/6`（6 = E2M1 最大值），`X̂ = round_fp4(X/s)`，scale 存 FP8 E4M3。
硬件的 `FP4MM(Â, s_A, B̂, s_B)` 指令把反量化融合在 MMA 里直接出 FP32 结果。
paper 实测对比了两种 FP4：**NVFP4（1×16 块 + E4M3 scale）明显优于 MXFP4（1×32 块 + E8M0 scale）**——attention 量化对块粒度和 scale 精度都敏感。这个结论对我们做 FP4-KV 直接有用。

**2. 两个 GEMM 全部 FP4 + 沿用 Sage 系列的 smoothing**

```
Q,K,V (FP16/BF16 tile，FlashAttention 分块)
  │  预处理：K ← K − mean(K)（通道 smoothing）
  │  每个 Q tile：q̄=mean(Q)，量化 (Q−q̄)
  ▼
S = FP4MM(Q̂,s_Q, K̂,s_K) + GEMV(q̄, Kᵀ)   ← QKᵀ 在 FP4 域，均值项用 GEMV 补偿
  ▼
P̃ = OnlineSoftmax(S)                      ← softmax 仍在 FP32
  ▼
两级量化 P̃（见下）→ P̂₂, s_P2, s_P1
  ▼
O = FP4MM(P̂₂,s_P2, V̂,s_V) × s_P1          ← P·V 也在 FP4 域
```

**3. 核心创新：P̃ 的两级量化（two-level scaling）**
难点：online softmax 出来的 P̃ ∈ [0,1]，于是块 scale = rowmax/6 ∈ [0, 0.167]——把 FP8 E4M3 的可表示范围（±448）浪费到只用零头，scale 本身的表示误差就把精度吃掉了（直接量化会崩，paper 图 3/图 12 有实证）。
解法：先用 **FP32 的 per-row 外层 scale** 把每行拉满量程，再做标准 NVFP4 块量化：

```
s_P1 = rowmax(P̃)/(448×6)   (FP32，每行一个)
P̃₂  = P̃/s_P1               → 块 scale 现在能填满 E4M3 的 [0,448]
s_P2, P̂₂ = ϕ(P̃₂)           (标准 NVFP4：FP8 块 scale + FP4 值)
最终 P̃ ≈ P̂₂ × s_P2 × s_P1   （两级：FP32 行 scale × FP8 块 scale）
```

**4. kernel 工程**（能落地的关键）：K 列置换以匹配 FP4 MMA 累加器布局（融合进量化 kernel）；量化的 16 元素 max 归约与 online softmax 的行 max **共享 shuffle**（省 50% shuffle，整 kernel +10%）；producer warp 之间 ping-pong 重叠 load 与 store（寄存器压力下的取舍）。

### 4.2 精度清单与定位（和前文两个工作对照）

| 方案 | linear GEMM | attention 计算 | KV cache **存储** |
| --- | --- | --- | --- |
| nvidia NVFP4 checkpoint | **FP4**（w4a4） | FP8 / BF16 | FP8 / BF16 |
| OSCAR | BF16 | 浮点（INT2 只是存储） | **INT2**（混合布局） |
| **SageAttention3** | 不涉及 | **FP4**（QKᵀ 和 PV 都在 FP4 Tensor Core） | **不动——仍是 FP16/BF16** |

**最重要的澄清：SageAttention3 不是 KV cache 量化。** K/V tile 是从（高精度的）KV cache 读出后**在 kernel 内逐 tile 现场量化成 FP4** 去喂 Tensor Core 的——省的是算力/带宽（TOPS），**不省 KV 显存**。它和 OSCAR 恰好互补：OSCAR 管"存"（INT2 存储、浮点计算），Sage3 管"算"（高精度存储、FP4 计算）。

### 4.3 对 qat-for-kv 的直接启示

1. **"FP4-KV attention kernel 不存在"的缺口，Sage3 补了一半**：它证明 attention 两个 GEMM 可以在 NVFP4 域算而基本不掉点（视频/图像生成模型上近无损；LLM 上未保证全模型无损，长文本要自己验证）。剩下那一半是把 KV **存储**也变 FP4——直接从 FP4 cache 读 tile 喂 FP4MM，连 kernel 内量化都省了，但要解决 V 沿 seq 维度的 scale 分块与 cache 布局问题。
2. **两级 scale 的思想可以搬到 FP4-KV**：KV 数值分布同样可能挤在窄区间（尤其 RoPE 后的 K），"FP32 外层粗 scale × FP8 块 scale"是对付 E4M3 scale 量程浪费的通用配方。
3. **格式选型有了实证**：做 FP4-KV 应优先 NVFP4（1×16+E4M3）而非 MXFP4——Sage3 在真实 Q/K/V 上测过两者差距。
4. QAT 侧的目标函数也有了着力点：Sage3/OSCAR 都指出 attention 误差的关键在 P̃ 和 K 的量化敏感方向——训练时对齐的应该是 attention 输出（乃至最终 logits，即我们的 K3 口径），而不是 KV 张量本身的重建误差。

## 5. 附：NVFP4 KV cache 的实战现状 —— Tony 的 TRT-LLM 调查笔记（2026-07-04 补）

> 来源：`tony-fp4kv.zip`（Notion 导出，已解压到 [`tony-fp4kv-extracted/`](tony-fp4kv-extracted/)），
> 《Investigating TRT-LLM's NVFP4 KV Cache Accuracy Loss》。这份笔记直接推翻了"公开生态没有 NVFP4 KV
> cache"的绝对说法——**TRT-LLM 已有 NVFP4 KV cache 路径**，但精度掉得异常，笔记追查并定位了根因。

### 5.1 核心结论

TRT-LLM 的 NVFP4 KV cache 掉点严重，**但罪魁不是 NVFP4 精度本身，而是 TRT-LLM 引擎实现**。

### 5.2 方法：SGLang 里的"假量化"对照实验

在 SGLang 加 Triton kernel：KV 写入 cache 前量化到 NVFP4 精度（two-level scale 与 TRT-LLM 完全一致：
FP32 per-tensor 全局 scale + FP8 per-group scale，同一套标定 scale）再**反量化回 BF16 存储**——量化误差
与真 NVFP4 一模一样，但走 SGLang 的正常 BF16 attention。这样把"量化误差"和"引擎实现"两个变量干净拆开。

### 5.3 证据链（四个实验）

| 实验 | 模拟 NVFP4（SGLang） | 真 NVFP4（TRT-LLM） |
| --- | --- | --- |
| Qwen3-8B ruler_vt @32K | −0.52 pp | **−2.52 pp** |
| Qwen3-8B niah_mk2 @32K | −5.00 pp | **−9.40 pp** |
| SWE-bench Verified（SWE-1.6，前 100 题） | 解出 31–32 | **解出 20–23** |
| SWE-1.6 ruler_vt @32K | 1.000（≈BF16 无损） | **0.7424（−25.8 pp）** |

实验 4 排除 attention backend 嫌疑：模拟路径搬到 SGLang 的 `trtllm_mha` FP8-matmul backend 上无退化。

### 5.4 根因与 TRT 的真实数据流

逐层激活对比锁定：**TRT 的 FP8 attention 把 o_proj 的 input scale 融进了 FP8 输出，SGLang 没有**。
TRT 的 NVFP4-KV 数据流（见 `tony-fp4kv-extracted/.../TRT_FP4KV.png`）：

```
Q: BF16 proj → norm+RoPE → 量化 FP8（⚠️ 未标定，无 Q scale）─┐
K: BF16 proj → norm+RoPE → 量化 NVFP4 → cache ─ 读出反量化到 FP8 ─┤→ QK FP8 → softmax FP32
V: BF16 proj → 量化 NVFP4 → cache ────────── 读出反量化到 FP8 ─┘   → P量化FP8 → PV FP8 → o_proj FP8
```

注意：**又一个"存储格式 ≠ 计算格式"的实例**——K/V 存 NVFP4，但 attention 计算在 FP8 域（对照上文
SageAttention3 是反过来：存 BF16、算 FP4）。另外 TRT 的 NVFP4-KV 在 prefill 阶段实际用的是 FP8 KV。

### 5.5 三个修复机会（笔记的可执行结论）

1. **给 Q 的 FP8 量化加标定 scale**（TRT 目前不用 Q scale，引擎侧易修）；
2. **支持 FP8 o_proj + NVFP4 MLP/MoE 组合**（目前 TRT 只支持 BF16 attention + NVFP4 MLP，修复无需改 kernel）；
3. **Q(FP8)/K(NVFP4) 精度不对称的补偿**：学一组 scale，放大 K norm 权重、反向缩小 Q norm 权重——输出数学
   等价，但压掉 K 激活的 outlier（K 是量化误差主源）。

前两项修完，TRT 可追平 SGLang 模拟精度；加第三项可**超过**模拟精度。第 3 项本质已是轻量标定/QAT 思路，
与本项目直接接轨。

### 5.6 对 qat-for-kv 的修正与启示

- **修正前文结论**：NVFP4 KV cache 并非完全没有实现——TRT-LLM 有（`kv_cache_quant_algo` 之外的引擎能力），
  只是目前实现有缺陷、公开 checkpoint 没有以它为目标导出。SGLang 侧则确认没有原生 FP4-KV 路径（本笔记
  是用假量化模拟的）。
- **"假量化 KV"是现成的 QAT 训练原语**：Tony 的 Triton kernel（quantize→dequantize→BF16 存储）就是 QAT
  前向所需要的 fake-quant 算子，且已验证与真实 NVFP4 误差一致——训练侧可以直接复用这个模拟口径。
- **K 的 outlier 是主要误差源**（与 SageAttention3 的 smoothing、OSCAR 的旋转都指向同一处）——QAT 的
  设计重心应放在 K 侧（RoPE 后分布），V 相对宽容。
- 模拟 NVFP4-KV 本身的净损失其实不小（niah_mk2 −5 pp @32K）——这就是 QAT 要去收复的空间；OSCAR 式
  旋转 + 两级 scale + QAT 三者叠加是合理的技术路线。

## 6. 方向评估：w4a4 + NVFP4-KV 的 inference 能不能做？（2026-07-04）

> 问题：想做 **w4·a4·kv4（NVFP4）** 的推理，方向可行吗？创新点和实现阻碍在哪？
> 结论：**能做，且时机刚好**——四块拼图都各自被证明过一半，没人组装过，KV 侧的训练适应是真空地带。

### 6.1 可行性：四块拼图的现状

| 拼图 | 现状 | 缺口 |
| --- | --- | --- |
| w4a4 linear | ✅ 成熟（实测 30B GPQA 只掉 2–4 分，QAD 后追平 bf16，见 005/013 与 011 sweep） | 无 |
| NVFP4 KV **存储** | TRT-LLM 已有路径但实现有 bug（§5，根因已定位 + 3 个修法） | 修好它 / 自己做对 |
| FP4 attention **计算** | SageAttention3 已证明可行（§4，两级 P 量化 + smoothing，开源） | 它只算不存（tile 从 BF16 现场量化） |
| KV 量化的**训练适应（QAT）** | **完全空白**——已验证所有公开 QAD checkpoint 的 KV 都只是 PTQ scale | ★ 最大创新空位 |

损失底线的量化依据：Tony 假量化实验（§5.3）给出 NVFP4-KV 纯量化损失——ruler_vt −0.5pp、
**niah_mk2 @32K −5pp**。这 5pp 就是要收复的空间，且不是格式极限（OSCAR 在 INT2 都能救回来），
只是没人给它做过 rotation/QAT。

### 6.2 创新性问题（按价值排序）

1. **KV-aware QAT（最大空位）**：现有 QAD 只适应权重/激活量化。把 KV fake-quant 插进 QAD 蒸馏
   loop 的边际成本极低（011 sweep 实证 QAD 前 50 步完成 70–90% 收敛）；Tony 的 Triton 假量化
   kernel 就是现成的训练原语（误差口径已验证与真实 NVFP4 一致）。§5.5 的机会 #3（Q/K norm 间学
   scale）是这个方向的最小版本，推广成完整 QAT 即论文级贡献。
2. **端到端全 4-bit 数据通路（w4·a4·kv4 + FP4 attention 计算）**：无公开先例。卖点："存也 FP4、
   算也 FP4"——从 FP4 cache 读出直接喂 FP4MM，省掉 Sage3 的 kernel 内量化；decode 是带宽瓶颈，
   KV 流量比 BF16 少 4×，长上下文 decode 加速理论上比 Sage3 的纯算力加速更值钱。
3. **K 侧 outlier 在 FP4-KV 约束下的处理**：三个前置工作都指向 K 是误差主源（RoPE 后结构性
   outlier），但 OSCAR 的旋转为 INT2 min-max 设计、Sage3 的 smoothing 为计算路径设计——**与
   NVFP4 的 1×16 块 + E4M3 scale + paged cache 布局兼容的 K 变换**是新设计空间（两级 scale 怎么
   分层：per-token FP32 外层 × per-16 E4M3 内层？外层 per-head 还是 per-token？）。
4. **定位问题（必须提前想清楚）**：OSCAR 已做到 INT2（BPE 2.38），FP4-KV ≈ 4.5 BPE——光比显存
   会输。答辩点：(a) FP4 走硬件 FP4MM，算力+带宽双赢（OSCAR 的 INT2 只省存储，计算仍需解包回
   浮点）；(b) 全 FP4 通路工程简洁（无三段混合布局/窗口降级 kernel）；(c) QAT 加持后精度上限更高。
   这个定位本身值得一节实验去论证。

### 6.3 实现阻碍（按难度排序）

1. **Attention kernel 是硬骨头（最大工程量）**：公开生态没有"读 NVFP4 paged KV"的 attention
   kernel。要解决 FP4MM operand layout 与 paged cache 布局的对齐、per-16 scale 在 page table 的
   存放、prefill 写入 kernel、sglang paged-attention 集成。可借力：Sage3 kernel 开源可改、sglang
   有 `--kv-cache-dtype fp4_e2m1` 入口壳子、OSCAR 的分段+merge 三 kernel 结构是架构参考。
   预估数周级 CUDA/Triton 工作。
2. **精度悬崖在长上下文**：−5pp @32K 是裸奔数字，128K 会更差；rotation/smoothing/QAT 至少叠两样。
   K 敏感、V 宽容，预算要偏科。
3. **MLA 模型另说**：latent KV 本身是信息瓶颈，再叠 FP4 风险大；第一阶段圈定 GQA 模型（Qwen3 系）。
4. **验证陷阱（008 的教训）**：任何"掉点"必须双引擎交叉验证 + 假量化对照，否则会把引擎 bug 归因
   给方法。K3/GPQA/RULER 全套 harness 直接复用（007/011 的脚本在 evaluation-log）。

### 6.4 建议攻关顺序（每步有独立产出）

1. **Phase 0（1–2 天，纯软件）**：Tony 式假量化在 sglang 里扫 NVFP4-KV 损失曲线（模型 × 上下文
   长度 × scale 方案），叠加离线 rotation/smoothing 消融 → 产出"设计空间地图"。
2. **Phase 1（1–2 周）**：KV fake-quant 插进 QAD，训 50–200 步，测恢复量（GPQA + K3 + RULER）。
   便宜且单独可成 workshop 级结果。
3. **Phase 2（数周）**：FP4-KV decode attention kernel（从 Sage3 + sgl-kernel 起步）。
4. **Phase 3**：组装 w4a4+kv4 端到端，accuracy + throughput @32K–128K，对比 FP8-KV 与 OSCAR。

**一句话结论**：方向成立，创新空位真实（KV-QAT + 全 4-bit 通路组合）；最大风险不是"能不能"，
是 kernel 工程量与 "vs OSCAR / FP8-KV" 的定位论证。Phase 0/1 便宜低风险，建议先跑。

## 7. 对 qat-for-kv 项目的含义

- 现有公开 NVFP4 生态中 **KV cache 量化的上限是 FP8**（且只有 NVIDIA 的 dense/MoE 导出用了）；NVFP4 KV cache 没有现成 checkpoint 可参照——如果本项目要做 NVFP4-KV 的 QAT，基线需要自己构造（ModelOpt 的 `kv_cache_quant_algo` 字段格式上支持扩展，serving 侧 sglang 有 `--kv-cache-dtype fp4_e2m1` 选项可作为推理端起点，见 turbo-sglang 的 flag 清单）。
- 本地路径：`model-and-data/Qwen3-4B-NVFP4-llmat`、`model-and-data/Qwen3-30B-A3B-NVFP4`、`model-and-data/DeepSeek-V4-Flash-NVFP4`。










