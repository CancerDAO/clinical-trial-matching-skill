#!/usr/bin/env bash
# v1.6.0 — End-to-end pipeline runner.
# Stages: search_plan → dual_source_search → metadata extraction → gating →
#         verification → feasibility scoring → risk lookup → efficacy lookup →
#         decision synthesis → HTML render → eval (optional)
#
# Usage:
#   bash scripts/run_v160_pipeline.sh \
#     --plan path/to/search_plan.json \
#     --patient path/to/patient.json \
#     --out-dir /tmp/output
#
set -euo pipefail

# ---- Args ----
PLAN=""
PATIENT=""
OUT_DIR=""
EVAL_CASE=""
TOP_N=3

while [[ $# -gt 0 ]]; do
  case "$1" in
    --plan) PLAN="$2"; shift 2;;
    --patient) PATIENT="$2"; shift 2;;
    --out-dir) OUT_DIR="$2"; shift 2;;
    --eval-case) EVAL_CASE="$2"; shift 2;;
    --top-n) TOP_N="$2"; shift 2;;
    *) echo "Unknown arg: $1"; exit 1;;
  esac
done

if [[ -z "$PLAN" || -z "$PATIENT" || -z "$OUT_DIR" ]]; then
  echo "Usage: $0 --plan <plan.json> --patient <patient.json> --out-dir <dir> [--eval-case <case.json>] [--top-n N]"
  exit 1
fi

mkdir -p "$OUT_DIR"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$SCRIPT_DIR/../repo"

echo "==================== v1.6.0 Pipeline ===================="
echo "Plan:    $PLAN"
echo "Patient: $PATIENT"
echo "Out dir: $OUT_DIR"
echo

# 1. Dual-source search (NCT only — ChiCTR queried separately by skill via MCP)
echo "[1/8] Dual-source search → $OUT_DIR/nct_results.json"
python3 "$REPO/retrieval/dual_source_search.py" \
  --plan "$PLAN" \
  --out "$OUT_DIR/nct_results.json" \
  --max-per-query 10

# 2. Metadata extraction
echo "[2/8] Trial metadata extraction → $OUT_DIR/nct_results_v160.json"
python3 "$REPO/extraction/trial_metadata_extractor.py" \
  --in "$OUT_DIR/nct_results.json" \
  --out "$OUT_DIR/nct_results_v160.json"

# 3. Gating
echo "[3/8] Gating → $OUT_DIR/gated.json"
python3 "$REPO/scoring/gating.py" \
  --in "$OUT_DIR/nct_results_v160.json" \
  --patient "$PATIENT" \
  --out "$OUT_DIR/gated.json"

# 4. Verification (live API)
echo "[4/8] NCT verification + citation chain → $OUT_DIR/verified.json"
python3 "$REPO/verification/nct_verifier.py" \
  --in "$OUT_DIR/gated.json" \
  --patient "$PATIENT" \
  --out "$OUT_DIR/verified.json"

# 5. Feasibility scoring
echo "[5/8] Feasibility scoring → $OUT_DIR/scored.json"
python3 "$REPO/scoring/feasibility.py" \
  --in "$OUT_DIR/verified.json" \
  --patient "$PATIENT" \
  --out "$OUT_DIR/scored.json"

# 6. Risk taxonomy
echo "[6/8] Risk taxonomy lookup → $OUT_DIR/risked.json"
python3 "$REPO/scoring/risk_lookup.py" \
  --in "$OUT_DIR/scored.json" \
  --patient "$PATIENT" \
  --out "$OUT_DIR/risked.json"

# 7. Efficacy + SoC lookup
echo "[7/8] Efficacy + SoC lookup → $OUT_DIR/efficacy.json"
python3 "$REPO/synthesis/efficacy_lookup.py" \
  --in "$OUT_DIR/risked.json" \
  --patient "$PATIENT" \
  --out "$OUT_DIR/efficacy.json"

# 8. Decision Report synthesis + HTML render
echo "[8/8] Decision Report synthesis"
cd "$REPO" && PYTHONPATH=. python3 synthesis/decision_paths.py \
  --in "$OUT_DIR/efficacy.json" \
  --patient "$PATIENT" \
  --out "$OUT_DIR/decision_report.json" \
  --top-n "$TOP_N"

echo "[9] HTML render → $OUT_DIR/report.html"
python3 "$REPO/synthesis/html_renderer.py" \
  --report "$OUT_DIR/decision_report.json" \
  --gated "$OUT_DIR/efficacy.json" \
  --patient "$PATIENT" \
  --out "$OUT_DIR/report.html"

# Optional eval
if [[ -n "$EVAL_CASE" ]]; then
  echo
  echo "[+] Running golden-case eval"
  python3 "$REPO/eval/runner.py" \
    --case "$EVAL_CASE" \
    --gated "$OUT_DIR/efficacy.json" \
    --report "$OUT_DIR/decision_report.json" \
    --out "$OUT_DIR/eval_report.json"
fi

echo
echo "==================== Pipeline complete ===================="
echo "Final HTML: $OUT_DIR/report.html"
