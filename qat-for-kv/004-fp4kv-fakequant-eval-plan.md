# Plan：fake-quant 模拟 NVFP4 KV cache，测全链路 NVFP4 在 RULER / GPQA 上的损失

- **日期：** 2026-07-04
- **目标：** 不写 FP4 attention kernel，用 Tony 式假量化（quantize→dequantize→BF16 存储）在 SGLang 里模拟 NVFP4 KV cache 的精度损失，量出 **全链路 NVFP4（w4a4 linear + NVFP4-KV）** 相对 BF16 的真实掉点，为 Phase 1（KV-QAT）提供基线和设计空间地图。
- **产出：** 结果写入 evaluation-log 新编号 md（014），含 A/D/E 三臂损失表 + 与 Tony 数字的交叉对照（实现验证）+ 是否值得上 QAT 的判断（决策门见 §6）。

## 1. 实验设计：三臂对比（A / D / E）

核心问题一句话：**全链路 NVFP4（w4·a4·kv4）比原版 checkpoint 掉多少、比 NVFP4 checkpoint 的现状跑法多掉多少。**
三臂哲学：A 和 E 是两个"不做任何人为改动"的现状端点（A = 原版 checkpoint 原样，E = NVFP4 checkpoint 原样），D 是唯一的实验操作（E 的基础上把 KV 假量化到 NVFP4）。

| 臂 | linear 权重/激活 | KV **数值精度**（等效格式） | KV **物理存储** | 目的 |
| --- | --- | --- | --- | --- |
| A（基线） | **原版 checkpoint 原样跑法**（原生什么精度就什么精度；本 plan 的 Qwen 系原版均为 BF16） | 原版原样（Qwen 系 = BF16，无任何量化） | `torch.bfloat16`（已核实：BF16 checkpoint 在 sglang auto 下即为此，三份 serve 日志为证） | 上限：原版模型的现状 |
| **D（全链路）** | **NVFP4**（w4a4：权重 E2M1 + 1×16 块 E4M3 scale + F32 全局 scale；激活动态量化 FP4） | **NVFP4**（fake-quant：值 E2M1 + 1×16 块 E4M3 scale + F32 per-tensor 全局 scale 两级，与 TRT-LLM 口径一致） | `torch.bfloat16`（quantize→dequantize 后写入；显式 `--kv-cache-dtype bfloat16`，保证 KV 误差 100% 来自 NVFP4 假量化、不叠加其他量化路径） | ★ 主角：w4·a4·kv4 |
| E（参照） | **NVFP4**（w4a4/该 checkpoint 的原生量化配置） | **checkpoint 原样跑法**（`--kv-cache-dtype auto`，checkpoint 带什么就用什么——逐模型见 §2 表） | 随 checkpoint：8B/30B = `torch.float8_e4m3fn`（原生 k/v_scale）；4B = `torch.bfloat16` | 生产现状参照：现有 NVFP4 checkpoint 今天怎么跑就怎么跑 |

读数方式：**D−A** = 全链路总损失；**E−A** = 生产现状的现有损失（8B/30B 可与 005/013 交叉核对）；**D−E** = 在生产现状之上再把 KV 压到 FP4 的边际代价（Phase 1 QAT 要收复的目标）。注意 D−E 的具体语义随 checkpoint 不同：8B/30B 是 "FP4-KV vs FP8-KV"，4B 是 "FP4-KV vs BF16-KV"（= 纯 KV 量化代价），解读时分开说。

## 2. 模型与规模

本 plan 只覆盖 **Qwen 系三个模型**（checkpoint 均已在 model-and-data）；**DeepSeek-V4-Flash 因架构与口径特殊（MLA latent KV、稀疏 indexer、FP8 原生、NVFP4 版仅 experts 量化）单独立项**，见 [005-deepseek-v4-flash-fp4kv-plan.md](005-deepseek-v4-flash-fp4kv-plan.md)。

| 模型 | 量化版 checkpoint | 可跑的臂 | 说明 |
| --- | --- | --- | --- |
| **Qwen3-8B**（主力，先跑） | nvidia NVFP4（w4a4 + FP8 KV scale） | **A / D / E 全** | Tony 对照数字就是 8B；dense 对量化敏感，信号大 |
| **Qwen3-30B-A3B** | nvidia NVFP4（w4a4 + FP8 KV scale） | **A / D / E 全** | 验证 MoE 是否对 KV 量化同样鲁棒（w4a4 只掉 2–4 分） |
| **Qwen3-4B** | llmat NVFP4（w4a4，**无 KV scale**，见 001 §3.1） | **A / D / E 全**（E = 原样跑法 = BF16 KV，因为该 checkpoint 本来就没做 KV 量化） | 社区 checkpoint，compressed-tensors 格式（sglang 需确认加载路径）；其 D−E 即纯 KV-FP4 代价 |

