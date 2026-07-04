# 005 — Qwen3-30B-A3B：BF16 对比 NVFP4（nvidia），GPQA-Diamond，经 SGLang + turbogate + turboeval

- **日期：** 2026-07-02
- **机器：** 4x NVIDIA B200（sm100），驱动 575.57.08
- **Serving 栈：** `/home/c3-debug/venvs/sglang` — SGLang **0.5.9**，torch 2.9.1+cu128，flashinfer（自带），Python 3.12
- **评测服务：** 托管版 turboeval（`eval.together-turbo.com`），通过本地 `turboeval` CLI 提交（`pip install -e /home/c3-debug/workspace/low-precision-project/turboeval`）
- **对外暴露：** turbogate（带鉴权，`@together.ai` key），从 `/home/c3-debug/workspace/low-precision-project/turbogate` 构建（`turbogate 0.1.0` + frpc）
- **模型（已下载的快照）：**
  - BF16：`/home/c3-debug/workspace/low-precision-project/model-and-data/Qwen3-30B-A3B`（Qwen/Qwen3-30B-A3B，57G，16 个分片）
  - NVFP4：`/home/c3-debug/workspace/low-precision-project/model-and-data/Qwen3-30B-A3B-NVFP4`（nvidia/Qwen3-30B-A3B-NVFP4，17G；ModelOpt `quant_algo=NVFP4`，w4a4 group16，`kv_cache_quant_algo=FP8`，MoE gate 和 lm_head 不做量化）
- **实验设计：** 2 个模型 × 3 次完整 GPQA-Diamond 跑（198 题，thinking 模式）= 6 个 job，两台 server 各占一块独立 GPU，两侧并发执行。

## Serving

两台 server 均为：TP=1，`--context-length 40960`，`--mem-fraction-static 0.85`，`--trust-remote-code`，**不加 `--reasoning-parser`**（这样 `<think>…</think>` 会留在 `content` 里，GPQA 的答案抽取器依赖这一点——已在 smoke 测试中验证）。

BF16（GPU 0，端口 30000）：

```bash
CUDA_VISIBLE_DEVICES=0 python -m sglang.launch_server \
  --model-path /home/c3-debug/workspace/low-precision-project/model-and-data/Qwen3-30B-A3B \
  --served-model-name qwen3-30b-a3b \
  --host 0.0.0.0 --port 30000 --tp 1 \
  --mem-fraction-static 0.85 --context-length 40960 --trust-remote-code \
  --preferred-sampling-params '{"temperature":0.6,"top_p":0.95,"top_k":20,"min_p":0}'
```

NVFP4（GPU 1，端口 30001）：flag 完全相同，只是换成 NVFP4 路径，`--served-model-name qwen3-30b-a3b-nvfp4`。量化配置从 `hf_quant_config.json` **自动识别**（`quantization='modelopt'`，ModelOptModelLoader，"Model is already quantized, loading directly"）；KV cache 按 checkpoint 里的 `kv_cache_quant_algo=FP8` 自动使用 **float8_e4m3fn**；`fp4_gemm_runner_backend=flashinfer_cutlass`，attention 走 `trtllm_mha`。无需显式指定 `--quantization`。

### Thinking 模式与采样——官方参数如何强制生效

Qwen3 官方 thinking 采样参数：`temperature=0.6, top_p=0.95, top_k=20, min_p=0`（非贪心）。

- Thinking 模式：Qwen3-30B-A3B 的 chat template 默认 `enable_thinking=True`；两台 server 的 smoke 补全中，`content` 内都包含 `<think>…</think>` 块，最终答案跟在其后（`reasoning_content` 为空——因为没挂 parser）。
- `temperature`/`top_p` 由 turboeval 逐请求发送（`--option temperature=0.6 --option top_p=0.95`）。
- `top_k=20`/`min_p=0` **不会**经 OpenAI 请求路径透传，因此用 `--preferred-sampling-params '{"temperature":0.6,"top_p":0.95,"top_k":20,"min_p":0}'` 在 **server 侧钉死**。已在两台 server 上通过 `GET /get_server_info` 实测确认 → `preferred_sampling_params: {'temperature': 0.6, 'top_p': 0.95, 'top_k': 20, 'min_p': 0}`（另有 `sampling_defaults: model`，且两个 checkpoint 自身的 `generation_config.json` 也带 temp 0.6 / top_k 20 / top_p 0.95）。

## 对外暴露（turbogate）

```bash
turbogate http 30000 zhizhou-qwen3-bf16    # → https://zhizhou-qwen3-bf16.gate.together-turbo.com
turbogate http 30001 zhizhou-qwen3-nvfp4   # → https://zhizhou-qwen3-nvfp4.gate.together-turbo.com
```

已验证：持 `@together.ai` key 通过 gate 访问 `/v1/models` 和真实的 `/v1/chat/completions` 均正常；无 key 请求返回 401。隧道和 server 在整个跑评期间都有监护。

## 提交

