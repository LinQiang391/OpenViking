#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MEMORYBENCH_PATH="${1:-}"
BENCHMARK="${2:-longmemeval}"
LIMIT="${3:-5}"
RUN_ID="${4:-ov-${BENCHMARK}-$(date +%Y%m%d-%H%M%S)}"
JUDGE_MODEL="${JUDGE_MODEL:-doubao-seed-1-8-251228}"
ANSWERING_MODEL="${ANSWERING_MODEL:-doubao-seed-1-8-251228}"

if [[ -z "${MEMORYBENCH_PATH}" ]]; then
  echo "Usage: $0 <memorybench_path> [benchmark] [limit] [run_id]"
  echo "Example: $0 /tmp/memorybench longmemeval 5 ov-longmem-demo"
  exit 1
fi

python3 "${SCRIPT_DIR}/install_openviking_provider.py" --memorybench-path "${MEMORYBENCH_PATH}"

echo "Running MemoryBench..."
echo "  provider: openviking"
echo "  benchmark: ${BENCHMARK}"
echo "  limit: ${LIMIT}"
echo "  run_id: ${RUN_ID}"
echo "  judge model: ${JUDGE_MODEL}"
echo "  answering model: ${ANSWERING_MODEL}"

(
  cd "${MEMORYBENCH_PATH}"
  bun run src/index.ts run \
    -p openviking \
    -b "${BENCHMARK}" \
    -j "${JUDGE_MODEL}" \
    -m "${ANSWERING_MODEL}" \
    -r "${RUN_ID}" \
    -l "${LIMIT}"
)

echo "Done. Inspect results under ${MEMORYBENCH_PATH}/runs/${RUN_ID}/"
