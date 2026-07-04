#!/bin/bash
# Usage: sweep_lane.sh <gpu> <port> <step1> <step2> ...
# Serves each checkpoint on the given GPU/port, runs K3 vs BF16(:8001), kills by exact PID.
set -u
GPU=$1; PORT=$2; shift 2
BASE=/home/c3-debug/workspace/low-precision-project/model-and-data/Qwen3-8B-qad-w4a4-kv8-exported
SCRATCH=/home/c3-debug/.tmp/claude-0/-home-c3-debug-workspace-low-precision-project-turbo-skills/5a034cf5-4a26-413c-8eda-c5ed5c9f4c3b/scratchpad
PY=/home/c3-debug/venvs/sglang/bin/python
export FLASHINFER_WORKSPACE_BASE=/home/c3-debug/fi_ws
export VLLM_CACHE_ROOT=/home/c3-debug/.vllm-cache
export TRITON_CACHE_DIR=/home/c3-debug/.triton-cache-lane$GPU
export TORCHINDUCTOR_CACHE_DIR=/home/c3-debug/.inductor-cache-lane$GPU

for STEP in "$@"; do
  D=$BASE/$STEP
  echo "[lane$GPU] === $STEP start $(date +%H:%M:%S) ==="
  # tokenizer fix (idempotent)
  if [ ! -f "$D/tokenizer_config.json.bak" ]; then
    cp "$D/tokenizer_config.json" "$D/tokenizer_config.json.bak"
    python3 -c "
import json
p='$D/tokenizer_config.json'
d=json.load(open(p)); d.pop('extra_special_tokens', None)
json.dump(d, open(p,'w'), indent=2, ensure_ascii=False)"
  fi
  LOG=/home/c3-debug/serve-logs/sweep-$STEP.log
  CUDA_VISIBLE_DEVICES=$GPU /home/c3-debug/venvs/vllm/bin/vllm serve "$D" \
    --served-model-name ckpt --host 0.0.0.0 --port $PORT \
    --tensor-parallel-size 1 --max-model-len 40960 --gpu-memory-utilization 0.85 \
    --kv-cache-dtype fp8 --trust-remote-code > "$LOG" 2>&1 &
  PID=$!
  # wait for health (max 25 min), bail on crash
  DEADLINE=$(( $(date +%s) + 1500 )); OK=0
  while [ $(date +%s) -lt $DEADLINE ]; do
    if ! kill -0 $PID 2>/dev/null; then echo "[lane$GPU] $STEP SERVER DIED: $(grep -E 'Error|Exception' $LOG | tail -1 | head -c 150)"; break; fi
    if curl -sf -m3 http://127.0.0.1:$PORT/health >/dev/null 2>&1; then OK=1; break; fi
    sleep 10
  done
  if [ $OK -eq 1 ]; then
    $PY $SCRATCH/k3_step_sweep.py $STEP $PORT || echo "[lane$GPU] $STEP K3 FAILED"
  else
    echo "[lane$GPU] $STEP SKIPPED (server not healthy)"
  fi
  kill $PID 2>/dev/null
  for i in $(seq 1 30); do kill -0 $PID 2>/dev/null || break; sleep 2; done
  kill -9 $PID 2>/dev/null
  # restore tokenizer
  [ -f "$D/tokenizer_config.json.bak" ] && mv -f "$D/tokenizer_config.json.bak" "$D/tokenizer_config.json"
  sleep 5
  echo "[lane$GPU] === $STEP done $(date +%H:%M:%S) ==="
done
echo "[lane$GPU] LANE COMPLETE"
