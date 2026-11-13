"""inspection_estimates 的统计口径与边界行为测试。

覆盖：两阶段 HT/Hájek 加权、事后分层、复检折叠、停证保留、
整层不可估、非应答敏感性、概率缺失保护、as_of 晚到事实、
版本化重放与跨季度分解/口径不可比。
"""
from __future__ import annotations

import copy
import json
import math
import tempfile
import unittest
from pathlib import Path

from regional_brand_guard.inspection_estimates.estimator import build_estimate
from regional_brand_guard.inspection_estimates.store import run_estimate

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures" / "analysis" / "input.json"
)
SCENARIOS = (
    Path(__file__).resolve().parents[1]
    / "fixtures" / "analysis_scenarios"
)


def load(name: str, scenarios: bool = False) -> dict:
    base = SCENARIOS if scenarios else FIXTURE.parent
    target = base / name if scenarios else base / "input.json"
    return json.loads(target.read_text(encoding="utf-8"))


class EstimatorCoreTests(unittest.TestCase):
    def setUp(self):
        self.data = load("input.json")

    def test_population_keeps_stopped_batch_and_uses_frame_size(self):
        doc = build_estimate(self.data)
        # 60 个常规框 + 1 个 new 层；batch-15 抽样后停证仍在总体内
        self.assertEqual(doc["population"]["frame_size"], 61)
        self.assertIn("batch-15", doc["design_diagnostics"]["stopped_certificates_retained"])

    def test_retest_is_folded_not_double_counted(self):
        doc = build_estimate(self.data)
        chains = doc["design_diagnostics"]["retest_chains"]
        self.assertEqual(len(chains), 1)
        self.assertEqual(chains[0]["active_test_id"], "retest-15")
        self.assertEqual(chains[0]["superseded_tests"], ["test-15"])
        # draw-15 最终合格：high 层 6 个应答全部合格
        high = next(b for b in doc["estimates"]["by_stratum"] if b["stratum"] == "high")
        self.assertEqual(high["point_estimate"], 1.0)
        self.assertEqual(high["respondent_draws"], 6)

    def test_two_stage_weights_differ_from_simple_proportion(self):
        # ordinary 层 pi=0.1，high 层 pi=0.4；Hájek 层内估计等于应答比例，
        # 但总体事后分层必须使用框规模（20 high / 40 ordinary / 1 new），
        # 而不是送检件数占比。
        doc = build_estimate(self.data)
        overall = doc["population"]
        self.assertEqual(overall["status"], "estimated_with_unobserved_envelope")
        self.assertAlmostEqual(overall["point_estimate"], (20 * 1.0 + 40 * (10 / 12)) / 60, places=6)
        # naive 口径必须同时暴露并带警告
        self.assertAlmostEqual(doc["design_diagnostics"]["naive_inspection_pass_rate"], 16 / 18, places=6)
        self.assertIn("不能代表在证总体", doc["design_diagnostics"]["naive_method_warning"])

    def test_empty_stratum_is_not_estimable_and_blocks_overall_claim(self):
        doc = build_estimate(self.data)
        new = next(b for b in doc["estimates"]["by_stratum"] if b["stratum"] == "new")
        self.assertEqual(new["status"], "not_estimable")
        self.assertIsNone(new["point_estimate"])
        self.assertIn("new", doc["population"]["unobserved_strata"])
        # 无信息包络必须覆盖 new 层的整单位不确定性
        env = doc["sensitivity"]["full_uninformed_envelope"]
        self.assertEqual(env["unknown_units"], 1)
        self.assertLess(env["lower"], doc["population"]["point_estimate"])
        self.assertGreater(env["upper"], doc["population"]["point_estimate"])

    def test_nonresponse_risk_bias_flagged_and_scenarios_bracket(self):
        doc = build_estimate(self.data)
        self.assertTrue(
            any("高风险层非应答率" in note for note in doc["design_diagnostics"]["risk_bias"])
        )
        qs = [s["nonresponse_pass_assumption_q"] for s in doc["sensitivity"]["nonresponse_scenarios"]]
        self.assertEqual(qs, [0.0, 0.25, 0.5, 1.0])
        rates = [s["observed_strata_pass_rate"] for s in doc["sensitivity"]["nonresponse_scenarios"]]
        self.assertEqual(rates, sorted(rates))  # q 越大，假定合格率越高，估计单调

    def test_late_fact_excluded_by_as_of(self):
        data = copy.deepcopy(self.data)
        # 把复检结论推到 as_of 之后：draw-15 回退为初检失败
        for t in data["tests"]:
            if t["test_id"] == "retest-15":
                t["available_at"] = "2026-11-25T00:00:00+00:00"
        doc = build_estimate(data)
        self.assertIn("retest-15", doc["design_diagnostics"]["late_facts_excluded"])
        high = next(b for b in doc["estimates"]["by_stratum"] if b["stratum"] == "high")
        self.assertAlmostEqual(high["point_estimate"], 5 / 6, places=6)
        self.assertEqual(doc["design_diagnostics"]["retest_chains"], [])

    def test_missing_inclusion_probability_never_silent_simple_ratio(self):
        data = load("missing_probability.json", scenarios=True)
        doc = build_estimate(data)
        high = next(b for b in doc["estimates"]["by_stratum"] if b["stratum"] == "high")
        self.assertEqual(high["missing_probability_draws"], ["md-h3"])
        # 仅 3 个有权重应答（2 合格 1 失败），缺失单元不参与点估计
        self.assertEqual(high["respondent_draws"], 3)
        self.assertAlmostEqual(high["point_estimate"], 2 / 3, places=6)
        env = doc["sensitivity"]["full_uninformed_envelope"]
        self.assertEqual(env["unknown_units"], 1)
        self.assertTrue(
            any("入样概率缺失" in note for note in doc["design_diagnostics"]["risk_bias"])
        )

    def test_entirely_unobserved_population_is_not_estimable(self):
        data = copy.deepcopy(self.data)
        data["draws"] = []
        data["tests"] = []
        doc = build_estimate(data)
        self.assertEqual(doc["population"]["status"], "not_estimable")
        self.assertIsNone(doc["population"]["point_estimate"])
        self.assertEqual(
            doc["sensitivity"]["full_uninformed_envelope"]["lower"], 0.0
        )
        self.assertEqual(
            doc["sensitivity"]["full_uninformed_envelope"]["upper"], 1.0
        )

    def test_weighted_design_diverges_from_pooled_naive_in_scenario(self):
        # raw_drop_v2：高风险层高权重小框、普通层低权重大框，
        # 加权总体估计必须与未加权送检比例不同，证明没有退化成简单比例。
        data = load("raw_drop_v2.json", scenarios=True)
        doc = build_estimate(data)
        naive = doc["design_diagnostics"]["naive_inspection_pass_rate"]
        design = doc["population"]["point_estimate"]
        self.assertNotAlmostEqual(naive, design, places=4)
        self.assertAlmostEqual(naive, 17 / 18, places=6)
        # 事后分层：high 框 10、ordinary 框 30，high 估计 7/8、ordinary=1
        self.assertAlmostEqual(design, (10 * (7 / 8) + 30 * 1.0) / 40, places=6)

    def test_variance_uses_poisson_linearization_not_srs(self):
        doc = build_estimate(self.data)
        ordinary = next(b for b in doc["estimates"]["by_stratum"] if b["stratum"] == "ordinary")
        # 手工复算：pi=0.1，w=10；10 合格 2 失败，p=10/12
        w = 10.0
        p = 10 / 12
        n_ht = 12 * w
        resid = 10 * (w * w * (1 - p) ** 2) + 2 * (w * w * (0 - p) ** 2)
        se = math.sqrt(resid) / n_ht
        self.assertAlmostEqual(ordinary["standard_error"], se, places=6)
        self.assertEqual(
            doc["design_diagnostics"]["joint_inclusion_probability_status"],
            "not_provided_by_sample_plan",
        )

    def test_all_pass_stratum_uses_rule_of_three_not_fake_interval(self):
        doc = build_estimate(self.data)
        high = next(b for b in doc["estimates"]["by_stratum"] if b["stratum"] == "high")
        ci = high["confidence_interval"]
        self.assertTrue(ci["degenerate"])
        self.assertEqual(ci["method"], "rule_of_three_degenerate")
        self.assertLess(ci["lower"], 1.0)


class VersioningAndComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "db.sqlite3"

    def test_replay_reuses_estimate_id_and_versions_increment(self):
        data = load("input.json")
        first = run_estimate(data, self.db)
        replay = run_estimate(copy.deepcopy(data), self.db)
        self.assertEqual(first["estimate_id"], replay["estimate_id"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(first["versions"]["estimate_version"], 1)

        v1 = load("raw_drop_v1.json", scenarios=True)
        v2 = load("raw_drop_v2.json", scenarios=True)
        r1 = run_estimate(v1, self.db)
        r2 = run_estimate(v2, self.db)
        self.assertNotEqual(r1["estimate_id"], r2["estimate_id"])
        self.assertEqual(r2["versions"]["estimate_version"], r1["versions"]["estimate_version"] + 1)
        self.assertEqual(r2["versions"]["predecessor_estimate_id"], r1["estimate_id"])

    def test_raw_drop_naive_falls_but_population_does_not_decline_significantly(self):
        v1 = load("raw_drop_v1.json", scenarios=True)
        v2 = load("raw_drop_v2.json", scenarios=True)
        r1 = run_estimate(v1, self.db)
        r2 = run_estimate(v2, self.db)
        self.assertGreater(
            r1["design_diagnostics"]["naive_inspection_pass_rate"],
            r2["design_diagnostics"]["naive_inspection_pass_rate"],
        )
        comp = r2["comparison"]
        self.assertFalse(comp["headline"]["claim_allowed"])
        self.assertFalse(comp["decomposition"]["significant_at_95"])
        self.assertIn("未发现显著变化", comp["headline"]["statement"])

    def test_standard_change_blocks_headline_and_keeps_comparable_subpopulation(self):
        s1 = load("standard_change_s1.json", scenarios=True)
        s2 = load("standard_change_s2.json", scenarios=True)
        run_estimate(s1, self.db)
        r2 = run_estimate(s2, self.db)
        comp = r2["comparison"]
        self.assertEqual(comp["caliber_comparability"],
                         "standard_changed_use_comparable_subpopulation_only")
        self.assertFalse(comp["headline"]["claim_allowed"])
        self.assertIn("禁止写成质量提升", comp["headline"]["statement"])
        self.assertIn("s1", comp["decomposition"]["comparable_subpopulation_by_standard"])
        # 同标准 s1 子总体两期均为全合格
        self.assertEqual(
            comp["decomposition"]["comparable_subpopulation_by_standard"]["s1"]["change"], 0.0
        )

    def test_evidence_links_sample_plan_and_certificate_versions(self):
        # 先写入证书基线（与 feature_demo 相同的库顺序）
        from regional_brand_guard.service import run_fixture
        demo = Path(__file__).resolve().parents[1] / "fixtures" / "demo.json"
        run_fixture(demo, self.db)
        doc = run_estimate(load("input.json"), self.db)
        cert = doc["evidence"]["certificate"]
        self.assertEqual(cert["baseline_case_id"], "regional_brand_guard-demo")
        self.assertIsNotNone(cert["certificate_run"])
        self.assertIsNotNone(cert["sample_plan_artifact_hash"])
        self.assertEqual(doc["evidence"]["sample_plan_source_versions"]["operations"], "r1")


if __name__ == "__main__":
    unittest.main()
