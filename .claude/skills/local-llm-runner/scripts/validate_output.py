"""
LLM 출력 스키마 검증.
--schema sector_selection | financial_eval
--file   검증할 JSON 파일 경로
검증 실패 시 exit(1), 성공 시 exit(0).
"""
import argparse
import json
import sys
from pathlib import Path


def validate_sector_selection(data: dict) -> list[str]:
    errors = []
    if "selected_sectors" not in data:
        errors.append("최상위 키 'selected_sectors' 없음")
        return errors
    if "analysis_summary" not in data:
        errors.append("최상위 키 'analysis_summary' 없음")

    sectors = data["selected_sectors"]
    if not isinstance(sectors, list):
        errors.append("'selected_sectors'가 배열이 아님")
        return errors
    if not (2 <= len(sectors) <= 3):
        errors.append(f"섹터 수 {len(sectors)}개 — 2~3개 필요")

    required_keys = {"sector_name", "rank", "rationale", "supporting_news", "news_links"}
    for i, s in enumerate(sectors):
        missing = required_keys - set(s.keys())
        if missing:
            errors.append(f"섹터[{i}] 필수 키 누락: {missing}")
        if not s.get("rationale", "").strip():
            errors.append(f"섹터[{i}] rationale가 비어있음")

    return errors


def validate_financial_eval(data: dict) -> list[str]:
    errors = []
    required_ticker_keys = {
        "per", "roe", "pbr", "eps_growth",
        "revenue_growth", "debt_ratio",
        "sector_avg", "z_scores", "evaluation",
    }
    if not isinstance(data, dict):
        errors.append("최상위 구조가 dict가 아님")
        return errors

    for ticker, val in data.items():
        if not isinstance(val, dict):
            errors.append(f"{ticker}: 값이 dict가 아님")
            continue
        missing = required_ticker_keys - set(val.keys())
        if missing:
            errors.append(f"{ticker}: 필수 키 누락 {missing}")
        if not val.get("evaluation", "").strip():
            errors.append(f"{ticker}: evaluation이 비어있음")

    return errors


VALIDATORS = {
    "sector_selection": validate_sector_selection,
    "financial_eval": validate_financial_eval,
}


def main():
    parser = argparse.ArgumentParser(description="LLM 출력 스키마 검증")
    parser.add_argument("--schema", required=True,
                        choices=["sector_selection", "financial_eval"])
    parser.add_argument("--file", required=True, help="검증할 JSON 파일 경로")
    args = parser.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"ERROR: 파일 없음: {path}", file=sys.stderr)
        sys.exit(1)

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"ERROR: JSON 파싱 실패: {e}", file=sys.stderr)
        sys.exit(1)

    errors = VALIDATORS[args.schema](data)
    if errors:
        print("검증 실패:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)

    print("Validation passed.")
    sys.exit(0)


if __name__ == "__main__":
    main()
