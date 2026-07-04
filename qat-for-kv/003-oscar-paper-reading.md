
你能给我解释一下这篇paper
oscar 是怎么处理 kv cahce 的吗？
你读一下这个 paper 把流程图画给我看

https://arxiv.org/pdf/2605.17757

---

# OSCAR 精读笔记（2026-07-04）

**OSCAR: Offline Spectral Covariance-Aware Rotation for 2-bit KV Cache Quantization**
（Zhongzhu Zhou*, Donglin Zhuang*, et al. — Together AI / U. Sydney / UIUC，arXiv 2605.17757）

## 1. 一句话概括

把 KV cache 压到 **INT2**（每元素 2 bit，128K 上下文时有效 BPE ≈ 2.38）还能基本不掉点。核心洞察：**KV 量化的目标不该是"重建 K/V 本身"，而是"保住 attention 实际消费的东西"**——K 通过 logits（QKᵀ）被消费，V 通过分数加权聚合（S·V）被消费，所以旋转矩阵应该从 **attention 诱导的协方差**（而不是 K/V 自身的协方差）里离线求出来。

## 2. 它怎么处理 KV cache（三层机制）

### 2.1 离线校准：attention-aware 旋转

标准做法（QuaRot 等）用数据无关的 Hadamard 旋转摊平 outlier，但 INT2 下直接崩（Qwen3-4B 上 QuaRot-INT2 平均分 1.40，等于全毁）。OSCAR 的改进：

- **Key 侧**：logits 失真 ‖QKᵀ − QK̂ᵀ‖² 由 **query 协方差 C_Q = (1/N)Σ qᵀq** 控制（不是 KᵀK！）。对 C_Q 做特征分解取 U_Q 作为 K 的基旋转——把量化误差从 query 最"在乎"的方向上挪开。
- **Value 侧**：输出失真 ‖SV − SV̂‖² 由 **分数加权协方差 C_S = (1/N)·VᵀSᵀSV** 控制。特征分解取 U_S 作为 V 的基旋转——被 attention 高权重读取的 value 方向少受误差。
- 最终旋转 = 基旋转 ∘ Hadamard ∘ 位反转置换：**R_K = U_Q·H_Had·P_br，R_V = U_S·H_Had·P_br**（H_Had 摊平通道能量，P_br 把大/小方差通道交错，让相邻通道动态范围接近——服务 per-group min-max scale）。
- 有最优性定理背书：在"残差协方差在环境基下对角"假设下，U_Q/U_S 是冻结误差代理目标的最优解（Thm 1）。
- 校准成本极小：**默认 8k 条 GPQA-Diamond 样本，一次性离线**；clip 阈值也从校准激活的分位数拟合（c_K, c_V = 0.96, 0.92），不在下游任务上调。

### 2.2 量化本体

对**旋转后**的 K/V 做 **affine 非对称 INT2**（per-token、head 维分 group，block 128/64/32），配 percentile clipping 控 outlier，4 个 2-bit 值打包进 1 byte。

### 2.3 在线混合精度布局（关键工程设计）

不是所有 token 都 INT2——cache 逻辑上分三段：

```
[1, S0] sink 段        │ [S0+1, t−W] 中段历史                │ [t−W+1, t] recent 段
BF16 原精度            │ 旋转 + clip + INT2（大头，~全部长度）│ BF16 原精度
（attention sink 保命）│                                     │（最新 W 个 token 保命）
```

## 3. 完整流程图

### 3.1 离线阶段（一次性）

```
校准集（8k 条 GPQA-Diamond prompt）
   │  跑一遍模型，逐层逐头收集
   ▼
┌────────────────────────────────────────────────┐
│  Q 激活 ──► C_Q = (1/N)Σ qᵀq ──► 特征分解 U_Q     │
│  S,V 激活 ─► C_S = (1/N)VᵀSᵀSV ─► 特征分解 U_S    │
│  旋转后激活分位数 ──► clip 阈值 (c_K=0.96, c_V=0.92)│
└────────────────────────────────────────────────┘
   │
   ▼
固定旋转矩阵  R_K = U_Q·H_Had·P_br ，R_V = U_S·H_Had·P_br
   │
   └─ R_V 直接吸收进投影权重（W_V 出来的 v 天生已旋转，省在线计算）
```

### 3.2 在线阶段（serving，已集成进 SGLang + paged-attention）

```
                      新 token 的 k_t, v_t (BF16)
                             │
              ┌──────────────┴───────────────┐
              │ prefill：fused Triton kernel  │
              │  k⁺ = Q₂⁺(clip(k_t·R_K, τ_K)) │──► 直接写 INT2 中段
              │  v⁺ = Q₂⁺(clip(v_t·R_V, τ_V)) │    （4 值/byte 打包）
              └──────────────────────────────┘
              │ decode：先以旋转后 BF16 写入 recent 窗口
              ▼
   ┌─────────────────────────────────────────────────┐
   │ KV cache:  [BF16 sink] [INT2 中段] [BF16 recent] │
   └─────────────────────────────────────────────────┘
              ▲
              │ 窗口滑动时：最老的 recent token 被 fused kernel
              │ "降级"（同样 clip+INT2 量化）挪进中段
              ▼
      ┌──────────── decode attention（3 个 kernel）────────────┐
      │ ① INT2 段 kernel：byte 解包 → scale/zero 反量化 →       │
      │    浮点累加（q 侧同样过 R_K，正交不变性保 logits 不变）    │
      │ ② BF16 段 kernel：sink + recent（元素量少几个数量级）     │
      │ ③ 共享 merge kernel：online-softmax 合并两路部分结果      │
      └───────────────────────────────────────────────────────┘
```

