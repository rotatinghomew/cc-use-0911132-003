"""生成 feature_demo 所需的补充演练输入（合成数据）。

场景 A（raw_drop）：同一总体两个 as_of 快照。v2 高风险层新增一次失败初检，
未加权送检合格率明显下降；但高风险层在框中占比小且为高入样概率层，
事后分层总体估计不构成显著下降。
场景 B（standard_change）：标准 s1 -> s2 变严，同样本在 s2 下失败增加，
演示口径不可比与共同标准子总体比较。
场景 C（missing_probability）：入样概率缺失，验证不静默回退简单比例。
"""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "fixtures" / "analysis_scenarios"


def frame_row(batch_id: str, stratum: str, plant: str) -> dict:
    return {
        "batch_id": batch_id,
        "stratum": stratum,
        "plant": plant,
        "certificate_start": "2026-08-01T00:00:00+00:00",
        "certificate_end": "2026-11-09T00:00:00+00:00",
        "quarter": "Q3",
        "population_size": 1,
    }


def draw(draw_id: str, batch_id: str, p2: float, standard: str = "s1") -> dict:
    return {
        "draw_id": draw_id,
        "batch_id": batch_id,
        "plant_inclusion_probability": 0.5,
        "conditional_inclusion_probability": p2,
        "available_at": "2026-09-05T00:00:00+00:00",
        "response": "tested",
        "standard": standard,
    }


def test(test_id: str, draw_id: str, passed: bool, standard: str,
         available_at: str = "2026-09-10T00:00:00+00:00", attempt: int = 1,
         supersedes: str | None = None) -> dict:
    row = {
        "test_id": test_id,
        "draw_id": draw_id,
        "attempt": attempt,
        "passed": passed,
        "available_at": available_at,
        "standard": standard,
    }
    if supersedes:
        row["supersedes"] = supersedes
    return row


def base_payload(extra_versions: str = "r1") -> dict:
    return {
        "schema_version": 1,
        "as_of": "2026-09-30T00:00:00+00:00",
        "source_versions": {"operations": extra_versions, "reference": "r1"},
        "frame": [],
        "draws": [],
        "tests": [],
        "certificate_changes": [],
        "standards": [
            {"version": "s1", "effective_at": "2026-08-01T00:00:00+00:00"},
            {"version": "s2", "effective_at": "2026-10-20T00:00:00+00:00"},
        ],
        "study": {
            "population_at": "2026-08-31T00:00:00+00:00",
            "nonresponse_ranges": [0.0, 0.25, 0.5, 1.0],
        },
        "baseline_case_id": "regional_brand_guard-demo",
    }


