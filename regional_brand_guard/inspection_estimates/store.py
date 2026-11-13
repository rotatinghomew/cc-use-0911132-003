"""估计结果的版本化持久化、证书库证据链接与跨季度比较。"""
from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Mapping

from regional_brand_guard.governance.cert_store import stable_json, utc_now
from regional_brand_guard.inspection_estimates.estimator import (
    EstimationError,
    _round,
    build_estimate,
    stable_estimate_id,
)

STORE_STAGE = "inspection_estimate"
MATERIALITY = 0.02  # 2 个百分点：小于该差值不宣称质量变化


class EstimateStore:
    """在既有证书 SQLite 库中追加估计版本表，不改动既有表结构与签发流程。"""

    def __init__(self, database: str | Path):
        self.database = str(database)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=20)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS inspection_estimates(
                    estimate_id TEXT PRIMARY KEY,
                    version INTEGER NOT NULL,
                    predecessor_estimate_id TEXT,
                    quarter TEXT NOT NULL,
                    population_at TEXT NOT NULL,
                    as_of TEXT NOT NULL,
                    input_fingerprint TEXT NOT NULL,
                    source_versions TEXT NOT NULL,
                    certificate_evidence TEXT NOT NULL,
                    document TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS inspection_estimates_quarter
                    ON inspection_estimates(quarter, population_at);
                CREATE TABLE IF NOT EXISTS inspection_estimate_events(
                    event_id TEXT PRIMARY KEY,
                    estimate_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            connection.commit()
        finally:
            connection.close()

    def certificate_evidence(self, baseline_case_id: str | None) -> dict[str, Any]:
        """链接同库既有证书运行（sample_plan/certificate 版本证据）。"""
        connection = self._connect()
        try:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            evidence: dict[str, Any] = {
                "database": Path(self.database).name,
                "baseline_case_id": baseline_case_id,
                "certificate_run": None,
                "sample_plan_artifact_hash": None,
                "certificate_decision": None,
            }
            if baseline_case_id and "runs" in tables:
                row = connection.execute(
                    "SELECT run_id, revision, state, artifacts, updated_at FROM runs "
                    "WHERE case_id=? ORDER BY updated_at DESC LIMIT 1",
                    (baseline_case_id,),
                ).fetchone()
                if row:
                    artifacts = json.loads(row["artifacts"])
                    plan = artifacts.get("inspection_sample_plan") or {}
                    case = artifacts.get("certificate_case") or {}
                    evidence.update(
                        {
                            "certificate_run": {
                                "run_id": row["run_id"],
                                "revision": row["revision"],
                                "state": row["state"],
                                "updated_at": row["updated_at"],
                            },
                            "sample_plan_artifact_hash": plan.get("evidence_hash"),
                            "sample_plan_policy_count": plan.get("policy_count"),
                            "certificate_decision": case.get("decision"),
                        }
                    )
            return evidence
        finally:
            connection.close()

    def _latest_version(self, quarter: str, population_at: str) -> int:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT COALESCE(MAX(version), 0) AS v FROM inspection_estimates "
                "WHERE quarter=? AND population_at=?",
                (quarter, population_at),
            ).fetchone()
            return int(row["v"])
        finally:
            connection.close()

    def load(self, estimate_id: str) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT document FROM inspection_estimates WHERE estimate_id=?",
                (estimate_id,),
            ).fetchone()
            return json.loads(row["document"]) if row else None
        finally:
            connection.close()

    def find_by_fingerprint(self, input_fingerprint: str) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT document FROM inspection_estimates WHERE input_fingerprint=?",
                (input_fingerprint,),
            ).fetchone()
            return json.loads(row["document"]) if row else None
        finally:
            connection.close()

    def save(
        self,
        estimate_id: str,
        version: int,
        predecessor_estimate_id: str | None,
        quarter: str,
        population_at: str,
        as_of: str,
        input_fingerprint: str,
        source_versions: Mapping[str, Any],
        certificate_evidence: Mapping[str, Any],
        document: Mapping[str, Any],
    ) -> None:
        connection = self._connect()
        try:
            connection.execute(
                "INSERT INTO inspection_estimates(estimate_id,version,predecessor_estimate_id,"
                "quarter,population_at,as_of,input_fingerprint,source_versions,"
                "certificate_evidence,document,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    estimate_id,
                    version,
                    predecessor_estimate_id,
                    quarter,
                    population_at,
                    as_of,
                    input_fingerprint,
                    stable_json(source_versions),
                    stable_json(certificate_evidence),
                    stable_json(document),
                    utc_now(),
                ),
            )
            connection.execute(
                "INSERT INTO inspection_estimate_events VALUES(?,?,?,?,?)",
                (
                    uuid.uuid4().hex,
                    estimate_id,
                    "estimate_version_recorded",
                    stable_json(
                        {
                            "version": version,
                            "predecessor_estimate_id": predecessor_estimate_id,
                            "reason": "new_or_superseded_inspection_evidence",
                        }
                    ),
                    utc_now(),
                ),
            )
            connection.commit()
        finally:
            connection.close()