**执行顺序**：8B（全臂，含 Tony 交叉验证）→ 4B → 30B。

## 3. fake-quant 实现方案

### 3.1 量化数学（对齐 TRT-LLM / ModelOpt 口径）

对**写入 cache 前的 K/V**（sglang 缓存的是 post-RoPE 的 K，与 TRT 一致）沿 head_dim 按 **1×16 块**：

```
2-level（TRT 口径）：
  s_global = amax_tensor / (448 × 6)          # FP32，per-tensor（静态标定或首轮动态）
  s_block  = cast_fp8_e4m3(amax_block / (6 × s_global))   # 模拟 E4M3 scale 的表示损失
  x̂ = round_to_e2m1(x / (s_block × s_global)) # E2M1 网格 {0,±.5,±1,±1.5,±2,±3,±4,±6}... 取最近值
  x' = x̂ × s_block × s_global                 # 反量化回 BF16 存储
1-level（消融）：s_block 直接用 FP32 amax_block/6，不过 E4M3。
```

关键：**E4M3 scale 的表示损失必须模拟**（Sage3 §4.1 和 Tony 的 1-level vs 2-level 差异都说明 scale 精度是主要变量之一）。

**s_global（per-tensor FP32 全局 scale）的来源**——块 scale 永远动态算、不需要标定；全局 scale 必须静态（真实 FP4 cache 中所有已写入块共享同一全局 scale，不可能回头重缩放旧条目，逐 token 动态会失真模拟语义），按 checkpoint 分三种情况：

| checkpoint | s_global 来源 | 说明 |
| --- | --- | --- |
| 8B / 30B（nvidia，带 FP8 k/v_scale） | **从现有 FP8 scale 推导，免标定**：FP8 静态量化定义 `k_scale = amax_calib/448` → `amax_calib = k_scale × 448` → `s_global = amax_calib/(448×6) = k_scale/6` | 与 nvidia 标定口径一致，且正是 Tony "same calibrated FP32 scales" 的做法——与他的对照数字同口径 |
| 4B（llmat，无任何 KV scale） | **需自行标定一次**：小批校准数据（几十条长 prompt 即可）hook 记录每层 post-RoPE K / V 的 amax → 存 per-tensor s_global | one-time，半天内 |
| （DeepSeek → 005） | 同 4B 需自标定（对象是 latent 的 amax） | — |

**风险备忘（只记录，暂不实现对应实验）**：nvidia 的 k_scale 标定集偏短文本，32K 长上下文下 K 的 outlier 分布可能更宽 → 推导的 s_global 偏小会引入截断误差。本轮**不加**"重标定 s_global"消融臂；仅在 D0 校准检查时顺手打印"推导 scale vs 长文本实测 amax"的比值留档，若后续 D 臂在 32K 出现异常大的掉点，此处是第一嫌疑（届时再决定是否立项重标定实验）。

### 3.2 挂载点（SGLang）与代码改动方式

- **代码改动纪律（用户要求）**：sglang 源码 clone 到 **`/home/c3-debug/workspace/low-precision-project/sglang/`**（checkout 与现 venv 一致的 `v0.5.9` tag），`pip install -e python/` 装进 eval venv 替换 wheel 版；**所有 fake-quant 改动直接写在这个源码 checkout 里，`git diff` 随时可审**。禁止直改 venv site-packages。
- 位置：`python/sglang/srt/mem_cache/memory_pool.py` 的 `set_kv_buffer`（所有 KV 写入的唯一汇聚点，prefill/decode 都走这里），量化后直接覆盖写入的 BF16 tensor。
- 开关：环境变量 `KV_FAKEQUANT={off|nvfp4_2level|nvfp4_1level}` + `KV_FAKEQUANT_BLOCK=16` + `KV_FAKEQUANT_SCALE_FILE`（4B 标定产物），不改任何 CLI 参数。
- 先用 torch 实现（eval 场景吞吐损失可接受），有需要再写 Triton。
- editable 安装完成后先跑一次 A 臂冒烟对照 wheel 版结果，确认 editable 切换本身零影响，再动代码。
- 注意 KV 存储 dtype 控制：NVFP4 checkpoint 在 sglang 下默认 auto→FP8 KV，**D 臂必须显式 `--kv-cache-dtype bfloat16`**（fake-quant 误差必须是唯一 KV 误差源），E 臂用 auto（FP8）。启动后从 server args 日志逐臂核实。