```bash
export TURBOEVAL_TARGET_API_KEY="$TOGETHER_API_KEY"   # key must pass the gate
turboeval create \
  --endpoint https://zhizhou-qwen3-{bf16|nvfp4}.gate.together-turbo.com/v1 \
  --model {qwen3-30b-a3b|qwen3-30b-a3b-nvfp4} \
  --benchmark gpqa_diamond \
  --option num_examples=198 --option concurrency=auto \
  --option temperature=0.6 --option top_p=0.95 --option max_tokens=32768
```

先做 smoke（`num_examples=5`，两个 endpoint 各一次）：5/5 完成，0 报错，答案抽取正常——
BF16 `evl_a8748e70e1c640ceae4c3910b284d016`，NVFP4 `evl_912e80edcf8e41679305acde2f7f0dc8`（在这 5 题子集上 acc 均为 0.6）。

## 结果（完整 gpqa_diamond，198 题，thinking）

6 个 job 全部 `completed`，每个都是 **198/198，0 条报错 trial**。

| model | run | accuracy | completed | errored | job_id |
| --- | --- | --- | --- | --- | --- |
| BF16 | 1 | 0.6414 | 198/198 | 0 | `evl_aa75de32795e4fc4ba5ddf7d6bdd1f6c` |
| BF16 | 2 | 0.5758 | 198/198 | 0 | `evl_b3044bd672bd49f482e13ce373d94bee` |
| BF16 | 3 | 0.6414 | 198/198 | 0 | `evl_aaa847201ced428795ffa07cca639833` |
| NVFP4 | 1 | 0.5960 | 198/198 | 0 | `evl_26b62648c0934bd08a4694657c9b2062` |
| NVFP4 | 2 | 0.5859 | 198/198 | 0 | `evl_b7afb4daf3a04f0781323cd1da8f708d` |
| NVFP4 | 3 | 0.5657 | 198/198 | 0 | `evl_356a1c0fc7cd422db399daba505c9023` |

前端页面：`https://eval.together-turbo.com/runs/<job_id>`。

### 汇总（3 次均值）

| model | GPQA-Diamond（均值±区间） |
| --- | --- |
| **Qwen/Qwen3-30B-A3B (BF16)** | **0.6195**（0.5758–0.6414） |
| **nvidia/Qwen3-30B-A3B-NVFP4 (w4a4, KV FP8)** | **0.5825**（0.5657–0.5960） |
| Δ NVFP4 − BF16 | **−0.0370**（−3.7 个百分点） |

## Caveat

- **可直接对比：** 同一台机器、同一 SGLang 构建、同样的采样参数（含 server 侧钉死的 top_k/min_p）、同样的 benchmark 和评测服务，所有 run 全部完成且 0 报错。唯一有意引入的差异就是 checkpoint（BF16 vs ModelOpt NVFP4 w4a4 + KV FP8）。
- 单次 GPQA 的方差很大（本次 BF16 跨度 0.576–0.641；本次 005 的 BF16 均值 0.6195，对比 run `bc032c0` 的 vLLM 3 次均值 0.628——跨引擎一致）。3 次均值仍带约 ±1–2 个百分点的不确定性；NVFP4 的 −3.7 个百分点差距大于 run 间噪声，但幅度不算大。
- BF16 的 KV cache 是 bf16；NVFP4 的 KV 按其 checkpoint 自带的 `hf_quant_config.json` 为 FP8——这就是 NVIDIA 发布的制品原样（w4a4-kv8），与生产环境中的 serving 方式一致。
- 两台 server 各自的 3 个 job 与另一个模型的 job 并发执行（2 块 GPU，互相独立）；评测服务的 concurrency `auto` 把每台 server 的并发请求推到约 170–210，且没有报错。
- 早期日志的参照点（Qwen3-**8B**，run 001）：bf16 0.5808，naive NVFP4 w4a4 0.5051——30B-A3B 这个 MoE 模型在 NVFP4 下的损失小得多（−3.7 对比 −7.6 个百分点）。

## 运维备注

- SGLang 0.5.9 wheel 安装（`uv pip install "sglang[all]"`）在 B200 sm100 / 驱动 575.57.08 上开箱即用（torch 2.9.1+cu128，CUDA-13 驱动上跑 cu128 wheel）。无需源码编译，无需 `--quantization` flag，NVFP4 也没有 kernel 问题（自动选中 flashinfer_cutlass fp4 GEMM）。
- BF16 启动约 5.5 分钟（95 秒权重加载 + FlashInfer autotune + CUDA-graph capture）；NVFP4 启动更快（35 秒加载），没有出现漫长的 fp4 autotune 卡顿（对比 004 中 vLLM w4a4 约 17 分钟的 warmup）。
- 6 个完整 job（2 台 server × 3）总墙钟时间约 13 分钟跑完。
- turbogate 隧道全程保持在线；`TURBOEVAL_TARGET_API_KEY` 必须是 `@together.ai` 的 Together key（不能是 `EMPTY`），评测服务才能通过 gate。
- API key 只从 `api.txt` 导出到进程环境变量；从未写入任何文件/日志/commit。
