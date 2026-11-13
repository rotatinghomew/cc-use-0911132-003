"""总体合格率推断的纯计算内核。

设计选择（与 fixtures/analysis/fields.md 口径一致）：

1. 两阶段入样概率 pi = plant_inclusion_probability * conditional_inclusion_probability。
   输入只给边际入样概率、未给二阶联合概率，因此方差采用泊松（有放回）线性化近似，
   并在 design_diagnostics 中明确写出该近似与有限总体修正缺失；这是该数据形态下
   可识别的保守方差，而不是把样本当作简单随机抽样。
2. 点估计：层内 Hájek 比率（HT 合格数 / HT 样本量），总体按 frame 已知规模事后分层。
3. 区间：logit 变换的正态区间（边界不越 [0,1]），同时保留 clip 后的线性区间。
4. 非应答：主估计仅基于有效应答、显式声明 MAR 假设；另按 study.nonresponse_ranges
   给出应答层内非应答合格假设 q 的敏感性，并对整层无应答/概率缺失给出 [0,1] 包络。
5. 复检按 supersedes 折叠为每个 draw 一个最终结论；晚于 as_of 的事实不采用。
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Mapping

from regional_brand_guard.governance.cert_store import DomainError, fingerprint, stable_json

Z_95 = 1.959963984540054


class EstimationError(DomainError):
    """输入数据不足以支撑估计时抛出（例如总体时点上没有在证批次）。"""


def _parse_ts(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        raise EstimationError(f"非法时间戳：{value!r}")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise EstimationError(f"非法时间戳：{value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _round(value: float | None, digits: int = 6) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(float(value), digits)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _logit_interval(p_hat: float, se: float, n_eff: float) -> dict[str, float | str]:
    """logit 变换区间；信息量不足时给出可辨认的退化/经验界限。"""
    if n_eff <= 1 or not math.isfinite(se):
        return {
            "level": 0.95,
            "method": "logit_wald",
            "lower": 0.0,
            "upper": 1.0,
            "degenerate": True,
            "note": "有效信息量不足，无任何可识别的正态近似区间",
        }
    if se <= 0.0:
        # 层内结论完全一致（全合格/全不合格）：泊松线性化方差为 0，
        # 采用 rule of three 经验单侧界限，而不是伪造一个零宽度区间。
        bound = min(1.0, 3.0 / n_eff)
        if p_hat >= 1.0:
            lower, upper = 1.0 - bound, 1.0
        elif p_hat <= 0.0:
            lower, upper = 0.0, bound
        else:
            lower, upper = p_hat, p_hat
        return {
            "level": 0.95,
            "method": "rule_of_three_degenerate",
            "lower": _round(_clamp01(lower)),
            "upper": _round(_clamp01(upper)),
            "degenerate": True,
            "note": "层内检测结论完全一致、线性化方差为 0，改用 rule of three 经验界限",
        }
    p_safe = min(1.0 - 1e-9, max(1e-9, p_hat))
    logit = math.log(p_safe / (1.0 - p_safe))
    se_logit = se / (p_safe * (1.0 - p_safe))
    lower = 1.0 / (1.0 + math.exp(-(logit - Z_95 * se_logit)))
    upper = 1.0 / (1.0 + math.exp(-(logit + Z_95 * se_logit)))
    return {
        "level": 0.95,
        "method": "logit_wald",
        "lower": _round(_clamp01(lower)),
        "upper": _round(_clamp01(upper)),
        "degenerate": False,
    }


def _final_test_outcome(
    tests: list[Mapping[str, Any]], as_of: datetime
) -> tuple[bool | None, dict[str, Any]]:
    """把同一 draw 的检测链折叠为最终结论。

    返回 (是否合格, 折叠说明)。只采用 available_at <= as_of 的事实；
    supersedes 链上的初检被替代后不再参与计算，复检不会多算一件样本。
    """
    adopted: list[Mapping[str, Any]] = []
    late: list[str] = []
    for test in tests:
        if _parse_ts(test["available_at"]) <= as_of:
            adopted.append(test)
        else:
            late.append(str(test.get("test_id")))
    if not adopted:
        return None, {"adopted_tests": [], "late_tests": late}
    superseded = {str(t.get("supersedes")) for t in adopted if t.get("supersedes")}
    chain_ids = {str(t.get("test_id")) for t in adopted}
    # 只保留未被替代的结论；理论上同一时刻有效结论应唯一，存在多个时以 attempt 最大者为准。
    active = [t for t in adopted if str(t.get("test_id")) not in superseded]
    active.sort(key=lambda t: int(t.get("attempt", 1)))
    chosen = active[-1]
    return bool(chosen["passed"]), {
        "adopted_tests": sorted(chain_ids),
        "superseded_tests": sorted(superseded & chain_ids),
        "active_test_id": str(chosen.get("test_id")),
        "active_attempt": int(chosen.get("attempt", 1)),
        "late_tests": late,
    }


def build_estimate(payload: Mapping[str, Any]) -> dict[str, Any]:
    """根据分析输入构造完整估计文档（不含持久化与 estimate_id 分配）。"""
    as_of = _parse_ts(payload["as_of"])
    study = payload.get("study") or {}
    population_at = _parse_ts(study["population_at"])
    q_grid = [float(x) for x in (study.get("nonresponse_ranges") or [0.0, 1.0])]

    # ---------- 1. 总体框：population_at 时点在证，停证不追溯剔除 ----------
    frame_rows = list(payload.get("frame") or [])
    eligible: list[Mapping[str, Any]] = []
    stopped_in_frame: list[str] = []
    for row in frame_rows:
        start = _parse_ts(row["certificate_start"])
        end_raw = row.get("certificate_end")
        end = _parse_ts(end_raw) if end_raw else None
        if start <= population_at and (end is None or end > population_at):
            eligible.append(row)
    if not eligible:
        raise EstimationError("population_at 时点没有任何在证批次，总体为空，无法估计")

    stop_map: dict[str, datetime] = {}
    late_certificate_changes: list[str] = []
    for chg in (payload.get("certificate_changes") or []):
        batch_id = str(chg["batch_id"])
        if _parse_ts(chg.get("available_at", chg["stopped_at"])) <= as_of:
            stop_map[batch_id] = _parse_ts(chg["stopped_at"])
        else:
            late_certificate_changes.append(batch_id)
    frame_ids = {str(row["batch_id"]) for row in eligible}
    for batch_id, stopped_at in stop_map.items():
        if batch_id in frame_ids and stopped_at > population_at:
            stopped_in_frame.append(batch_id)

    strata = sorted({str(row["stratum"]) for row in eligible})
    frame_size: dict[str, int] = {}
    for row in eligible:
        key = str(row["stratum"])
        frame_size[key] = frame_size.get(key, 0) + int(row.get("population_size", 1))
    n_population = sum(frame_size.values())

    # ---------- 2. 抽样单元折叠 ----------
    draws = list(payload.get("draws") or [])
    tests_by_draw: dict[str, list[Mapping[str, Any]]] = {}
    for test in payload.get("tests") or []:
        tests_by_draw.setdefault(str(test["draw_id"]), []).append(test)

    draw_records: list[dict[str, Any]] = []
    missing_probability_draws: list[str] = []
    orphan_draws: list[str] = []
    late_draws: list[str] = []
    for draw in draws:
        draw_id = str(draw["draw_id"])
        batch_id = str(draw["batch_id"])
        if batch_id not in frame_ids:
            orphan_draws.append(draw_id)
            continue
        if _parse_ts(draw["available_at"]) > as_of:
            late_draws.append(draw_id)
            continue
        p1 = draw.get("plant_inclusion_probability")
        p2 = draw.get("conditional_inclusion_probability")
        pi: float | None = None
        if isinstance(p1, (int, float)) and isinstance(p2, (int, float)) and p1 and p2:
            if 0.0 < float(p1) <= 1.0 and 0.0 < float(p2) <= 1.0:
                pi = float(p1) * float(p2)
        if pi is None:
            # 概率缺失：该单元不能进入加权点估计，只进入透明包络。
            missing_probability_draws.append(draw_id)
        outcome, chain = _final_test_outcome(tests_by_draw.get(draw_id, []), as_of)
        stratum = next(str(r["stratum"]) for r in eligible if str(r["batch_id"]) == batch_id)
        draw_records.append(
            {
                "draw_id": draw_id,
                "batch_id": batch_id,
                "stratum": stratum,
                "pi": pi,
                "response": str(draw.get("response")),
                "outcome": outcome,  # True/False/None(未应答或无可用结论)
                "standard": str(draw.get("standard")),
                "chain": chain,
            }
        )

    # ---------- 3. 分层估计 ----------
    stratum_blocks: dict[str, dict[str, Any]] = {}
    overall_contacts = 0
    overall_respondents = 0
    overall_naive_pass = 0
    overall_naive_n = 0
    nonresponse_by_stratum: dict[str, dict[str, int]] = {}

    for stratum in strata:
        rows = [d for d in draw_records if d["stratum"] == stratum]
        contact = len(rows)
        respondents = [d for d in rows if d["outcome"] is not None and d["pi"] is not None]
        non_contacts = [d for d in rows if d["outcome"] is None]
        missing_pi = [d for d in rows if d["pi"] is None]
        n_resp = len(respondents)
        overall_contacts += contact
        overall_respondents += n_resp
        nonresponse_by_stratum[stratum] = {
            "contacted": contact,
            "responded": n_resp,
            "non_contacts": len(non_contacts),
            "missing_inclusion_probability": len(missing_pi),
            "nonresponse_rate": _round(len(non_contacts) / contact) if contact else None,
        }

        naive_n = sum(1 for d in rows if d["outcome"] is not None)
        naive_pass = sum(1 for d in rows if d["outcome"] is True)
        overall_naive_n += naive_n
        overall_naive_pass += naive_pass

        block: dict[str, Any] = {
            "stratum": stratum,
            "frame_size": frame_size[stratum],
            "frame_share": _round(frame_size[stratum] / n_population),
            "contacted_draws": contact,
            "respondent_draws": n_resp,
            "non_contact_draws": [d["draw_id"] for d in non_contacts],
            "missing_probability_draws": [d["draw_id"] for d in missing_pi],
            "naive_inspection_pass_rate": _round(naive_pass / naive_n) if naive_n else None,
            "naive_inspection_n": naive_n,
        }

        if n_resp == 0:
            block["status"] = "not_estimable"
            block["reason"] = (
                "该层没有任何带有效入样概率的检测结论，停止对点估计与区间下结论"
            )
            block["point_estimate"] = None
            block["confidence_interval"] = None
            block["standard_error"] = None
            stratum_blocks[stratum] = block
            continue

        # Hájek 层内估计（已知一阶 pi 的 HT 比率）
        weights = [1.0 / d["pi"] for d in respondents]
        outcomes = [1.0 if d["outcome"] else 0.0 for d in respondents]
        n_ht = sum(weights)
        t_ht = sum(w * y for w, y in zip(weights, outcomes))
        p_hajek = t_ht / n_ht
        # 泊松线性化方差：Var(比率) ≈ sum w^2 (y-p)^2 / (sum w)^2
        resid_var = sum(w * w * (y - p_hajek) ** 2 for w, y in zip(weights, outcomes))
        var_hajek = resid_var / (n_ht * n_ht)
        se_hajek = math.sqrt(max(0.0, var_hajek))
        # Kish 有效样本量与设计效应（相对同等 n 的 SRS 二项方差）
        n_eff = (sum(weights) ** 2) / sum(w * w for w in weights)
        srs_var = p_hajek * (1.0 - p_hajek) / n_resp
        deff = var_hajek / srs_var if srs_var > 0 else None

        block.update(
            {
                "status": "estimated",
                "estimator": "hajek_ratio_two_stage_ht",
                "point_estimate": _round(p_hajek),
                "standard_error": _round(se_hajek),
                "coefficient_of_variation": _round(se_hajek / p_hajek) if p_hajek > 0 else None,
                "confidence_interval": _logit_interval(p_hajek, se_hajek, n_eff),
                "linear_interval_95": {
                    "lower": _round(_clamp01(p_hajek - Z_95 * se_hajek)),
                    "upper": _round(_clamp01(p_hajek + Z_95 * se_hajek)),
                },
                "ht_sample_size": _round(n_ht, 4),
                "effective_sample_size": _round(n_eff, 4),
                "design_effect_vs_srs": _round(deff, 4) if deff is not None else None,
                "joint_inclusion_probabilities": "not_provided",
            }
        )
        stratum_blocks[stratum] = block

    # ---------- 4. 总体事后分层估计 ----------
    estimable = [s for s in strata if stratum_blocks[s]["status"] == "estimated"]
    unobserved = [s for s in strata if stratum_blocks[s]["status"] != "estimated"]
    observed_frame = sum(frame_size[s] for s in estimable)
    unobserved_frame = n_population - observed_frame

    overall: dict[str, Any] = {
        "scope": "all_certified_batches_at_population_at",
        "frame_size": n_population,
        "strata": strata,
        "estimable_strata": estimable,
        "unobserved_strata": unobserved,
        "estimable_frame_share": _round(observed_frame / n_population),
        "unobserved_frame_share": _round(unobserved_frame / n_population),
    }
    if estimable:
        p_over = sum(frame_size[s] / observed_frame * stratum_blocks[s]["point_estimate"] for s in estimable)
        var_over = sum(
            (frame_size[s] / observed_frame) ** 2 * stratum_blocks[s]["standard_error"] ** 2
            for s in estimable
        )
        se_over = math.sqrt(max(0.0, var_over))
        n_eff_over = sum(stratum_blocks[s]["effective_sample_size"] for s in estimable)
        overall.update(
            {
                "status": "estimated_with_unobserved_envelope" if unobserved else "estimated",
                "estimator": "post_stratified_hajek",
                "point_estimate": _round(p_over),
                "standard_error": _round(se_over),
                "coefficient_of_variation": _round(se_over / p_over) if p_over > 0 else None,
                "confidence_interval": _logit_interval(p_over, se_over, n_eff_over),
                "linear_interval_95": {
                    "lower": _round(_clamp01(p_over - Z_95 * se_over)),
                    "upper": _round(_clamp01(p_over + Z_95 * se_over)),
                },
                "effective_sample_size": _round(n_eff_over, 4),
                "point_estimate_scope": "estimable_strata_only",
            }
        )
    else:
        overall.update(
            {
                "status": "not_estimable",
                "reason": "所有分层均无有效检测，无法对总体合格率下任何点估计结论",
                "point_estimate": None,
                "standard_error": None,
                "confidence_interval": None,
            }
        )

    # ---------- 5. 敏感性界限 ----------
    # 5a. 完全无信息包络：未观测框 + 概率缺失接触单元，合格率只能取 [0,1]
    missing_pi_frame = len({d["batch_id"] for d in draw_records if d["pi"] is None})
    unknown_mass = unobserved_frame + missing_pi_frame
    if estimable:
        known_contribution = sum(frame_size[s] * stratum_blocks[s]["point_estimate"] for s in estimable)
        worst_lower = known_contribution / n_population
        worst_upper = (known_contribution + unknown_mass) / n_population
    else:
        worst_lower, worst_upper = 0.0, 1.0
    full_envelope = {
        "assumption": "未观测部分合格率无任何信息，取 [0,1] 极端界限",
        "unknown_units": unknown_mass,
        "unknown_frame_share": _round(unknown_mass / n_population),
        "lower": _round(_clamp01(worst_lower)),
        "upper": _round(_clamp01(worst_upper)),
    }

    # 5b. 非应答 q 情景（应答层内，按 HT 权重重构；未观测层仍按 [0,1] 包络叠加）
    scenarios: list[dict[str, Any]] = []
    for q in q_grid:
        if not (0.0 <= q <= 1.0):
            continue
        stratum_q: dict[str, float] = {}
        for stratum in estimable:
            rows = [d for d in draw_records if d["stratum"] == stratum]
            respondents = [d for d in rows if d["outcome"] is not None and d["pi"] is not None]
            non_contacts = [d for d in rows if d["outcome"] is None and d["pi"] is not None]
            wr = sum(1.0 / d["pi"] for d in respondents)
            tr = sum((1.0 / d["pi"]) * (1.0 if d["outcome"] else 0.0) for d in respondents)
            wn = sum(1.0 / d["pi"] for d in non_contacts)
            p_q = (tr + q * wn) / (wr + wn) if (wr + wn) > 0 else float("nan")
            stratum_q[stratum] = p_q
        if estimable:
            observed_q = sum(frame_size[s] / observed_frame * stratum_q[s] for s in estimable)
            low = (observed_q * observed_frame) / n_population
            high = (observed_q * observed_frame + unobserved_frame + missing_pi_frame) / n_population
        else:
            observed_q = None
            low, high = 0.0, 1.0
        scenarios.append(
            {
                "nonresponse_pass_assumption_q": q,
                "observed_strata_pass_rate": _round(observed_q),
                "population_lower_with_unobserved_envelope": _round(_clamp01(low)),
                "population_upper_with_unobserved_envelope": _round(_clamp01(high)),
                "stratum_pass_rates_under_q": {s: _round(v) for s, v in stratum_q.items()},
            }
        )

    # ---------- 6. 设计诊断 ----------
    pi_values = sorted({d["pi"] for d in draw_records if d["pi"] is not None})
    standards = {str(s.get("version")): s for s in (payload.get("standards") or [])}
    standards_in_analysis = sorted({d["standard"] for d in draw_records if d["outcome"] is not None})
    retest_chains = []
    for d in draw_records:
        chain = d["chain"]
        if chain.get("superseded_tests"):
            retest_chains.append(
                {
                    "draw_id": d["draw_id"],
                    "superseded_tests": chain["superseded_tests"],
                    "active_test_id": chain["active_test_id"],
                    "active_attempt": chain["active_attempt"],
                }
            )
    late_tests = sorted(
        str(t.get("test_id"))
        for t in (payload.get("tests") or [])
        if _parse_ts(t["available_at"]) > as_of
        and str(t.get("draw_id")) in {d["draw_id"] for d in draw_records}
    )
    # 非应答风险偏向：高层非应答率是否显著高于其余层
    risk_bias_notes: list[str] = []
    high_key = "high" if "high" in nonresponse_by_stratum else None
    if high_key:
        high_rate = nonresponse_by_stratum[high_key]["nonresponse_rate"] or 0.0
        other_rates = [
            v["nonresponse_rate"]
            for k, v in nonresponse_by_stratum.items()
            if k != high_key and v["nonresponse_rate"] is not None
        ]
        if other_rates and high_rate > max(other_rates):
            risk_bias_notes.append(
                f"高风险层非应答率 {high_rate:.0%} 高于其他层，MAR 假设可能不成立；"
                "主估计需与敏感性界限同时阅读，不得单独引用"
            )
    if missing_probability_draws:
        risk_bias_notes.append(
            "存在入样概率缺失的抽检单元，已排除出加权估计并计入无信息包络，"
            "未静默改算简单比例"
        )
    if unobserved:
        risk_bias_notes.append(
            f"分层 {','.join(unobserved)} 无任何有效检测，该层点估计标记为不可估，"
            "总体结论仅以包络形式覆盖该层"
        )

    diagnostics = {
        "method": "two_stage_horvitz_thompson_hajek_with_post_stratification",
        "variance_approximation": (
            "泊松（有放回）线性化近似；输入未提供二阶联合入样概率，"
            "未做有限总体修正，方差偏保守"
        ),
        "joint_inclusion_probability_status": "not_provided_by_sample_plan",
        "joint_inclusion_probability_values": pi_values,
        "population_rule": "frame 中 certificate_start<=population_at 且 certificate_end>population_at 的批次",
        "stopped_certificates_retained": sorted(stopped_in_frame),
        "retest_rule": "supersedes 折叠为同一 draw 的最终结论，复检不计为新样本",
        "retest_chains": retest_chains,
        "late_facts_excluded": late_tests,
        "late_draws_excluded": late_draws,
        "late_certificate_changes_excluded": late_certificate_changes,
        "orphan_draws_excluded": orphan_draws,
        "response_by_stratum": nonresponse_by_stratum,
        "mar_assumption": (
            "主估计假设已入样单元的检测应答在层内与合格状态独立（MAR）；"
            "非应答偏向风险层时该假设存疑，见 risk_bias 与 sensitivity"
        ),
        "risk_bias": risk_bias_notes,
        "standards_observed": standards_in_analysis,
        "standards_catalog": standards,
        "naive_inspection_pass_rate": _round(overall_naive_pass / overall_naive_n)
        if overall_naive_n
        else None,
        "naive_inspection_n": overall_naive_n,
        "naive_method_warning": (
            "naive_inspection_pass_rate 为不合格件数/送检件数的未加权口径，"
            "忽略不等概率与框构成，不能代表在证总体，仅供对照"
        ),
        "contacted_draws": overall_contacts,
        "respondent_draws": overall_respondents,
        "coverage_draws_over_frame": _round(overall_contacts / n_population),
        "coverage_respondents_over_frame": _round(overall_respondents / n_population),
        "source_versions": dict(payload.get("source_versions") or {}),
        "as_of": payload["as_of"],
        "population_at": study["population_at"],
        "input_fingerprint": fingerprint(payload),
    }

    # ---------- 7. 标准口径拆分（供跨季度比较） ----------
    # 每个标准只在出现该标准的分层内估计，再按这些层的框规模归一化；
    # comparable_subpopulation_frame 明确该子总体覆盖范围，不与全总体混用。
    standard_blocks: dict[str, dict[str, Any]] = {}
    for std in standards_in_analysis:
        p_parts = []
        var_parts = []
        frame_used = 0
        for stratum in estimable:
            rows = [
                d
                for d in draw_records
                if d["stratum"] == stratum and d["outcome"] is not None and d["pi"] is not None
            ]
            std_rows = [d for d in rows if d["standard"] == std]
            if not std_rows:
                continue
            weights = [1.0 / d["pi"] for d in std_rows]
            ys = [1.0 if d["outcome"] else 0.0 for d in std_rows]
            nht = sum(weights)
            tht = sum(w * y for w, y in zip(weights, ys))
            p = tht / nht
            v = sum(w * w * (y - p) ** 2 for w, y in zip(weights, ys)) / (nht * nht)
            p_parts.append((frame_size[stratum], p))
            var_parts.append((frame_size[stratum], v))
            frame_used += frame_size[stratum]
        if p_parts:
            p_std = sum(f * p for f, p in p_parts) / frame_used
            se_std = math.sqrt(sum(f * f * v for f, v in var_parts)) / frame_used
            standard_blocks[std] = {
                "status": "estimated",
                "comparable_subpopulation_frame": frame_used,
                "comparable_subpopulation_share_of_total": _round(frame_used / n_population),
                "point_estimate": _round(p_std),
                "standard_error": _round(se_std),
                "confidence_interval": _logit_interval(p_std, se_std, frame_used),
            }
        else:
            standard_blocks[std] = {"status": "not_estimable", "point_estimate": None}

    return {
        "schema_version": 1,
        "population": overall,
        "estimates": {
            "overall": overall,
            "by_stratum": [stratum_blocks[s] for s in strata],
            "by_standard": standard_blocks,
        },
        "design_diagnostics": diagnostics,
        "sensitivity": {
            "full_uninformed_envelope": full_envelope,
            "nonresponse_scenarios": scenarios,
        },
        # comparison 在版本层（store）结合 predecessor 填充；单次运行先给口径自对照。
        "comparison": {
            "predecessor_estimate_id": None,
            "caliber_comparability": "single_run_no_predecessor",
            "naive_vs_design_based": {
                "naive_inspection_pass_rate": diagnostics["naive_inspection_pass_rate"],
                "design_based_pass_rate": overall.get("point_estimate"),
                "difference_design_minus_naive": _round(
                    (overall.get("point_estimate") or 0.0)
                    - (diagnostics["naive_inspection_pass_rate"] or 0.0)
                )
                if overall.get("point_estimate") is not None
                and diagnostics["naive_inspection_pass_rate"] is not None
                else None,
                "interpretation": "两口径回答不同问题，差值反映不等概率加权与框构成，不能互相替代",
            },
            "decomposition": None,
            "headline": {
                "claim_allowed": False,
                "statement": "单期运行无前置估计，不产生质量变化结论；本期合格率见 estimates",
            },
            "headline_claim_allowed": False,
        },
    }


def stable_estimate_id(payload: Mapping[str, Any]) -> str:
    """同一输入重放得到同一 estimate_id。"""
    return "est-" + fingerprint(payload)[:12]