## 4. 关键数字

| 维度 | 结果 |
| --- | --- |
| 精度（5 benchmark 平均 gap vs BF16） | Qwen3-4B-Thinking **−3.78**，Qwen3-8B **−1.42**；QuaRot-INT2 直接崩到 ~0 |
| 规模化 | Qwen3-32B、GLM-4.7 (358B) 上与 BF16 基本持平（AIME25: 74.00 vs 72.59） |
| 长上下文 | RULER-NIAH 到 128K 保持稳健（naive rotation INT2 崩溃） |
| 显存 | KV cache ≈ **8× 压缩**（BPE 2.38 @128K，含 scale/zero 和混合精度开销） |
| 吞吐 | 同显存预算大 batch **至多 7×**；bs=1 解码 **至多 3×**（省带宽） |

## 5. Q / K / V 分别是什么精度（精度清单）

OSCAR **只量化 KV cache 这一个对象**，权重和其他激活一律不动——和我们验证过的 NVFP4 模型（w4a4 权重/激活量化、KV 反而 FP8/不量化）正好是光谱的两端：

| 对象 | OSCAR 中的精度 | 说明 |
| --- | --- | --- |
| **W_Q / W_K / W_V / W_O 权重** | **不量化**（Qwen3 系 = BF16；GLM-4.7 = 其原生 FP8） | OSCAR 是纯 KV-cache 方案，权重零改动（R_V 吸收进 W_V 是等价正交变换，不是量化） |
| **Q（query 激活）** | **BF16，从不缓存** | 每步现算现用；在线乘 R_K 进旋转空间和 K 对齐（正交旋转不改 logits） |
| **K cache** | **三段混合**：sink（前 S0 个 token）= 旋转后 BF16；中段历史 = 旋转 + clip + **INT2**；recent（最新 W 个）= 旋转后 BF16 | decode 新 token 先以 k·R_K（BF16）写进 recent，滑出窗口时被 fused kernel 降级成 INT2 |
| **V cache** | 同 K 的三段布局（旋转用 R_V，且 R_V 已吸收进 W_V，v 天生已旋转） | |
| **attention 计算** | INT2 段在 kernel 内解包+反量化后**浮点累加**；softmax 与两路 merge 均为浮点 | INT2 只是存储格式，计算不在 2-bit 域进行 |
| **有效 BPE** | **≈2.38 bits/元素**（@128K，Qwen3-8B） | 高于理论 2.0 的部分来自 per-group scale/zero 元数据 + 两个 BF16 窗口的摊销 |

一句话：**OSCAR = w16·a16·kv2（混合布局）**；我们前面看的 nvidia NVFP4 模型 = w4·a4·kv8。两者动的维度完全互补——这也正说明 KV cache 量化和权重/激活量化是两个独立可组合的旋钮。

## 6. 读后感 / 与 qat-for-kv 的关系

1. **OSCAR 是纯 PTQ、零训练**：全部机制在离线校准（协方差 + 分位数）+ 推理 kernel 里完成，模型权重一个不动（除了把 R_V 吸收进 W_V 属于等价变换）。这与我们 qat-for-kv 的思路正交且互补——OSCAR 证明"推理侧的变换设计"能把 KV 压到 2-bit，QAT 则是从训练侧让模型适应 KV 量化误差；两者可叠加（先 OSCAR 旋转、再对残余误差做 QAT）。
2. **"面向下游消费者做量化"的思想可以直接搬**：我们前面验证过（[001-instruction.md](001-instruction.md)）NVFP4 生态的 KV 至多 FP8、attention kernel 不支持 FP4-KV。OSCAR 展示了完整的解法模板：自定义 attention kernel（分段 + 反量化 + merge）+ 混合精度布局（sink/recent 保 BF16）+ 离线校准的旋转。如果做 FP4-KV，这三件套一样都少不了。
3. **和我们 K3 实验的呼应**：OSCAR 的图 2 用 KL(p_FP16‖p_q) 度量 attention 分数失真——和我们 007/011 用 K3 KL 度量模型分布偏移是同一路数量化诊断，只是他们测在 attention score 层，我们测在最终 token 分布层。
4. 实用细节值得记：sink + recent 双 BF16 窗口是 INT2 不崩的保命机制（消融里去掉就崩）；校准只要 8k 条、对领域不敏感——非常便宜。

PDF 本地副本：scratchpad/oscar.pdf（如需图表原图可再取）。