### 3.3 正确性验证（跑分前必做）

1. 单元测试：对已知 tensor 手算 NVFP4 量化误差，对照 kernel 输出（相对误差分布、块内 amax 保序）。
2. 冒烟：8B BF16+fakeKV 生成 5 条 completion，肉眼检查连贯性 + finish_reason（照 008 教训：简单题不暴露问题，加 2 道难题看长思维链是否失控）。
3. **与 Tony 数字交叉对照**（外部 ground truth，只作实现验证、不进结果矩阵）：跑一次轻量诊断——BF16 权重 + fake-NVFP4 KV，只测 ruler_vt / niah_mk2 @32K，对照 Tony 的 −0.52pp / −5.00pp。方向/量级对上才开跑正式三臂；对不上先查实现。

## 4. 评测配置

### 4.1 GPQA-Diamond（thinking，走 turboeval 托管服务）

- 沿用 005/006 全套协议：`temperature=0.6, top_p=0.95, top_k=20, min_p=0`（server-side pin）、`max_tokens=32768`、无 reasoning-parser、turbogate 暴露、smoke-5 先行。
- 三臂各 3 runs 取均值（GPQA 单次方差 ±3pp，3-run 是可比性底线）。

### 4.2 RULER（非 thinking，走 turboeval，004 协议）

- 13 任务 × 50 例，`--max-length` 分 **8192 / 32768** 两档（32K 档是 KV 误差的放大镜，与 Tony 口径一致），`temperature=0.7, top_p=0.8`，`max_tokens=2048`，tokenizer Qwen/Qwen3-8B。
- 重点子任务：`ruler_vt`、`niah_multikey_2`（历史上最敏感的两个，004/Tony 都用它们）；13 任务全跑但解读以这两个 + 均值为主。

### 4.3 辅助指标（本地、便宜）

- 每臂顺手跑一次 K3（复用 `k3_cross_model_vllm.py` 的 sglang 版思路或直接 52-prompt 采样对照 A 臂）——K3 与下游掉点的相关性我们在 007/011 已验证，可以作为快速预警。

## 5. 执行顺序

1. **D0：实现 + 验证**（§3，半天到一天；含 Tony 对照诊断）
2. **D1 上午：8B 三臂 GPQA**（各 smoke-5 + 3-run）
3. **D1 下午：8B 三臂 RULER**（8K + 32K 两档）
4. **D2：4B 三臂 + 30B 三臂**（GPQA + RULER，同协议；4B 需先确认 compressed-tensors 在 sglang 的加载）+ 结果整理成 014 md
5. 全程遵守：双引擎/假量化对照的验证纪律（008 教训）；GPU 用空闲卡，不碰他人实验；精确 PID 清理。

## 6. 预期结果与决策门

| 观察 | 含义 / 下一步 |
| --- | --- |
| 诊断跑 @32K RULER 掉 3–6pp（对齐 Tony） | 实现正确 → 开跑正式三臂 |
| D−E 小（≤1–2pp） | FP4-KV 的边际代价低，方向的推理端阻力小 → 重心放 kernel（Phase 2） |
| D−E 大（≥3pp，尤其 32K RULER） | KV 再降 4-bit 的损失显著 → Phase 1 KV-QAT 必要且收益明确 |
| GPQA（短上下文为主）掉点很小、RULER 32K 掉点大 | 主战场在长上下文 → Phase 1 的训练/评测重心放 RULER 32K–128K |

## 7. 风险与对策

- **sglang patch 影响非预期路径**（如 radix cache 复用旧 KV）：验证时用 `--disable-radix-cache` 跑一组对照，确认无差异后再开启。
- **8B NVFP4 需要显式 `--quantization modelopt_fp4`**（008 记录的坑）；D 臂 KV dtype 必须显式 bf16。
- **turboeval 并发限额**（6 running jobs/账号）：与 veomni 等他人 job 错峰，或分批提交。
- 任何反直觉结果（例如 fake-quant 比 BF16 还高分）→ 先跑 5 题人工检查输出，再查实现，不直接采信。
