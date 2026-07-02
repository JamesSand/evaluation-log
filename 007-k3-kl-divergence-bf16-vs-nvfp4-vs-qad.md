# 007 — K3 KL 散度：Qwen3-8B BF16 vs NVFP4 vs QAD-w4a4-kv8-step500（SGLang，跨模型）

> **⚠️ 更正（2026-07-02，见 [008](008-qad-s500-vllm-repro-sglang-serving-bug-confirmed.md)）：** 本文所有
> 模型都用 SGLang 0.5.9 提供服务，而该版本后来被证实对这份 QAD 导出的 serving 是**错误的**
> （失控/退化解码；vLLM 加载完全相同的权重不会出现此现象）。因此 QAD 各行（k3 0.19/0.25
> 以及"退化特征"的解读）度量的是 *SGLang 对 QAD 的错误 serving*，而不是 checkpoint 本身。
> BF16 sanity 行和 NVFP4 各行（~0.047）仍然有效（引擎内行为一致）。要得到该 checkpoint
> 的真实 divergence，还需要用 vLLM 服务 QAD 再跑一次 K3。

- **日期：** 2026-07-02
- **机器 / 软件栈：** 与 005/006 相同 — 4x B200，SGLang 0.5.9（`/home/c3-debug/venvs/sglang`），GPU 0/1/2 上各起一个 server（BF16 :30000，NVFP4 :30001 `--quantization modelopt_fp4` `--mem-fraction-static 0.75`，QAD step500 :30002 同样的 quant flag + 本地 tokenizer 修复，跑完后已还原）。
- **本实验度量什么：** xorl 的 K3 gate（`turbo-skills/skills/xorl/SKILL.md:104`、`xorl-internal/experiments/k3_tests/compare_logprobs.py`）检查的是*同一份权重、两个引擎*，gate 为 **bf16 KL ≤ 0.01**。这里我们把完全相同的机制/公式复用到*同一引擎、不同权重*的场景——此时 K3 量化的是**量化/蒸馏对 token 分布造成的损伤**，数值预期会超过 0.01 的 gate；gate 线只作参考，不作 pass/fail 判定。
- **Harness：** [`k3_cross_model.py`](k3_cross_model.py)（本目录有副本）— 完全照搬 repo 的机制：从参考模型经 `/generate` 做 greedy（temp 0）采样（`input_ids`、`return_logprob`、`logprob_start_len=-1`），再把完全相同的 token 序列通过 prefill 强制喂入(teacher-force)目标模型（`max_new_tokens=0`），取 `input_token_logprobs[-gen_len:]`。
  - 公式（逐 token）：`k1 = logp_ref − logp_target`；`k3 = exp(logq−logp) − (logq−logp) − 1`（Schulman 估计量，取 KL(p‖q) 的无偏方向；repo 的字面方向也在 JSON 里以 `k3_repo` 一并给出）。
  - 数据：52 条 prompt = 32 条 GPQA-Diamond（simple-evals 模板，thinking 模式）+ 20 条通用/数学/代码；`max_new_tokens=1024`；每对模型约 53k 个生成 token。52 条里有 49–51 条触到 1024 上限（greedy 的 thinking 轨迹很长）——所有数字都是沿（截断的）greedy 轨迹统计的，与 repo gate 的约定一致。
  - 原始统计：[`k3_cross_model_results.json`](k3_cross_model_results.json)（本目录副本；含 gpqa/generic 拆分、逐 prompt 的 k3，以及 repo 字面方向的 `k3_repo`）。

## 结果（逐 token，每行 n ≈ 53k tokens）

| 采样方 → 被打分方 | k3 均值 | k1 均值 | k3 中位数 | k3 p99 | 相对 0.01 gate |
| --- | --- | --- | --- | --- | --- |
| **bf16 → bf16**（sanity：采样路径 vs prefill 路径，同一 server） | **0.00031** | 0.00057 | 1.1e-9 | 0.006 | ✅ 通过 — harness 噪声底 |
| **bf16 → NVFP4** | **0.0466** | 0.098 | 5.7e-5 | 0.93 | 超出约 5 倍 |
| **bf16 → QAD s500** | **0.1928** | 0.330 | 8.0e-4 | 3.31 | 超出约 19 倍 |
| **QAD s500 → bf16**（反向） | **0.2462** | 0.357 | 1.5e-4 | 4.66 | 超出约 25 倍 |
| **NVFP4 → bf16**（反向） | **0.0470** | 0.082 | 7.3e-5 | 0.91 | 超出约 5 倍 |

GPQA 与 generic 两个拆分在每一行内部结果一致（例如 bf16→qad：gpqa 0.177 / generic 0.219）；完整拆分和逐 prompt 统计见 JSON。

## 发现

1. **Sanity 行验证了 harness 本身**：同一模型，采样路径 vs prefill 路径，k3 = 3.1e-4——远低于 0.01 的引擎一致性 gate；因此其上的所有数值都是真实的模型 divergence，不是工具链噪声。
2. **NVFP4（纯 PTQ）：k3 ≈ 0.047，且对称**（正向 0.0466 / 反向 0.0470）。逐 token 中位数极小（5.7e-5）——绝大多数 token 几乎完全一致——但重尾（p95 ≈ 0.21，p99 ≈ 0.93）把均值撑了起来：量化噪声偶尔会剧烈翻转 token 偏好。逐 prompt 的分布范围为 0.023–0.074。
3. **QAD step500：k3 ≈ 0.19–0.25，比 NVFP4 差 4–5 倍，且不对称**（反向 > 正向）。这种不对称正是退化的特征：在 QAD 自己的 greedy 轨迹上，它会走进 BF16 认为概率极低的区域（p99 = 4.66）——与 006 记录的"重复→乱码"崩溃一致。注意 QAD 是*蒸馏*出来的（权重不同），所以它的 K3 里包含了有意为之的训练漂移，不只是量化噪声；但从量级、不对称性，再加上 006 中低于随机的 GPQA 来看，指向的是劣化而非良性漂移。
4. **按 RL gate 的读法**：按 xorl 的约定，这些量化 checkpoint 没有一个能作为 rollout 引擎搭配 bf16 trainer 通过 gate（全部 ≥ 0.01 gate 的 5 倍）。NVFP4 最接近；QAD step500 差得很远。
5. 排序与 GPQA 质量阶梯（006）一致：BF16 0.579 > NVFP4 0.493（k3 0.047）> QAD 按打分约 0.15/已退化（k3 0.19–0.25）。在这里 K3 对下游损伤的追踪相当到位。

## 运维备注

- 用 `logprob_start_len: 0` 打分且并发 16 时会把 server **打到 OOM**（全位置 logits：vocab 151936 下每个 batch 约 9 GiB）——曾把 NVFP4 server 干掉一次。修复方法：`logprob_start_len = prompt_len − 1`（只取生成位置的 logits）+ 打分并发降到 4 + FP4 server 加 `--mem-fraction-static 0.75`。今后任何在这个 vocab 规模上使用 `input_token_logprobs` 的场景都值得记住这一点。
- 量化各行 k1 ≈ 2× k3：符合预期——token 来自 *greedy* 轨迹（repo 约定），并非从 p 采样，因此两个估计量都不是严格意义上的分布 KL；把它们当作沿轨迹 token 的 divergence 度量即可（二者在噪声底处一致，对模型的排序也完全相同）。
- 清理已完成：3 个 server 全部停掉，GPU 已释放，QAD 的 tokenizer_config 已还原。