def scenario_a() -> None:
    # 框：high 10 批（plant-a0..a3，每厂多批），ordinary 30 批（plant-b0..b9）
    p = base_payload("r1")
    high_batches = [f"h-{i}" for i in range(10)]
    ord_batches = [f"o-{i}" for i in range(30)]
    for i, b in enumerate(high_batches):
        p["frame"].append(frame_row(b, "high", f"plant-a{i % 4}"))
    for i, b in enumerate(ord_batches):
        p["frame"].append(frame_row(b, "ordinary", f"plant-b{i % 10}"))

    high_draw_batches = high_batches[:8]   # high 接触 8 件，p2=0.8 -> pi=0.4
    ord_draw_batches = ord_batches[:10]    # ordinary 接触 10 件，p2=0.2 -> pi=0.1
    for i, b in enumerate(high_draw_batches):
        p["draws"].append(draw(f"d-h{i}", b, 0.8))
    for i, b in enumerate(ord_draw_batches):
        p["draws"].append(draw(f"d-o{i}", b, 0.2))
    # v1：18 件全合格
    for i in range(8):
        p["tests"].append(test(f"t-h{i}", f"d-h{i}", True, "s1"))
    for i in range(10):
        p["tests"].append(test(f"t-o{i}", f"d-o{i}", True, "s1"))
    (OUT / "raw_drop_v1.json").write_text(
        json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # v2：高风险层 h7 初检失败（后补证据，as_of 后移），其余不变
    p2 = json.loads(json.dumps(p))
    p2["as_of"] = "2026-10-30T00:00:00+00:00"
    p2["source_versions"] = {"operations": "r2", "reference": "r1"}
    p2["tests"] = [t for t in p2["tests"] if t["draw_id"] != "d-h7"]
    p2["tests"].append(
        test("t-h7-fail", "d-h7", False, "s1",
             available_at="2026-10-22T00:00:00+00:00")
    )
    (OUT / "raw_drop_v2.json").write_text(
        json.dumps(p2, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def scenario_b() -> None:
    # 同一抽样，s1 全合格；s2 变严后 ordinary 层 3 件失败、high 层 1 件失败
    p1 = base_payload("r1")
    high_batches = [f"sb-h-{i}" for i in range(6)]
    ord_batches = [f"sb-o-{i}" for i in range(12)]
    for i, b in enumerate(high_batches):
        p1["frame"].append(frame_row(b, "high", f"plant-c{i % 3}"))
    for i, b in enumerate(ord_batches):
        p1["frame"].append(frame_row(b, "ordinary", f"plant-d{i % 6}"))
    for i, b in enumerate(high_batches):
        p1["draws"].append(draw(f"sd-h{i}", b, 0.8, standard="s1"))
    for i, b in enumerate(ord_batches):
        p1["draws"].append(draw(f"sd-o{i}", b, 0.2, standard="s1"))
    for i in range(6):
        p1["tests"].append(test(f"st-h{i}", f"sd-h{i}", True, "s1"))
    for i in range(12):
        p1["tests"].append(test(f"st-o{i}", f"sd-o{i}", True, "s1"))
    (OUT / "standard_change_s1.json").write_text(
        json.dumps(p1, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    p2 = json.loads(json.dumps(p1))
    p2["as_of"] = "2026-11-15T00:00:00+00:00"
    p2["source_versions"] = {"operations": "r3", "reference": "r2"}
    # 二期同时存在 s1 复检组（共同标准可比子总体）与 s2 新口径组：
    # 前 2 个 high draw 与前 2 个 ordinary draw 仍按 s1 检测，其余切换到变严的 s2。
    s1_draws = {"sd-h0", "sd-h1", "sd-o0", "sd-o1"}
    for d in p2["draws"]:
        d["standard"] = "s1" if d["draw_id"] in s1_draws else "s2"
    p2["tests"] = []
    for i in range(6):
        std = "s1" if i < 2 else "s2"
        p2["tests"].append(
            test(f"st2-h{i}", f"sd-h{i}", i != 5, std,
                 available_at="2026-11-05T00:00:00+00:00")
        )
    for i in range(12):
        std = "s1" if i < 2 else "s2"
        p2["tests"].append(
            test(f"st2-o{i}", f"sd-o{i}", i not in (2, 5, 9), std,
                 available_at="2026-11-05T00:00:00+00:00")
        )
    (OUT / "standard_change_s2.json").write_text(
        json.dumps(p2, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def scenario_c() -> None:
    # 一件 draw 缺失 conditional_inclusion_probability：必须排除加权并给包络
    p = base_payload("r1")
    for i in range(4):
        p["frame"].append(frame_row(f"m-h-{i}", "high", f"plant-e{i}"))
    for i in range(8):
        p["frame"].append(frame_row(f"m-o-{i}", "ordinary", f"plant-f{i % 4}"))
    for i in range(4):
        row = draw(f"md-h{i}", f"m-h-{i}", 0.8)
        if i == 3:
            row["conditional_inclusion_probability"] = None
        p["draws"].append(row)
    for i in range(8):
        p["draws"].append(draw(f"md-o{i}", f"m-o-{i}", 0.2))
    for i in range(4):
        p["tests"].append(test(f"mt-h{i}", f"md-h{i}", i != 2, "s1"))
    for i in range(8):
        p["tests"].append(test(f"mt-o{i}", f"md-o{i}", True, "s1"))
    (OUT / "missing_probability.json").write_text(
        json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    scenario_a()
    scenario_b()
    scenario_c()
    print(f"wrote scenarios to {OUT}")


if __name__ == "__main__":
    main()
