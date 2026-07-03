# 010 — QAD w4a4-kv8 step0–500 checkpoint sweep：K3 KL vs 训练步数曲线

- **日期：** 2026-07-03
- **目标：** 对 `togethercomputer/Qwen3-8B-modelopt-qad-w4a4-kv8-exported` 下 step50–step500（每 50 步一档）加上蒸馏前的 step0 student（`…-qad-quantized-student-w4a4-kvfp8-exported`），共 11 个 checkpoint，逐一测 K3 KL（相对 BF16 teacher），画出 KL 随训练步数的曲线。
- **机器 / 软件栈：** 与 007/008 相同——4x B200，**vLLM 0.24.0**（venv 含 `KVCacheScaleParameter` 补丁，见 008）。BF16 参考常驻 GPU1:8001；各 checkpoint 在 GPU0:8010 / GPU2:8011 双通道轮流服务（`--kv-cache-dtype fp8`，每个 checkpoint 的 tokenizer 修复用后即还原，精确 PID 清理）。
- **方法：** 与 007 完全一致（[`k3_cross_model_vllm.py`](k3_cross_model_vllm.py) + sweep 驱动 [`k3_step_sweep.py`](k3_step_sweep.py)）。正向（BF16‖ckpt）对所有 checkpoint 复用**同一份固定的 BF16 greedy 样本**（52 prompt × ≤1024 token ≈ 53k token），保证逐点可比；反向（ckpt‖BF16）从各 checkpoint 现采样。
- **原始数据：** [`k3_sweep_results.json`](k3_sweep_results.json)（含每点 k1/k3 的 mean/median/p95/p99 与 gpqa/generic 拆分）。
- **可复现性自证：** step500 的 sweep 测量（0.0312/0.0335）与 007 的独立测量（0.0312/0.0334）完全一致。

## 曲线

![K3 KL vs QAD training step](k3-vs-step-qad-w4a4.png)

## 数据

| step | fwd K3 = KL(BF16‖ckpt) | rev K3 = KL(ckpt‖BF16) |
| --- | --- | --- |
| **0**（蒸馏前 PTQ student，`…-qad-quantized-student-w4a4-kvfp8-exported`） | **0.04468** | **0.04743** |
| 50 | 0.03244 | 0.03702 |
| 100 | 0.03159 | 0.03646 |
| 150 | 0.03187 | 0.03560 |
| 200 | 0.03123 | 0.03378 |
| 250 | 0.03152 | 0.03560 |
| 300 | 0.03079 | 0.03439 |
| 350 | 0.03060 | 0.03462 |
| 400 | 0.03084 | 0.03595 |
| 450 | 0.02972 | 0.03573 |
| 500 | 0.03124 | 0.03354 |

参考线：NVFP4 纯 PTQ 同口径 K3 = 0.045–0.049（007）；xorl 引擎一致性 gate = 0.01；harness 噪声底 ≈ 0.0003。

## 发现

1. **step0 直接验证了 PTQ 起点**：蒸馏前的量化 student 实测 K3 = 0.0447/0.0474，恰好落在 nvidia 官方
   NVFP4 PTQ 的参考带（0.045–0.049）内——同为 PTQ、K3 相同，符合预期，也再次交叉验证了 harness。
2. **曲线是"一步下台阶 + 长平台"：** 从 step0 的 ≈0.045 到 step50 的 ≈0.032，**~90% 的分布收敛发生在
   最初 50 步内**；step50→500 正向仅再降 −0.0012（约 −4%），反向 0.034–0.037 缓降。
3. **正向有微弱但一致的下行趋势**（0.0324→0.0297@450，线性看每 100 步约 −0.0005）；step500 略回升到
   0.0312，与逐点噪声（±0.0005 量级）同阶，不宜过度解读单点。反向更嘈杂（±0.001），但均值也从
   0.0365（前 150 步）降到 0.0346（后 350 步）。
4. **两个方向全程保持对称**（rev/fwd ≈ 1.1），没有任何一个 checkpoint 出现 007-sglang 时代那种伪
   "退化不对称"——再次佐证所有 step 的 export 在 vLLM 下 serving 正常。
5. **对训练的含义：** 以 K3 论，step50 之后继续蒸馏的收益已经很小；结合 004（step500 GPQA 0.5808 追平
   bf16），如果想省算力，值得回头用 GPQA 等下游指标测几个早期 checkpoint（如 step100/200），确认下游
   质量是否也已在早期饱和——K3 平不代表下游能力一定平（K3 测的是分布贴近度，不是能力）。
6. 含 step0 共 11 个 checkpoint 的服务与测量零失败（20 次 vLLM 起停，双通道 ~55 分钟走完）。

## 运维备注

- 双通道 sweep 驱动：[`sweep_lane.sh`](sweep_lane.sh)——每 checkpoint 起服务→测→按精确 PID 杀，
  fp4_gemm autotune 因 `FLASHINFER_WORKSPACE_BASE` 缓存命中，每个 server 起动仅约 4–6 分钟。
- 下载注意：一次 `hf download` 带 9 个 `--include` pattern 时 step50 被静默漏掉（64/72 文件），
  单独补一次即可；下载后按 `model.safetensors` + `hf_quant_config.json` 逐目录校验。
- 清理：全部 server 已停，11 个 checkpoint 的 tokenizer_config 均已还原，GPU 已释放。
