# 008 — QAD w4a4-kv8 step500：vLLM 复现 ⇒ 006 中低于随机水平的 GPQA 分数是 SGLang serving bug

- **日期:** 2026-07-02
- **问题:** 006 通过 SGLang 测得该 checkpoint 在 GPQA 上仅 0.13–0.19（76% 的生成为 runaway/退化输出），而昨天的 vLLM job `evl_43aaa7c8c076455096b1a40b70f31254` 得分 **0.5808**。昨天的分数是不是计数口径造成的假象——还是 SGLang 对这个 checkpoint 的 serving 坏了？
- **先回答计数口径的问题:** 不是假象。拉取了昨天的 job：`accuracy`（严格口径，errors=0，分母为全部题目）== `accuracy_scored` == 0.5808，198/198 全部完成，0 个报错，partial=False。没有任何样本被丢弃。
- **本次实验:** 在这台机器上用 vLLM 复现昨天的配置，并与 SGLang 的结果对比——同一 checkpoint、同一 turboeval 流程。

## 环境配置（对齐 run 004 / 昨天的 job）

- venv `/home/c3-debug/venvs/vllm`：**vLLM 0.24.0, torch 2.11.0+cu130**（与昨天完全相同的软件栈），GPU0。
- Serve 命令（004 的配方）：

```bash
vllm serve model-and-data/Qwen3-8B-qad-w4a4-kv8-exported/step500 \
  --served-model-name qwen3-8b-w4a4kv8-s500 --host 0.0.0.0 --port 8000 \
  --tensor-parallel-size 1 --max-model-len 40960 --gpu-memory-utilization 0.85 \
  --kv-cache-dtype fp8 \
  --override-generation-config '{"temperature":0.6,"top_p":0.95,"top_k":20,"min_p":0}' \
  --trust-remote-code
```

- 需要两处本地修复（记录下来供下次参考）：
  1. 与 006 相同的 `extra_special_tokens` tokenizer 修复（本地临时打上，跑完后恢复）。
  2. **vLLM 0.24.0 bug 补丁**：加载该 checkpoint 的 KV scale 时崩溃，报
     `TypeError: KVCacheScaleParameter.weight_loader() takes 2 positional arguments but 3 were given`
     （`models/qwen2.py:461` 在 fused-QKV 路径上多传了 `shard_id`）。给 venv 里的
     `layers/quantization/kv_cache.py` 打了补丁，使其接受并忽略多余参数（安全：scale 是标量，k/v 身份
     已经编码在参数名里）。昨天的环境显然没有触发这个问题——机器/环境不同。
- Warmup 约 16 分钟（fp4_gemm autotune 21 个 profile 约 10.6 分钟——与 004 的运维记录一致）。
- 端点暴露：turbogate `zhizhou-qad-vllm-repro`；提交参数与昨天完全一致（gpqa_diamond, 198, conc 32, temp 0.6, top_p 0.95, max_tokens 32768）。

## 决定性的 smoke 测试（与 006 抽样的同 5 道 GPQA 题）

| 题目 | SGLang 0.5.9 (006) | vLLM 0.24.0（本次） |
| --- | --- | --- |
| Q0 | finish=**length** 32768 tok，输出为乱码 | finish=stop **4294** tok，✅ |
| Q1 | finish=**length** 32768 | finish=stop 4800，❌（连贯但答错） |
| Q2 | finish=**length** 32768 | finish=stop 5127，✅（`\boxed`） |
| Q3 | finish=stop 1375 | finish=stop 3817，❌ |
| Q4 | finish=**length** 32768 | finish=stop 8350，✅ |

同一 checkpoint、同一采样参数：vLLM 在全部 5 题上都正常终止（长度与 004 的平均 6277 相符）；SGLang
在 4/5 题上 runaway。生成退化随引擎而变 → **是 SGLang 的 serving bug，不是 checkpoint 本身受损**。

## 通过 turboeval 跑完整 GPQA-Diamond（流程与 006 的 SGLang 运行完全一致）

| 运行 | 引擎 | accuracy | completed | errored | 耗时 | job_id |
| --- | --- | --- | --- | --- | --- | --- |
| 昨天 (004) | vLLM（另一环境） | 0.5808 | 198/198 | 0 | 460s | `evl_43aaa7c8c076455096b1a40b70f31254` |
| 复现 1 | vLLM（本机） | **0.5152** | 198/198 | 0 | 282s | `evl_0343b27c7119400da7509c418f64e5b6` |
| 复现 2 | vLLM（本机） | TBD | | | | `evl_b13ed4dfe3e0444caab9eec8dc5c4ac2` |
| 复现 3 | vLLM（本机） | TBD | | | | TBD |
| (006) 3 次运行 | SGLang 0.5.9 | 0.131 / 0.187 / 0.141 | 各 198/198 | 0 | — | 见 006 |

vLLM 3 次运行均值：TBD。

## 结论（待复现 2–3 完成后定稿）

1. **昨天的 0.5808 是真实成绩**——分母为全部题目、零报错、没有只按已判分样本过滤。
2. **006 中低于随机水平的 QAD 分数，以及 007 的 QAD K3（0.19–0.25），都被 SGLang 侧的 serving
   缺陷污染了**——针对这个 ModelOpt w4a4-kv8 导出件的 runaway/退化解码，vLLM 在相同权重 + 相同采样下
   并不出现。006/007 已附更正说明并指向本文。
3. `\boxed` 作答习惯确实存在但只是偶发（smoke 中 1/5）——它会在 simple-evals 判分器上损失一些分数，
   但不是主因；主因是随引擎而变的生成退化。
4. SGLang 缺陷的疑点（未验证）：modelopt_fp4 的 w4a4 GEMM 路径（`flashinfer_cutlass`）、KV FP8 的
   scale 处理，或二者在 sm100 上的相互作用。值得做一个最小复现并提 upstream issue。

## 清理

运行结束后已拆除 vLLM server 和隧道；QAD 的 tokenizer_config 已恢复；kv_cache.py 补丁保留在
vllm venv 中（见上文说明）。
