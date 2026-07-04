#!/usr/bin/env python3
"""Cross-model K3 KL divergence via SGLang, following xorl-internal's
experiments/k3_tests/compare_logprobs.py mechanics (sampling via /generate with
input_ids + return_logprob; scoring via prefill max_new_tokens=0 and
input_token_logprobs[-gen_len:]).

Matrix: sample greedily (temp 0) from a SAMPLER model, teacher-force the same
token sequence through TARGET models, compute per-token estimators:
  k1  = logp_sampler - logp_target                  (unbiased for KL(p||q))
  k3  = exp(logq - logp) - (logq - logp) - 1        (Schulman, unbiased for KL(p||q))
  k3_repo = exp(logp - logq) - (logp - logq) - 1    (repo-literal direction, reference)
"""
import csv, json, random, sys, time
import numpy as np
import urllib.request
from concurrent.futures import ThreadPoolExecutor

SCRATCH = "/home/c3-debug/.tmp/claude-0/-home-c3-debug-workspace-low-precision-project-turbo-skills/5a034cf5-4a26-413c-8eda-c5ed5c9f4c3b/scratchpad"
SERVERS = {"bf16": "http://127.0.0.1:30000", "nvfp4": "http://127.0.0.1:30001", "qad": "http://127.0.0.1:30002"}
MAX_NEW = 1024
CONC = 16        # sampling concurrency
CONC_SCORE = 4   # scoring concurrency (full-seq logprobs materialize large logits)

QUERY_TEMPLATE = """Answer the following multiple choice question. The last line of your response should be of the following format: 'Answer: $LETTER' (without quotes) where LETTER is one of ABCD. Think step by step before answering.

{Question}

A) {A}
B) {B}
C) {C}
D) {D}""".strip()

GENERIC = [
    "Explain the concept of gradient descent in machine learning.",
    "Write a Python function that computes the Fibonacci sequence using dynamic programming.",
    "What are the key differences between TCP and UDP protocols?",
    "Summarize the main ideas behind transformer architectures in deep learning.",
    "Describe the process of photosynthesis in plants, step by step.",
    "A store sells apples for $2 each and oranges for $3 each. If Maria buys 7 apples and 5 oranges, how much does she spend in total?",
    "A train travels 120 km in 1.5 hours. What is its average speed in km/h?",
    "If a rectangle has a perimeter of 36 cm and its length is twice its width, what are its dimensions?",
    "Tom has 3 times as many marbles as Jane. Together they have 48 marbles. How many does each have?",
    "A tank is filled by pipe A in 6 hours and by pipe B in 4 hours. How long to fill it with both pipes open?",
    "Write a SQL query to find the second-highest salary in an employees table.",
    "Implement binary search in Python and explain its time complexity.",
    "Explain the difference between processes and threads in operating systems.",
    "What is Bayes' theorem? Give a simple worked example.",
    "Prove that the square root of 2 is irrational.",
    "Explain how HTTPS establishes a secure connection (TLS handshake).",
    "What causes the seasons on Earth? Explain the role of axial tilt.",
    "Compare supervised, unsupervised, and reinforcement learning with examples.",
    "Explain what a hash map is and how collisions are handled.",
    "A ball is dropped from 80 m. Ignoring air resistance, how long does it take to hit the ground (g=10 m/s^2)?",
]

