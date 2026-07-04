# 007 — K3 KL 散度：Qwen3-8B BF16 vs NVFP4 vs QAD-w4a4-kv8-step500（vLLM，跨模型）

- **日期：** 2026-07-02
- **机器 / 软件栈：** 4x B200（sm100），**vLLM 0.24.0**（`/home/c3-debug/venvs/vllm`，torch 2.11.0+cu130，flashinfer 0.6.12）。BF16 在 GPU1:8001；QAD step500 与 NVFP4 先后在 GPU0:8000（两者均 `--kv-cache-dtype fp8`，按 checkpoint 的 `hf_quant_config.json` 配置；QAD 需本地 tokenizer 修复，跑完已还原）。
- **背景：** 最初用 SGLang 0.5.9 测过一版，后来证实该版本对这份 QAD 导出的 serving 有缺陷（失控/退化解码，见 [008](008-qad-s500-vllm-repro-sglang-serving-bug-confirmed.md)），QAD 相关数值被污染而作废；本文只保留 **纯 vLLM 栈的有效测量**。sglang 侧的 NVFP4/sanity 数值与 vLLM 吻合（跨引擎一致），其 harness 与原始数据仍保留在本目录（[`k3_cross_model.py`](k3_cross_model.py) / [`k3_cross_model_results.json`](k3_cross_model_results.json)）供查证。
- **本实验度量什么：** xorl 的 K3 gate（`turbo-skills/skills/xorl/SKILL.md:104`、`xorl-internal/experiments/k3_tests/compare_logprobs.py`）检查的是*同一份权重、两个引擎*，gate 为 **bf16 KL ≤ 0.01**。这里我们把完全相同的公式复用到*同一引擎、不同权重*的场景——此时 K3 量化的是**量化/蒸馏对 token 分布造成的损伤**，数值预期会超过 0.01 的 gate；gate 线只作参考，不作 pass/fail 判定。
- **Harness：** [`k3_cross_model_vllm.py`](k3_cross_model_vllm.py)（本目录有副本）——从参考模型 greedy（temp 0）采样，再把完全相同的 token 序列强制喂入(teacher-force)目标模型取逐 token logprob。vLLM 接口：采样用 `/v1/completions` + `return_tokens_as_token_ids`，打分用 `prompt_logprobs=0`。
  - 公式（逐 token）：`k1 = logp_ref − logp_target`；`k3 = exp(logq−logp) − (logq−logp) − 1`（Schulman 估计量，取 KL(p‖q) 的无偏方向；repo 的字面方向也在 JSON 里以 `k3_repo` 一并给出）。
  - 数据：52 条 prompt = 32 条 GPQA-Diamond（simple-evals 模板，thinking 模式）+ 20 条通用/数学/代码；`max_new_tokens=1024`；每对模型约 53k 个生成 token。52 条里有 51 条触到 1024 上限（greedy 的 thinking 轨迹很长）——所有数字都是沿（截断的）greedy 轨迹统计的，与 repo gate 的约定一致。
  - 原始统计：[`k3_vllm_results.json`](k3_vllm_results.json)（含 gpqa/generic 拆分与 `k3_repo` 方向）。

## 结果（逐 token，每行 n ≈ 53k tokens）

| 采样方 → 被打分方 | k3 均值 | k1 均值 | k3 中位数 | k3 p99 | 相对 0.01 gate |
| --- | --- | --- | --- | --- | --- |
| **bf16 → bf16**（sanity：采样路径 vs prefill 路径，同一 server） | **0.00029** | 0.00063 | 8.0e-10 | 0.006 | ✅ 通过 — harness 噪声底 |
| **bf16 → NVFP4** | **0.0450** | 0.0999 | 7.5e-5 | 0.86 | 超出约 4–5 倍 |
| **NVFP4 → bf16**（反向） | **0.0489** | 0.0810 | 7.0e-5 | 0.88 | 超出约 5 倍 |
| **bf16 → QAD s500** | **0.0312** | 0.0819 | 7.5e-5 | 0.64 | 超出约 3 倍 |
| **QAD s500 → bf16**（反向） | **0.0334** | 0.0454 | 7.1e-5 | 0.61 | 超出约 3 倍 |

GPQA 与 generic 两个拆分在每一行内部结果一致；完整拆分见 JSON。

## 发现

1. **Sanity 行验证了 harness 本身**：同一模型，采样路径 vs prefill 路径，k3 = 2.9e-4——远低于 0.01 的引擎一致性 gate；因此其上的所有数值都是真实的模型 divergence，不是工具链噪声。
2. **NVFP4（纯 PTQ）：k3 ≈ 0.045–0.049，双向对称**。逐 token 中位数极小（~7e-5）——绝大多数 token 几乎完全一致——但重尾（p99 ≈ 0.86）把均值撑了起来：量化噪声偶尔会剧烈翻转 token 偏好。
3. **QAD step500：k3 ≈ 0.031–0.033，双向对称，甚至低于 NVFP4**——量化感知蒸馏(QAD)确实把 token 分布拉得比纯 PTQ 更贴近 BF16 teacher。这与 GPQA 质量完全自洽：QAD 0.5808 ≈ BF16 0.579 > NVFP4 0.493（见 006/008）。
4. **K3 排序（QAD < NVFP4）正确预测了 GPQA 排序（QAD > NVFP4）**——K3 可以作为量化损伤的代理指标，前提是 serving 引擎本身可信（这正是 008 的教训：换第二个引擎交叉验证之前，不要把异常归因给 checkpoint）。
5. **按 RL gate 的读法**：三个量化 checkpoint 的 K3 都在 0.01 gate 的 3–5 倍——直接当 bf16 trainer 的 rollout 引擎都不达标，QAD 最接近 gate。
6. 量化各行 k1 ≈ 2× k3：符合预期——token 来自 *greedy* 轨迹（repo 约定），并非从 p 采样，因此两个估计量都不是严格意义上的分布 KL；把它们当作沿轨迹 token 的 divergence 度量即可（二者在噪声底处一致，对模型的排序也完全相同）。

## 运维备注

- vLLM 的打分路径用 `prompt_logprobs=0`（只物化被选 token 的 logprob），无 OOM 风险；早前 sglang 版 harness 用 `logprob_start_len: 0` 全位置取 logprob 曾把 server 打到 OOM（vocab 151936 下每 batch ~9 GiB），修复是改为 `logprob_start_len = prompt_len − 1` + 降打分并发。
- vLLM 0.24.0 加载这两份 FP4 checkpoint 的 KV scale 会触发 `KVCacheScaleParameter.weight_loader()` 参数个数 bug，venv 内已打补丁（详见 [008](008-qad-s500-vllm-repro-sglang-serving-bug-confirmed.md)）。
- 清理已完成：server 全部停掉，GPU 已释放，QAD 的 tokenizer_config 已还原。
