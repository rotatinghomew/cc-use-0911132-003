set -eu
workspace=$(mktemp -d)
trap 'rm -rf "$workspace"' EXIT
export PYTHONDONTWRITEBYTECODE=1
# -E -s：忽略 PYTHON* 环境变量与用户 site 包，保留仓库内包解析，等价于隔离模式的防护面。
PY="python3 -E -s"

bash scripts/demo.sh
python3 scripts/build_analysis_scenarios.py

# 同一证书库：先跑既有逐案签发流程，分析证据再链接该版本
$PY -m regional_brand_guard.certification.cli fixtures/demo.json \
    --database "$workspace/analysis.sqlite3" > "$workspace/baseline.json"

run_estimate() {
    $PY -m regional_brand_guard.inspection_estimates \
        --input "$1" --database "$workspace/analysis.sqlite3" --output "$2" >/dev/null
}

echo "===== 1. 主输入：两阶段不等概率 + 整层未检（new 层必须停止下结论） ====="
run_estimate fixtures/analysis/input.json "$workspace/first.json"
run_estimate fixtures/analysis/input.json "$workspace/replay.json"
$PY scripts/check_analysis.py "$workspace/first.json" "$workspace/replay.json"

echo "===== 2. 原始送检合格率下降，但总体并未显著下降 ====="
run_estimate fixtures/analysis_scenarios/raw_drop_v1.json "$workspace/raw_v1.json"
run_estimate fixtures/analysis_scenarios/raw_drop_v2.json "$workspace/raw_v2.json"

echo "===== 3. 标准变严：只允许共同标准子总体比较 ====="
run_estimate fixtures/analysis_scenarios/standard_change_s1.json "$workspace/std_s1.json"
run_estimate fixtures/analysis_scenarios/standard_change_s2.json "$workspace/std_s2.json"

echo "===== 4. 入样概率缺失：透明包络，不回退简单比例 ====="
run_estimate fixtures/analysis_scenarios/missing_probability.json "$workspace/missing.json"

$PY scripts/demo_analysis_narrative.py "$workspace"
$PY -m json.tool "$workspace/first.json"
