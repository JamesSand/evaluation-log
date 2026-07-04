# 006 — Qwen3-8B：BF16 vs NVFP4 vs QAD-w4a4-kv8-step500，GPQA-Diamond（SGLang + turbogate + turboeval）+ QAD 诊断

> **⚠️ 更正（2026-07-02，见 [008](008-qad-s500-vllm-repro-sglang-serving-bug-confirmed.md)）：**
> 下文记录的 QAD"生成退化"问题，事后证实是 **SGLang 侧针对该 checkpoint 的 serving 缺陷，
> 而非 checkpoint 本身受损**。同一 checkpoint 改用 vLLM 0.24.0 serving（同一台机器、相同采样、
> 相同 turboeval 流程）可以干净地终止生成，得分约 0.52–0.58。下文的 QAD 分数（约 0.15）以及
> "w4a4 QAD 破坏了输出纪律"的解读，测的其实是 SGLang 的 bug，不是模型本身。BF16 和 NVFP4 的
> 数字不受影响（引擎内自洽可比）。

- **日期：** 2026-07-02
- **机器 / 软件栈：** 与 [005](005-qwen3-30b-a3b-bf16-vs-nvfp4-gpqa-sglang-turboeval.md) 相同 — 4x B200（sm100，驱动 575.57.08），SGLang **0.5.9**（`/home/c3-debug/venvs/sglang`，torch 2.9.1+cu128），turbogate 0.1.0，托管版 turboeval，本地 `turboeval` CLI。
- **模型（已下载到 `model-and-data/` 的快照）：**
  - BF16：`Qwen3-8B`（Qwen/Qwen3-8B，16G）
  - NVFP4：`Qwen3-8B-NVFP4`（nvidia/Qwen3-8B-NVFP4，6G；ModelOpt NVFP4 w4a4 group16，KV FP8）
  - QAD：`Qwen3-8B-qad-w4a4-kv8-exported/step500`（togethercomputer/Qwen3-8B-modelopt-qad-w4a4-kv8-exported 的 `step500/`；ModelOpt NVFP4 w4a4，KV FP8 — QAD student，参见 004 次实验用 vLLM 对其打分）
- **实验设计：** 3 个模型 × 3 次完整 GPQA-Diamond（198 题，thinking 模式）= 9 个 job；服务分别跑在 GPU 0/1/2（端口 30000/30001/30002），协议与 005 一致（先过 smoke-5 门检，所有采样参数完全相同）。

## Serving（与 005 相同的配方）

TP=1，`--context-length 40960`，`--mem-fraction-static 0.85`，`--trust-remote-code`，不加 `--reasoning-parser`，
`--preferred-sampling-params '{"temperature":0.6,"top_p":0.95,"top_k":20,"min_p":0}'`（三台服务均通过 `/get_server_info` 核实；thinking 的 `<think>` 保留在 `content` 里 — 每台都用真实补全 smoke 验证过）。

服务名：`qwen3-8b` / `qwen3-8b-nvfp4` / `qwen3-8b-qad-w4a4-kv8-s500`。
Gate：`zhizhou-qwen3-8b-{bf16,nvfp4,qad}.gate.together-turbo.com`（带鉴权，无 key 返回 401）。

相比 005 有两处 serving 差异（都值得记住）：

1. **两个 FP4 8B checkpoint 都必须显式加 `--quantization modelopt_fp4`。** 与 30B-A3B NVFP4（自动检测正常）不同，自动检测把这两个模型路由到了 `ModelOptFp8Config`，然后崩溃报 `ValueError: ModelOptFp8Config only supports static FP8 quantization…`。显式指定该 flag 即可修复；KV FP8 仍会自动生效（`torch.float8_e4m3fn`）。
2. **QAD step500 的 tokenizer_config 里 `extra_special_tokens` 字段格式有误（是 list 而不是 dict）**——会让当前版本的 transformers 崩溃（`AttributeError: 'list' object has no attribute 'keys'`）。基座 Qwen3-8B 的 config 里根本没有这个字段，且那些 token 本来就都已注册；只在**本地副本**里删掉了该字段（旁边保留了 `tokenizer_config.json.bak`）。这是导出器的 bug，值得在上游 HF repo 修掉。

## 提交

与 005 完全相同（`gpqa_diamond`，`num_examples=198`，`concurrency=auto`，`temperature=0.6`，`top_p=0.95`，`max_tokens=32768`，`TURBOEVAL_TARGET_API_KEY=$TOGETHER_API_KEY`）。
Smoke-5 job（全部 5/5，0 出错）：BF16 `evl_eca9afff387f4014881fa180eb1d0150`，NVFP4 `evl_0dbba7a629e14845ad96cd21ea7b7229`，QAD `evl_55d4b7723a75462187b44ee412410f0a`。

## 结果（完整 gpqa_diamond，198 题，thinking 模式）

所有 job 均 `completed`，每个都是 **198/198，0 出错**。

