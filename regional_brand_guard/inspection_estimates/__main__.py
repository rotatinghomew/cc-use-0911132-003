"""命令行入口：

    python3 -m regional_brand_guard.inspection_estimates \
        --input <inspection_json> --database <certificate_db> --output <estimate_json>

可选 --predecessor-input 指定前置季度输入，用于跨季度比较；
不指定时自动衔接同季度同总体时点的最近版本（后补检测场景）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from regional_brand_guard.governance.cert_store import DomainError
from regional_brand_guard.inspection_estimates.estimator import EstimationError
from regional_brand_guard.inspection_estimates.store import run_estimate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="两阶段不等概率抽样下的在证批次总体合格率推断"
    )
    parser.add_argument("--input", required=True, help="季度抽检分析输入 JSON")
    parser.add_argument("--database", required=True, help="证书 SQLite 库（与签发流程同库）")
    parser.add_argument("--output", required=True, help="估计结果输出 JSON")
    parser.add_argument(
        "--predecessor-input",
        help="前置季度输入 JSON（跨季度比较；缺省时自动衔接同季度最近版本）",
    )
    args = parser.parse_args(argv)

    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    predecessor = None
    if args.predecessor_input:
        predecessor = json.loads(
            Path(args.predecessor_input).read_text(encoding="utf-8")
        )

    try:
        document = run_estimate(payload, args.database, predecessor)
    except (DomainError, EstimationError, KeyError, ValueError) as exc:
        print(f"估计失败：{exc}", file=sys.stderr)
        return 2

    Path(args.output).write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(document, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
