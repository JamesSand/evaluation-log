#!/usr/bin/env python3
"""Cross-model K3 on vLLM (OpenAI API), mirroring k3_cross_model.py's methodology.

Differences from the sglang harness (API only — math/prompts/sampling identical):
  - sample: POST /v1/completions with prompt=<token ids>, temperature=0, logprobs=0,
            extra "return_tokens_as_token_ids": true  -> gen token ids + logprobs
  - score:  POST /v1/completions with prompt=<full ids>, max_tokens=1,
            extra "prompt_logprobs": 0 -> per-position dict {token_id: {logprob}}
Usage: k3_cross_model_vllm.py <phase1|phase2>
  phase1: servers bf16:8001 qad:8000  -> rows bf16->bf16, bf16->qad, qad->bf16
  phase2: servers bf16:8001 nvfp4:8000 -> rows bf16->nvfp4, nvfp4->bf16 (reuses saved bf16 samples)
"""
import json, sys, time
import numpy as np
import urllib.request
from concurrent.futures import ThreadPoolExecutor

SCRATCH = "/home/c3-debug/.tmp/claude-0/-home-c3-debug-workspace-low-precision-project-turbo-skills/5a034cf5-4a26-413c-8eda-c5ed5c9f4c3b/scratchpad"
sys.path.insert(0, SCRATCH)
from k3_cross_model import load_gpqa, GENERIC, stats  # same prompts, same stats

MAX_NEW = 1024
CONC = 16
CONC_SCORE = 8   # vLLM prompt_logprobs=0 only materializes the chosen-token logprob; lighter than sglang path

MODELS = {"bf16": ("http://127.0.0.1:8001", "qwen3-8b"),
          "qad": ("http://127.0.0.1:8000", "qwen3-8b-w4a4kv8-s500"),
          "nvfp4": ("http://127.0.0.1:8000", "qwen3-8b-nvfp4")}

def post(url, data, timeout=1800):
    req = urllib.request.Request(url, data=json.dumps(data).encode(),
                                 headers={"content-type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=timeout))

def sample(key, input_ids):
    base, model = MODELS[key]
    r = post(f"{base}/v1/completions", {
        "model": model, "prompt": input_ids,
        "temperature": 0, "max_tokens": MAX_NEW,
        "logprobs": 0, "return_tokens_as_token_ids": True,
    })
    lp = r["choices"][0]["logprobs"]
    ids = [int(t.split(":")[1]) for t in lp["tokens"]]
    return ids, lp["token_logprobs"]

def score(key, full_ids, gen_len):
    base, model = MODELS[key]
    r = post(f"{base}/v1/completions", {
        "model": model, "prompt": full_ids,
        "temperature": 0, "max_tokens": 1,
        "prompt_logprobs": 0,
    })
    plp = r["choices"][0]["prompt_logprobs"]  # aligned to prompt positions; [0] is None
    out = []
    for pos in range(len(full_ids) - gen_len, len(full_ids)):
        entry = plp[pos]
        tok = str(full_ids[pos])
        if tok in entry:
            out.append(entry[tok]["logprob"])
        else:  # key may be int in some versions
            out.append(list(entry.values())[0]["logprob"])
    return out

def encode_prompts():
    from transformers import AutoTokenizer
    tok = AutoTokenizer.from_pretrained(
        "/home/c3-debug/workspace/low-precision-project/model-and-data/Qwen3-8B")
    prompt_sets = [("gpqa", p) for p in load_gpqa(32)] + [("generic", p) for p in GENERIC]
    return [(tag, tok.apply_chat_template([{"role": "user", "content": p}],
                                          add_generation_prompt=True, tokenize=True))
            for tag, p in prompt_sets]

def run_pair(encoded, samples, target, sampler_name, report):
    t1 = time.monotonic()
    def score_one(i):
        (tag, ids), (out_ids, s_lps) = encoded[i], samples[i]
        if not out_ids:
            return None
        t_lps = score(target, list(ids) + out_ids, len(out_ids))
        return tag, np.array(s_lps, dtype=np.float64), np.array(t_lps, dtype=np.float64)
    with ThreadPoolExecutor(max_workers=CONC_SCORE) as ex:
        scored = [x for x in ex.map(score_one, range(len(encoded))) if x]
    agg = {}
    for split in ("all", "gpqa", "generic"):
        lr = np.concatenate([t - s for tag, s, t in scored if split == "all" or tag == split])
        agg[split] = dict(k1=stats(-lr), k3=stats(np.exp(lr) - lr - 1.0),
                          k3_repo=stats(np.exp(-lr) + lr - 1.0))
    key = f"{sampler_name}->{target}"
    report[key] = dict(splits=agg, gen_tokens=int(sum(len(s) for _, s, _ in scored)))
    a = agg["all"]
    print(f"  {key}: k3_mean={a['k3']['mean']:.6g} k1_mean={a['k1']['mean']:.6g} "
          f"median={a['k3']['median']:.3g} p99={a['k3']['p99']:.3g} n={a['k3']['n']} "
          f"({time.monotonic()-t1:.0f}s)", flush=True)

def sample_all(encoded, key):
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=CONC) as ex:
        samples = list(ex.map(lambda e: sample(key, e[1]), encoded))
    lens = [len(s[0]) for s in samples]
    print(f"[sampler={key}] {len(samples)} seqs, tokens={sum(lens)}, "
          f"capped={sum(1 for g in lens if g >= MAX_NEW)}/{len(lens)} ({time.monotonic()-t0:.0f}s)", flush=True)
    return samples

def main():
    phase = sys.argv[1]
    encoded = encode_prompts()
    report_path = f"{SCRATCH}/k3_vllm_results.json"
    try:
        report = json.load(open(report_path))
    except Exception:
        report = {}
    if phase == "phase1":
        bf16_samples = sample_all(encoded, "bf16")
        json.dump([[list(map(int, s[0])), list(map(float, s[1]))] for s in bf16_samples],
                  open(f"{SCRATCH}/k3_vllm_bf16_samples.json", "w"))
        run_pair(encoded, bf16_samples, "bf16", "bf16", report)   # sanity
        run_pair(encoded, bf16_samples, "qad", "bf16", report)
        qad_samples = sample_all(encoded, "qad")
        run_pair(encoded, qad_samples, "bf16", "qad", report)
    elif phase == "phase2":
        bf16_samples = [(ids, lps) for ids, lps in json.load(open(f"{SCRATCH}/k3_vllm_bf16_samples.json"))]
        run_pair(encoded, bf16_samples, "nvfp4", "bf16", report)
        nvfp4_samples = sample_all(encoded, "nvfp4")
        run_pair(encoded, nvfp4_samples, "bf16", "nvfp4", report)
    json.dump(report, open(report_path, "w"), indent=1)
    print(f"saved {report_path}", flush=True)

if __name__ == "__main__":
    main()