| 模型 | run | accuracy | job_id |
| --- | --- | --- | --- |
| BF16 | 1 | 0.5758 | `evl_876ea06bc6864fe4a84f29dcc54f5f27` |
| BF16 | 2 | 0.5606 | `evl_763dff21a45e4126a705945f8d711285` |
| BF16 | 3 | 0.6010 | `evl_a76321dd634c4506bc322000f8022cfe` |
| NVFP4 | 1 | 0.5000 | `evl_6193b866ed7a4e9cb75319cc0be0da69` |
| NVFP4 | 2 | 0.4495 | `evl_ae17891dc7384fdeae5d53778539152a` |
| NVFP4 | 3 | 0.5303 | `evl_c63aa70e2b4f478e85f8d326b270acdd` |
**QAD step500 — 走 turboeval 的 GPQA-Diamond（所有 run、任意 concurrency）：低于随机水平，约 0.15。这是
答案格式（FORMAT）抽取失败，不代表模型的推理质量（见后文分析）。**

| QAD s500 | run | accuracy | conc | 备注 | job_id |
| --- | --- | --- | --- | --- | --- |
| — | 1 | （已取消） | 235 | 资源争用 | `evl_a955e14a6794484281cc8aa20ca2604a` |
| — | 2 | 0.1414 | 217 | 198/198，0 err | `evl_3f637455ea3c4ccdb8f4f11e56a9f714` |
| — | 3 | 0.1717 | 256 | 198/198，0 err | `evl_1bc3446d13e6420985eda69d76ae9e38` |
| — | 诊断 | 0.148（61/198，已取消） | 16 | **服务器未饱和**（`token usage 0.12`） | `evl_9095eb24a32c41489fdef37e74c3d7a2` |
| — | clean 1 | 0.1313 | 32 | 198/198，0 err，`token usage 0.07` | `evl_fc974071521d45bc9e68fcf592a6fa04` |
| — | clean 2 | 0.1869 | 32 | 198/198，0 err | `evl_8a1197f622d64a22bf05251ac198fab3` |
| — | clean 3 | 0.1414 | 32 | 198/198，0 err | `evl_5e1f4615170846de94381c2494d61f86` |

Clean conc-32 三次平均 = **0.1532**（0.131–0.187）— 与 auto 各次（0.14–0.17）以及未饱和的
conc-16 诊断（0.148）一致。**确认 concurrency 不是影响因素；无论并发多少分数都在 ~0.15 附近。**
全部结果都印证了后文所述的答案格式伪影。

前端 URL：`https://eval.together-turbo.com/runs/<job_id>`。

### 汇总（三次平均）

| 模型 | GPQA-Diamond 均值（范围） | 相对 BF16 的 Δ |
| --- | --- | --- |
| Qwen/Qwen3-8B (BF16) | **0.5791**（0.5606–0.6010） | — |
| nvidia/Qwen3-8B-NVFP4 (w4a4, KV FP8) | **0.4933**（0.4495–0.5303） | **−8.6 pts** |
| QAD w4a4-kv8 step500（turboeval 原样打分） | **0.1532**（0.131–0.187）— 是 serving 伪影，不是质量分数 | n/a |
| QAD w4a4-kv8 step500（本地 `\boxed` 感知 grader） | **0.3350**（0.298–0.364）— 仍被截断严重拖累 | n/a |

## QAD 真实分数测试（本地 boxed 感知 grader）— 两种失败模式

为了得到 QAD 的真实能力，我用本地 harness（`scratchpad/gpqa_boxed_eval.py`，3 个 seed × 198 题）直接打同一台
sglang 服务器：**prompt、题目、采样与 simple-evals 完全一致**，只把答案抽取器改为依次尝试
`\boxed{X}` → `Answer: X` → 最后一个独立的选项字母。这样可以把伪影隔离出来。

结果：boxed 感知均值 **0.335**（从 turboeval 的 0.153 提上来）— 但仍远低于 BF16 的 0.579。把
594 条样本合并起来看，原因非常明显：

| 情况 | 占比 | accuracy |
| --- | --- | --- |
| **被截断（`finish=length`，打满 32768）** | **450/594 = 75.8%** | 0.287（≈ 随机；根本没输出真正的答案） |
| 正常收尾（`finish=stop`） | 144/594 = 24.2% | 0.486 |
| — 其中，答案以 `Answer:` 形式给出 | 75/144 | — |
| — 其中，答案以 `\boxed{}` 形式给出（turboeval 会漏掉） | 55/144 = 38% | — |

**所以 QAD 存在两种截然不同的失败模式，而且 `\boxed` 只是*次要*的那个：**

