from __future__ import annotations
import argparse
import json
from regional_brand_guard.service import run_fixture

def main() -> int:
    parser = argparse.ArgumentParser(description='海宁区域品牌准入与持续合规平台')
    parser.add_argument("fixture")
    parser.add_argument("--database", required=True)
    args = parser.parse_args()
    result = run_fixture(args.fixture, args.database)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
