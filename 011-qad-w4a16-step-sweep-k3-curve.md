# 011 — QAD w4a16-kv16 step0–500 checkpoint sweep：K3 KL 曲线（+ 与 w4a4 家族对比）

- **日期：** 2026-07-03
- **目标：** 对 w4a16-kv16 QAD 家族复刻 [010](010-qad-w4a4-step-sweep-k3-curve.md) 的 sweep：step0（蒸馏前 student，`togethercomputer/Qwen3-8B-modelopt-qad-quantized-student-w4a16-kv16-exported`）+ step50–500（`togethercomputer/Qwen3-8B-modelopt-qad-w4a16-kv16-exported`，每 50 步一档），共 11 个 checkpoint，测 K3 KL（相对 BF16 teacher）并画曲线。
- **机器 / 软件栈：** 与 010 相同（vLLM 0.24.0）。区别：本家族 `quant_algo=W4A16_NVFP4`、**无 KV 量化**，serving 不加 `--kv-cache-dtype fp8`（用默认 bf16 KV）。BF16 参考在 GPU3:8001；checkpoint 单通道 GPU0:8010 逐个服务（GPU1/2 当时被其他实验占用，未动）。
- **方法 / harness：** 与 010 完全一致（同一份固定 BF16 greedy 样本做正向、各 checkpoint 现采样做反向，~53k token/方向）。原始数据：[`k3_sweep_w4a16.json`](k3_sweep_w4a16.json)。

## 曲线

w4a16 家族：

![K3 KL vs QAD training step (w4a16)](k3-vs-step-qad-w4a16.png)

两个家族对比（w4a4 数据来自 010）：

![QAD families compared](k3-vs-step-qad-families.png)

## 数据

| step | fwd K3 = KL(BF16‖ckpt) | rev K3 = KL(ckpt‖BF16) |
| --- | --- | --- |
| **0**（蒸馏前 PTQ student） | **0.02193** | **0.02261** |
| 50 | 0.01596 | 0.01851 |
| 100 | 0.01594 | 0.01765 |
| 150 | 0.01613 | 0.01718 |
| 200 | 0.01572 | 0.01596 |
| 250 | 0.01574 | 0.01598 |
| 300 | 0.01522 | 0.01598 |
| 350 | 0.01506 | 0.01576 |
| 400 | 0.01478 | 0.01635 |
| 450 | 0.01451 | 0.01517 |
| 500 | 0.01451 | 0.01475 |

参考：w4a4 家族（010）step0 = 0.0447/0.0474、平台 ≈ 0.031/0.034；xorl 引擎一致性 gate = 0.01；harness 噪声底 ≈ 0.0003。

## ⚠️ 重要口径说明：两家族测的是不同的推理模式（2026-07-03 补）

- **w4a4 家族**是真 NVFP4 w4a4 推理：vLLM `quant_algo=NVFP4` + fp4_gemm kernel，激活按 checkpoint 自带的
  `input_scale`（QAD 学到的）动态量化到 FP4，KV FP8。
- **w4a16 家族**是 weight-only 推理：vLLM `W4A16_NVFP4` 走 Marlin kernel，激活全程 BF16；且该 export
  **没有 `input_scale`/KV scale**，物理上无法直接按 w4a4 服务。
- 因此本文对比是"训练配方 × 推理模式"的混合对比。"w4a16 KL 减半"是关于**推理模式**的正确陈述；
  但若 w4a16 student 的部署意图是最终跑 w4a4 推理（如 009 中 VeOmni-w4 的事后校准路线），本文曲线是
  部署 KL 的**下界**。公平的同部署模式配方对比需要先给 w4a16 checkpoint 事后校准激活 scale
  （ModelOpt/tore-quant），再按 w4a4 服务重测。


## 发现

1. **w4a16 的 PTQ 起点就只有 w4a4 的一半**：step0 = 0.0219/0.0226 vs w4a4 的 0.0447/0.0474。只量化权重
   （激活留 16-bit、KV 留 bf16）带来的分布损伤大约减半——量化损伤主要来自激活 4-bit 这一半。
2. **曲线形状与 w4a4 完全同构：一步下台阶 + 长平台。** step0→step50 完成大部分收敛（0.0219→0.0160，
   ~70%），此后 450 步缓降到 0.0145（每 100 步约 −0.0003）。QAD 的"前 50 步定乾坤"规律跨配方成立。
3. **平台高度：w4a16 ≈ 0.0145–0.0160，约为 w4a4 平台（0.030–0.032）的一半。** 两家族蒸馏后的 KL 比值
   与蒸馏前的 PTQ 起点比值几乎相同（≈2×）——QAD 在两种配方上的相对收益一致（都压掉起点的 ~30%），
   剩余 KL 由量化配方本身决定。
4. **双向对称性良好且随训练收紧**：rev/fwd 从 step50 的 1.16 收敛到 step500 的 1.02，无任何退化迹象。
5. **对 RL gate 的含义**：w4a16 step500 的 K3 = 0.0145，距 0.01 的引擎一致性 gate 只差 45%——是目前
   所有量化 checkpoint 里最接近"可当 rollout 引擎"的；w4a4 平台（0.031）还差 3 倍。
6. 11 个 checkpoint 零失败（单通道 ~75 分钟）。GPU1/2 上他人的 sglang 实验全程未受影响。

## 运维备注

- 复用 010 的 sweep 框架，仅参数化了模型目录（`SWEEP_BASE`）、KV 参数（`SWEEP_KV_ARGS`，本家族置空）和
  输出文件（`K3_SWEEP_OUT`）；step0 用符号链接并入统一目录结构，跑完已移除。
- 本家族 export 的 `tokenizer_config.json` 同样带坏的 `extra_special_tokens` 字段（list），同样按
  006/008 的方式本地修复、用后还原。
- 起跑时 GPU1 被其他实验占用导致 BF16 首次启动失败（Free memory 23GiB < 需求），改用 GPU3 后正常——
  多人共用机器时先 `nvidia-smi` 查占用再选卡。