1. **生成退化 / 无法终止（主导因素，约 76%）。** 在难的 GPQA 题目上，w4a4 student 会先在某个短语上
   打转，随后崩塌成退化的 token 垃圾（字面上就是 `),,)):` `,]` `)))` …），一路跑满 `max_tokens=32768`
   上限，始终没有给出答案。有一条被截断的回复长达 143 KB，几乎全是标点符号糊。任何抽取器都救不回
   这些样本 → 真正把分数打垮的就是它。
   （确认是真实现象、不是 serving bug：EOS 配置与基座 Qwen3-8B 完全一致，而且简单/短 prompt 都能在
   349–1815 个 token 处干净收尾——只有长的难题推理轨迹才停不下来。）
2. **`\boxed{}` 答案格式（次要因素，占正常收尾少数派的约 38%）。** 当它*确实*收尾时，经常写成
   `\boxed{B}` 而不是要求的 `Answer: B`；turboeval 的 simple-evals grader（正则
   `Answer:\s*([A-D])`）会漏掉这些。本地 boxed 感知 grader 能把它们捞回来，这就是 0.153 → 0.335 的增益来源。

在它能正常收尾的子集上，知识水平约 0.49（单看 seed0 是 0.588，与 004 次实验 / BF16 相当），
说明模型的*知识*基本完好——但它的**输出纪律**（终止 + 格式）被激进的 w4a4 量化在仅 step500 时
严重破坏了。结论：这个 checkpoint 以当前 serving 状态无法用于 GPQA 类评测；需要先解决截断/退化问题
（更多蒸馏步数、更好的 w4a4 配方，或加 repetition penalty / 更短的 max_tokens 兜底），之后测出的
质量数字才有意义。

## 为什么 QAD 在 turboeval 上低于随机水平 — 答案格式（FORMAT）抽取失败（次要模式）

QAD step500 每次 turboeval run 都得 **0.14–0.17，低于 GPQA 的 0.25 随机基线**。诊断如下：

- **不是 concurrency / KV 饱和的问题。** 我最初的假设错了：`auto` 的 job 确实把服务器打满了
  （conc 217–256，`token usage 1.00`，比 BF16 慢约 3.5–6 倍），但在**固定 conc 16、服务器未饱和
  （`token usage 0.12`，无排队）**的重跑中仍然只有 **0.148**。负载不是原因。
- **根因：QAD student 把最终答案写成 `\boxed{B}` / `\boxed{\frac14}`，而不是 simple-evals 的 GPQA
  grader 要求的 `Answer: B` 行。** gpqa_diamond 使用固定的 openai/simple-evals prompt + grader（正则
  `Answer:\s*([A-D])`；唯一可调参数是 `n_samples`——没有自定义答案正则，也没有 system-prompt 钩子）。
  已针对这台服务器手工验证：在一道真实的研究生物理选择题上，模型推理**正确**（`finish=stop`，
  `</think>` 干净闭合，选了 `\boxed{B}` 即正确选项），但始终没写 `Answer: B`，于是 grader 什么都
  抽不到 → 记 0 分 → 低于随机。（在"2+2"这种*平凡* prompt 上它倒是遵守 `Answer:` 格式，这也是
  5 条 smoke 能通过的原因——smoke 检查的是能否完成，不是难题上的格式。）
- **这是 w4a4-QAD 系列的已知失败模式**，`zhizhou-note/004-…-output-examples.md` 里有记录：
  当时的低分是"大量 response 抽不到 (A)/(B)/(C)/(D)，记为 invalid=0"——答案无法解析，而不是推理
  错误。这里是同一机制。
- **结论：** ~0.15 是诚实的*同流程*数字，但它测的是答案格式化能力，不是模型能力。要得到模型真实的
  GPQA 质量，需要 `\boxed` 感知的 grader（比如产出 004 次实验数字的 lm-eval / run_eval 路径）——
  这超出了固定的 turboeval simple-evals grader 的能力范围。上面补充的 clean conc-32 重跑只是为了
  给出一个稳定、无争用的同流程数字；它们不改变结论。

## Caveats

- 协议/机器/引擎与 005 相同；BF16 与 NVFP4 8B 之间、以及与 005 的结果可直接比较。
- **QAD 的 turboeval 数字不是质量分数**——见上文格式分析。不要把它当作模型能力去和 BF16/NVFP4 的
  数字比较。
- 均为 3 次平均；GPQA 单次波动很大（NVFP4 在这里跨 0.449–0.530）。
- 与更早的单次结果一致性核对（001，vLLM）：bf16 0.5808 / 朴素 NVFP4 0.5051——本次 sglang 三次均值
  （0.5791 / 0.4933）在噪声范围内吻合。
- 30B-A3B（005 次实验）在 NVFP4 下只掉了 −3.7 pts；而 dense 8B 掉了 −8.6 pts——小的 dense 模型
  对量化敏感得多。
- QAD 需要两项专属的 serving 修复（其他 checkpoint 都不需要）：显式 `--quantization modelopt_fp4`，
  以及从本地 `tokenizer_config.json` 里删掉格式有误的 `extra_special_tokens`（list 而非 dict；
  导出器 bug；保留了 `.bak`；`\boxed` 行为与此无关——用标准 Qwen3 模板时该行为依旧存在）。
