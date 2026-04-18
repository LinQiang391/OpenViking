#!/usr/bin/env bash
################################################################################
# E2E 一键测试脚本
#
# 用法:
#   bash run_e2e_tests.sh                       # 运行全部功能测试
#   bash run_e2e_tests.sh --functional          # 仅功能测试
#   bash run_e2e_tests.sh --locomo              # 仅 LocomoSmall 评测
#   bash run_e2e_tests.sh --all                 # 功能测试 + LocomoSmall
#
# 环境变量（可选）:
#   OG_PASSWORD          openGauss 密码 (用于数据库直连验证)
#   JUDGE_API_KEY        LLM Judge API Key (用于答案评分)
#   VOLCENGINE_API_KEY   Volcengine API Key (Judge 备选)
#   OV_HOST / OV_PORT    OpenViking 地址 (默认 127.0.0.1:1933)
#   OC_HOST / OC_PORT    OpenClaw 地址 (默认 127.0.0.1:18790)
#   OG_HOST / OG_PORT    openGauss 地址 (默认 127.0.0.1:15432)
#   LOCOMO_DATA          locomo10_small.json 路径 (默认从 memcore 分支获取)
################################################################################
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_step()  { echo -e "\n${CYAN}========== $* ==========${NC}"; }

MODE="functional"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --functional) MODE="functional"; shift ;;
        --locomo)     MODE="locomo"; shift ;;
        --all)        MODE="all"; shift ;;
        -h|--help)
            echo "Usage: $0 [--functional|--locomo|--all]"
            echo ""
            echo "  --functional   Run functional E2E tests (default)"
            echo "  --locomo       Run LocomoSmall benchmark only"
            echo "  --all          Run both functional and locomo tests"
            exit 0
            ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

log_step "环境检查"

if ! command -v python3 &>/dev/null; then
    log_warn "python3 not found, trying python..."
    if ! command -v python &>/dev/null; then
        echo -e "${RED}[ERROR]${NC} Python not found. Please install Python 3.8+"
        exit 1
    fi
    PYTHON=python
else
    PYTHON=python3
fi

log_info "Python: $($PYTHON --version)"

if ! $PYTHON -c "import pytest" 2>/dev/null; then
    log_info "安装测试依赖..."
    $PYTHON -m pip install -r "$SCRIPT_DIR/requirements.txt" -q
fi

log_step "服务可达性预检"

OV_HOST="${OV_HOST:-127.0.0.1}"
OV_PORT="${OV_PORT:-1933}"
OC_HOST="${OC_HOST:-127.0.0.1}"
OC_PORT="${OC_PORT:-18790}"

check_port() {
    local host="$1" port="$2" name="$3"
    if curl -sf --connect-timeout 3 "http://${host}:${port}/" >/dev/null 2>&1 || \
       curl -sf --connect-timeout 3 "http://${host}:${port}/health" >/dev/null 2>&1; then
        log_info "$name (${host}:${port}) ✓"
        return 0
    else
        log_warn "$name (${host}:${port}) 不可达"
        return 1
    fi
}

SERVICES_OK=true
check_port "$OV_HOST" "$OV_PORT" "OpenViking" || SERVICES_OK=false
check_port "$OC_HOST" "$OC_PORT" "OpenClaw"   || SERVICES_OK=false

if [[ "$SERVICES_OK" != "true" ]]; then
    echo -e "${RED}[ERROR]${NC} 部分服务不可达，请先确认部署完成"
    echo "  提示: bash deploy.sh -password 'YourPassword'"
    exit 1
fi

log_info "所有核心服务可达"

if [[ -n "${OG_PASSWORD:-}" ]]; then
    log_info "OG_PASSWORD 已设置，将验证 openGauss 数据库"
else
    log_warn "OG_PASSWORD 未设置，跳过数据库直连验证"
fi

if [[ -n "${JUDGE_API_KEY:-${VOLCENGINE_API_KEY:-${ARK_API_KEY:-}}}" ]]; then
    log_info "Judge API Key 已设置，将执行 LLM 评分"
else
    log_warn "无 Judge API Key，跳过 LLM 评分"
fi

PYTEST_OPTS=(-v --tb=short -x --rootdir="$SCRIPT_DIR")

run_functional() {
    log_step "功能测试 (E2E Functional)"
    $PYTHON -m pytest test_e2e_functional.py "${PYTEST_OPTS[@]}" "$@"
}

run_locomo() {
    log_step "LocomoSmall 性能评测"
    $PYTHON -m pytest test_locomo_benchmark.py "${PYTEST_OPTS[@]}" -s "$@"
    if [[ -f "reports/locomo_summary.json" ]]; then
        echo ""
        log_step "LocomoSmall 评测结果"
        $PYTHON -c "
import json
with open('reports/locomo_summary.json') as f:
    s = json.load(f)
print(f\"Overall: {s['total_correct']}/{s['total_questions']} = {s['overall_accuracy']*100:.1f}%\")
for name, stats in s.get('by_category', {}).items():
    print(f\"  {name}: {stats['correct']}/{stats['total']} = {stats['accuracy']*100:.1f}%\")
"
    fi
}

EXIT_CODE=0

case "$MODE" in
    functional)
        run_functional || EXIT_CODE=$?
        ;;
    locomo)
        run_locomo || EXIT_CODE=$?
        ;;
    all)
        run_functional || EXIT_CODE=$?
        echo ""
        run_locomo || { EXIT_CODE=$?; true; }
        ;;
esac

echo ""
if [[ $EXIT_CODE -eq 0 ]]; then
    log_info "全部测试通过 ✓"
else
    echo -e "${RED}[FAIL]${NC} 部分测试失败 (exit=$EXIT_CODE)"
fi

exit $EXIT_CODE
