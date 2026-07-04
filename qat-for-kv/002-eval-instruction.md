
刚刚我们已经验证了下边这些 model 的 nvfp4 的具体的版本

llmat/Qwen3-4B-NVFP4

nvidia/Qwen3-30B-A3B-NVFP4

nvidia/DeepSeek-V4-Flash-NVFP4


接下来我们做这样一个事情，我们要对比这些 model 的 bf16 的版本，和这些 model 在 nvfp4 kv cache 下边的差异
这些 model 的 bf16 版本如下
Qwen/Qwen3-4B
For thinking mode (enable_thinking=True), use Temperature=0.6, TopP=0.95, TopK=20, and MinP=0. DO NOT use greedy decoding, as it can lead to performance degradation and endless repetitions.
For non-thinking mode (enable_thinking=False), we suggest using Temperature=0.7, TopP=0.8, TopK=20, and MinP=0.

Qwen/Qwen3-30B-A3B
For thinking mode (enable_thinking=True), use Temperature=0.6, TopP=0.95, TopK=20, and MinP=0. DO NOT use greedy decoding, as it can lead to performance degradation and endless repetitions.
For non-thinking mode (enable_thinking=False), we suggest using Temperature=0.7, TopP=0.8, TopK=20, and MinP=0.

deepseek-ai/DeepSeek-V4-Flash
temperature = 1.0, top_p = 1.0.


我想知道这些 model 在 RULER 128k context length 下边的表现分数分别是多少？

你给我用 turboeval 来测试一下，sampling 的参数我都给你写在上边了。原始的 model 和 nvfp4 的 model 都用同一套 sampling 的参数。我知道 qwen 系列的 model 用 non thinking 测试 RULER 是对的。你给我看一下 deepseek v4 flash ，我不知道 dpsk v4 flash 有没有 thinking 或者 non thinking 的模式的区别？











