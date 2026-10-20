#!/usr/bin/env bash
# Benchmark local models with Ollama and compare prompt variants.
#   ollama pull qwen2.5-coder:7b && ollama pull llama3.1:8b
#   ./scripts/run_model_bench.sh qwen2.5-coder:7b llama3.1:8b
set -euo pipefail
MODELS=("${@:-qwen2.5-coder:7b}")
BASE_URL="${PATCHPROOF_BASE_URL:-http://localhost:11434/v1}"
for model in "${MODELS[@]}"; do
  for variant in basic checklist; do
    patchproof bench run --model "$model" --base-url "$BASE_URL" \
      --prompt-variant "$variant" --label "$model ($variant)" --out results
  done
done
patchproof bench table results/*/summary.json | tee results/RESULTS.md
