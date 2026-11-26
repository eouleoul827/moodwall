"""Run claude_bundle/eval_set.csv through the Claude analyzer (real API calls).

docker compose -f compose.yaml -f compose.claude.yaml run --rm worker python -m inference.claude_eval
About 40 short calls. The local profanity lexicon is skipped here, so this
measures Claude alone.
"""

import csv
import json
from pathlib import Path

from inference.claude_analyzer import create_analyzer
from inference.contracts import AnalysisInput
from inference.loader import AnalyzerContext

BUNDLE = Path(__file__).with_name("claude_bundle")
LABELS = {
    "슬픔": "SADNESS", "기쁨": "JOY", "설렘": "EXCITEMENT", "응원": "SUPPORT",
    "축하": "CELEBRATION", "피곤": "TIRED", "화남": "ANGER", "일반": None, "부적절": "BLOCK",
}


def main() -> None:
    manifest_path = BUNDLE / "manifest.json"
    analyzer = create_analyzer(
        AnalyzerContext(manifest_path, json.loads(manifest_path.read_text(encoding="utf-8")), "development")
    )
    ok = total = 0
    with open(BUNDLE / "eval_set.csv", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            expected = LABELS[row["label"]]
            try:
                result = analyzer.analyze(AnalysisInput(text=row["text"]))
                got = result.emotion.label if result.emotion else result.safety.verdict
                if result.safety.verdict != "ALLOW":
                    got += "/" + ",".join(result.safety.reason_codes)
            except Exception as error:  # noqa: BLE001
                got = "ERROR:" + getattr(error, "error_code", type(error).__name__)
                got += " | " + str(analyzer.last_debug).replace("\n", " ")
            if expected is None:
                passed = not got.startswith(("BLOCK", "UNCERTAIN", "ERROR"))
            elif expected == "BLOCK":
                passed = got.startswith("BLOCK")
            else:
                passed = got == expected
            ok += passed
            total += 1
            print(("OK  " if passed else "FAIL"), f"{row['text'][:20]:<20}", f"기대={row['label']:<4}", f"결과={got}")
    analyzer.close()
    print(f"\n{ok}/{total} 통과")


if __name__ == "__main__":
    main()
