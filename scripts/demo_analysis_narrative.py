"""读取 feature_demo 各输出，核验并打印业务叙述所需的关键结论。"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def load(directory: Path, name: str) -> dict:
    return json.loads((directory / name).read_text(encoding="utf-8"))


def line(text: str) -> None:
    print(text)


def main() -> int:
    directory = Path(sys.argv[1])
    main_doc = load(directory, "first.json")
    raw_v1 = load(directory, "raw_v1.json")
    raw_v2 = load(directory, "raw_v2.json")
    std_s2 = load(directory, "std_s2.json")
    missing = load(directory, "missing.json")

    line("\n========== 业务叙述核验 ==========")

    # 1. 整层未检必须停止下结论
    new_block = next(
        b for b in main_doc["estimates"]["by_stratum"] if b["stratum"] == "new"
    )
    assert new_block["status"] == "not_estimable", "new 层必须标记不可估"
    line(f"[整层未检] new 层状态={new_block['status']}：{new_block['reason']}")
    env = main_doc["sensitivity"]["full_uninformed_envelope"]
    line(
        f"[整层未检] 总体只能给包络 {env['lower']:.4f} ~ {env['upper']:.4f}，"
        f"未观测份额 {env['unknown_frame_share']:.2%}"
    )

    # 2. 原始送检合格率下降而总体未显著下降
    naive_v1 = raw_v1["design_diagnostics"]["naive_inspection_pass_rate"]
    naive_v2 = raw_v2["design_diagnostics"]["naive_inspection_pass_rate"]
    assert naive_v2 < naive_v1
    comp = raw_v2["comparison"]
    assert not comp["headline"]["claim_allowed"]
    line(
        f"[原始口径下降] 未加权送检合格率 {naive_v1:.2%} -> {naive_v2:.2%}"
    )
    d = comp["decomposition"]
    line(
        f"[总体未下降] 设计口径变化 {d['total_change_design_based']:+.2%}，"
        f"95% 显著={d['significant_at_95']}（SE {d['standard_error_of_change']:.2%}），"
        f"结论：{comp['headline']['statement']}"
    )

    # 3. 标准变严
    std_comp = std_s2["comparison"]
    assert std_comp["caliber_comparability"].startswith("standard_changed")
    comparable = std_comp["decomposition"]["comparable_subpopulation_by_standard"]
    line(
        f"[标准口径] {std_comp['standards_predecessor']} -> "
        f"{std_comp['standards_current']}，{std_comp['headline']['statement']}"
    )
    line(f"[标准口径] 共同标准可比子总体：{json.dumps(comparable, ensure_ascii=False)}")

    # 4. 概率缺失
    risk = [n for n in missing["design_diagnostics"]["risk_bias"] if "入样概率缺失" in n]
    assert risk
    m_env = missing["sensitivity"]["full_uninformed_envelope"]
    line(
        f"[概率缺失] {risk[0]}；无信息包络 "
        f"{m_env['lower']:.4f} ~ {m_env['upper']:.4f}"
    )

    # 5. 证据版本
    evidence = main_doc["evidence"]
    line(
        f"[证据链接] sample_plan 版本={evidence['sample_plan_source_versions']}，"
        f"证书运行={evidence['certificate']['certificate_run']['run_id'][:8]}…，"
        f"签发决策={evidence['certificate']['certificate_decision']}"
    )
    line(
        f"[版本化] estimate_id={main_doc['estimate_id']}，"
        f"estimate_version={main_doc['versions']['estimate_version']}，"
        f"重放 estimate_id 一致性已由 check_analysis.py 核验"
    )
    line("========== 业务叙述核验通过 ==========\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