def _quarter_of(payload: Mapping[str, Any]) -> str:
    quarters = {str(row.get("quarter")) for row in payload.get("frame") or [] if row.get("quarter")}
    if len(quarters) == 1:
        return next(iter(quarters))
    return "multi"


def build_comparison(
    current: Mapping[str, Any],
    predecessor: Mapping[str, Any] | None,
    current_payload: Mapping[str, Any],
    predecessor_payload: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """跨季度/跨版本比较：区分产品表现、产品构成与标准口径。"""
    base = current["comparison"]
    if predecessor is None:
        return base

    cur_overall = current["population"]
    pre_overall = predecessor["population"]
    cur_p = cur_overall.get("point_estimate")
    pre_p = pre_overall.get("point_estimate")

    cur_std = set(current["design_diagnostics"]["standards_observed"])
    pre_std = set(predecessor["design_diagnostics"]["standards_observed"])
    common_std = sorted(cur_std & pre_std)
    caliber_changed = cur_std != pre_std

    decomposition: dict[str, Any] | None = None
    headline = {
        "claim_allowed": False,
        "statement": "证据不足或口径不可比，不得直接宣称质量提升/下降",
    }

    if cur_p is not None and pre_p is not None:
        total_change = cur_p - pre_p
        cur_share = {b["stratum"]: b["frame_share"] for b in current["estimates"]["by_stratum"]}
        pre_share = {b["stratum"]: b["frame_share"] for b in predecessor["estimates"]["by_stratum"]}
        cur_rate = {
            b["stratum"]: b["point_estimate"]
            for b in current["estimates"]["by_stratum"]
            if b["status"] == "estimated"
        }
        pre_rate = {
            b["stratum"]: b["point_estimate"]
            for b in predecessor["estimates"]["by_stratum"]
            if b["status"] == "estimated"
        }
        common_strata = sorted(set(cur_share) & set(pre_share) & set(cur_rate) & set(pre_rate))

        # 三因素分解（公共层上做，未观测层不参与点估计分解）
        performance = sum(
            pre_share[s] * (cur_rate[s] - pre_rate[s]) for s in common_strata
        )
        composition = sum(
            (cur_share[s] - pre_share[s]) * pre_rate[s] for s in common_strata
        )
        interaction = sum(
            (cur_share[s] - pre_share[s]) * (cur_rate[s] - pre_rate[s])
            for s in common_strata
        )
        explained = performance + composition + interaction
        coverage_cur = cur_overall.get("estimable_frame_share") or 0.0
        coverage_pre = pre_overall.get("estimable_frame_share") or 0.0
        decomposition = {
            "total_change_design_based": _round(total_change),
            "performance_effect_common_strata": _round(performance),
            "composition_effect": _round(composition),
            "interaction_effect": _round(interaction),
            "explained_on_common_strata": _round(explained),
            "common_strata": common_strata,
            "unobserved_change_note": (
                "两期未观测层份额不同，未观测部分只能进入敏感性包络，不参与点估计分解"
                if abs(coverage_cur - coverage_pre) > 1e-9
                else "两期可估层覆盖一致"
            ),
        }

        # 显著性：差值与合并标准误比较
        se_diff = (
            (cur_overall.get("standard_error") or 0.0) ** 2
            + (pre_overall.get("standard_error") or 0.0) ** 2
        ) ** 0.5
        significant = abs(total_change) > 1.96 * se_diff
        material = abs(total_change) >= MATERIALITY
        decomposition["standard_error_of_change"] = _round(se_diff)
        decomposition["significant_at_95"] = bool(significant)
        decomposition["materiality_threshold"] = MATERIALITY

        if caliber_changed:
            # 标准变严：只允许在共同标准子总体上比较
            cur_by_std = current["estimates"]["by_standard"]
            pre_by_std = predecessor["estimates"]["by_standard"]
            comparable = {}
            for std in common_std:
                c = cur_by_std.get(std) or {}
                p = pre_by_std.get(std) or {}
                if c.get("point_estimate") is not None and p.get("point_estimate") is not None:
                    comparable[std] = {
                        "predecessor": p["point_estimate"],
                        "current": c["point_estimate"],
                        "change": _round(c["point_estimate"] - p["point_estimate"]),
                    }
            headline = {
                "claim_allowed": False,
                "statement": (
                    "检测标准口径发生变化，总体两数不可直接比较；仅允许引用共同标准 "
                    f"{common_std or '无'} 子总体结果，禁止写成质量提升"
                ),
            }
            decomposition["caliber_effect"] = "标准集合变化：" + f"{sorted(pre_std)} -> {sorted(cur_std)}"
            decomposition["comparable_subpopulation_by_standard"] = comparable
        elif significant and material:
            direction = "提升" if total_change > 0 else "下降"
            headline = {
                "claim_allowed": True,
                "statement": f"同口径下总体合格率{direction} {abs(total_change):.2%}，显著且超过重要性阈值",
            }
        else:
            headline = {
                "claim_allowed": False,
                "statement": (
                    f"同口径变化 {total_change:+.2%} 未通过显著性/重要性检验，"
                    "结论应表述为未发现显著变化"
                ),
            }

    return {
        "predecessor_estimate_id": predecessor.get("estimate_id"),
        "predecessor_versions": predecessor.get("versions"),
        "caliber_comparability": "same_standard"
        if not caliber_changed
        else "standard_changed_use_comparable_subpopulation_only",
        "standards_predecessor": sorted(pre_std),
        "standards_current": sorted(cur_std),
        "common_standards": common_std,
        "decomposition": decomposition,
        "headline": headline,
        "naive_vs_design_based": base["naive_vs_design_based"],
        "headline_claim_allowed": headline["claim_allowed"],
    }


def run_estimate(
    payload: Mapping[str, Any],
    database: str | Path,
    predecessor_payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """完整入口：计算、版本化、证书证据链接、（可选）跨版本比较并落库。"""
    document = build_estimate(payload)
    estimate_id = stable_estimate_id(payload)
    input_fingerprint = document["design_diagnostics"]["input_fingerprint"]

    store = EstimateStore(database)
    existing = store.find_by_fingerprint(input_fingerprint)
    if existing is not None:
        replayed = dict(existing)
        replayed["replayed"] = True
        return replayed

    quarter = _quarter_of(payload)
    population_at = str(payload["study"]["population_at"])
    as_of = str(payload["as_of"])
    certificate_evidence = store.certificate_evidence(payload.get("baseline_case_id"))

    predecessor_doc = None
    if predecessor_payload is not None:
        pre_id = stable_estimate_id(predecessor_payload)
        predecessor_doc = store.load(pre_id)
        if predecessor_doc is None:
            raise EstimationError(
                f"前置估计 {pre_id} 不在证书库中，请先运行前置季度输入"
            )
    else:
        # 同季度同总体时点的最近版本作为天然前置（后补检测场景）
        connection = store._connect()
        try:
            row = connection.execute(
                "SELECT estimate_id, document FROM inspection_estimates "
                "WHERE quarter=? AND population_at=? ORDER BY version DESC LIMIT 1",
                (quarter, population_at),
            ).fetchone()
        finally:
            connection.close()
        if row:
            predecessor_doc = json.loads(row["document"])

    version = store._latest_version(quarter, population_at) + 1
    document["estimate_id"] = estimate_id
    document["versions"] = {
        "estimate_version": version,
        "predecessor_estimate_id": predecessor_doc.get("estimate_id") if predecessor_doc else None,
        "quarter": quarter,
        "created_at": utc_now(),
        "versioning_rule": "输入指纹变化即新版本；同输入重放复用同一 estimate_id",
    }
    document["evidence"] = {
        "sample_plan_source_versions": document["design_diagnostics"]["source_versions"],
        "input_fingerprint": input_fingerprint,
        "certificate": certificate_evidence,
        "standards_catalog": document["design_diagnostics"]["standards_catalog"],
    }
    document["comparison"] = build_comparison(
        document, predecessor_doc, payload, predecessor_payload
    )

    store.save(
        estimate_id=estimate_id,
        version=version,
        predecessor_estimate_id=document["versions"]["predecessor_estimate_id"],
        quarter=quarter,
        population_at=population_at,
        as_of=as_of,
        input_fingerprint=input_fingerprint,
        source_versions=document["design_diagnostics"]["source_versions"],
        certificate_evidence=certificate_evidence,
        document=document,
    )
    document["replayed"] = False
    return document
