import argparse
import json
from pathlib import Path

from app.services.analysis_intent_quality_service import AnalysisIntentQualityService


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Score saved LLM IntentDraft predictions against a regression dataset."
    )
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--predictions", required=True, type=Path)
    parser.add_argument("--minimum-exact-match", type=float, default=0.85)
    args = parser.parse_args()

    cases = json.loads(args.dataset.read_text(encoding="utf-8"))
    predictions = json.loads(args.predictions.read_text(encoding="utf-8"))
    report = AnalysisIntentQualityService().evaluate(cases, predictions)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if (
        report["missing_case_ids"]
        or report["exact_match_rate"] < args.minimum_exact_match
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