def load_gpqa(n=32):
    rows = []
    with open(f"{SCRATCH}/gpqa_diamond.csv", newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("Question") and r.get("Correct Answer"):
                rows.append(r)
    rng = random.Random(0)
    prompts = []
    for row in rows[:n]:
        choices = [row["Correct Answer"], row["Incorrect Answer 1"], row["Incorrect Answer 2"], row["Incorrect Answer 3"]]
        perm = rng.sample(range(4), 4)
        s = [choices[i] for i in perm]
        prompts.append(QUERY_TEMPLATE.format(Question=row["Question"], A=s[0], B=s[1], C=s[2], D=s[3]))
    return prompts

def post(url, data, timeout=1200):
    req = urllib.request.Request(url, data=json.dumps(data).encode(),
                                 headers={"content-type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=timeout))

def sample(server, input_ids):
    """Greedy-generate; return (output_ids, sampler_logprobs)."""
    r = post(f"{server}/generate", {
        "input_ids": input_ids,
        "sampling_params": {"temperature": 0, "max_new_tokens": MAX_NEW},
        "return_logprob": True, "return_text_in_logprobs": False,
        "logprob_start_len": -1,
    })
    meta = r["meta_info"]
    out = meta["output_token_logprobs"]
    return [x[1] for x in out], [x[0] for x in out]

def score(server, full_ids, gen_len, prompt_len):
    """Prefill-score; per-token logprobs for last gen_len tokens (repo sglang_score,
    but logprob_start_len=prompt_len so only gen-position logits materialize — avoids
    the OOM that logprob_start_len=0 causes at vocab 151k)."""
    r = post(f"{server}/generate", {
        "input_ids": full_ids,
        "sampling_params": {"temperature": 0, "max_new_tokens": 0},
        "return_logprob": True, "return_text_in_logprobs": False,
        "logprob_start_len": max(prompt_len - 1, 0),
    })
    all_lps = [x[0] for x in r["meta_info"]["input_token_logprobs"]]
    return all_lps[-gen_len:]

def stats(arr):
    a = np.asarray(arr, dtype=np.float64)
    return dict(mean=float(a.mean()), median=float(np.median(a)),
                p95=float(np.percentile(a, 95)), p99=float(np.percentile(a, 99)),
                max=float(a.max()), n=int(a.size))

def main():
    from transformers import AutoTokenizer
    tok = AutoTokenizer.from_pretrained(
        "/home/c3-debug/workspace/low-precision-project/model-and-data/Qwen3-8B")
    gpqa, generic = load_gpqa(32), GENERIC
    prompt_sets = [("gpqa", p) for p in gpqa] + [("generic", p) for p in generic]
    encoded = []
    for tag, p in prompt_sets:
        ids = tok.apply_chat_template([{"role": "user", "content": p}],
                                      add_generation_prompt=True, tokenize=True)
        encoded.append((tag, ids))
    print(f"{len(encoded)} prompts (gpqa=32, generic={len(generic)})", flush=True)

    # matrix: (sampler, [targets to score])   bf16 self-score = sanity row
    matrix = [("bf16", ["bf16", "nvfp4", "qad"]),
              ("qad", ["bf16"]),
              ("nvfp4", ["bf16"])]
    report = {}
    for sampler, targets in matrix:
        t0 = time.monotonic()
        with ThreadPoolExecutor(max_workers=CONC) as ex:
            samples = list(ex.map(lambda e: sample(SERVERS[sampler], e[1]), encoded))
        gen_lens = [len(s[0]) for s in samples]
        print(f"[sampler={sampler}] sampled {len(samples)} seqs, "
              f"tokens total={sum(gen_lens)}, capped@{MAX_NEW}={sum(1 for g in gen_lens if g >= MAX_NEW)} "
              f"({time.monotonic()-t0:.0f}s)", flush=True)
        for target in targets:
            t1 = time.monotonic()
            def score_one(i):
                (tag, ids), (out_ids, s_lps) = encoded[i], samples[i]
                if not out_ids:
                    return None
                t_lps = score(SERVERS[target], list(ids) + out_ids, len(out_ids), len(ids))
                return tag, np.array(s_lps), np.array(t_lps)
            with ThreadPoolExecutor(max_workers=CONC_SCORE) as ex:
                scored = [x for x in ex.map(score_one, range(len(encoded))) if x]
            agg = {}
            for split in ("all", "gpqa", "generic"):
                lr_q_p = np.concatenate([t - s for tag, s, t in scored
                                         if split == "all" or tag == split])  # logq - logp
                k1 = -lr_q_p                                   # logp - logq
                k3 = np.exp(lr_q_p) - lr_q_p - 1.0             # unbiased KL(p||q)
                k3r = np.exp(-lr_q_p) + lr_q_p - 1.0           # repo-literal direction
                agg[split] = dict(k1=stats(k1), k3=stats(k3), k3_repo=stats(k3r))
            key = f"{sampler}->{target}"
            report[key] = dict(splits=agg,
                               per_prompt_k3=[float((np.exp(t-s)-(t-s)-1).mean()) for _, s, t in scored],
                               gen_tokens=int(sum(len(s) for _, s, _ in scored)),
                               capped=sum(1 for g in gen_lens if g >= MAX_NEW))
            a = agg["all"]
            print(f"  {key}: k3_mean={a['k3']['mean']:.6g} k1_mean={a['k1']['mean']:.6g} "
                  f"k3_median={a['k3']['median']:.3g} p99={a['k3']['p99']:.3g} "
                  f"n={a['k3']['n']} ({time.monotonic()-t1:.0f}s)", flush=True)
    json.dump(report, open(f"{SCRATCH}/k3_cross_model_results.json", "w"), indent=1)
    print(f"saved {SCRATCH}/k3_cross_model_results.json", flush=True)

if __name__ == "__main__":
    main()
