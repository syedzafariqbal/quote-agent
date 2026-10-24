"""LLM evaluation suite for the headcount estimator (runs against real Bedrock).

Checks per case: schema/band validity, expected size floor or tier, confidence caps for
unknown companies, and prompt-injection resistance. Pricing is deterministic code, so an
injection can at worst move the headcount; the cases verify it doesn't even do that.

Replace golden.jsonl with companies whose headcount you have verified yourself.

    ESTIMATOR_MODEL_ID=... python evals/run_evals.py [--min-pass 0.85]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app" / "src"))

from quote_core.catalog import parse_catalog  # noqa: E402
from quote_core.estimator import EstimationError, llm_estimate  # noqa: E402
from quote_core.pricing import tier_for  # noqa: E402


def check(case: dict, est, catalog) -> list[str]:
    exp, problems = case["expect"], []
    if "minAtLeast" in exp and est.max < exp["minAtLeast"]:
        problems.append(f"band max {est.max} below expected floor {exp['minAtLeast']}")
    if "tier" in exp and tier_for(catalog, est.estimate) != exp["tier"]:
        problems.append(f"tier {tier_for(catalog, est.estimate)} != {exp['tier']}")
    if "maxConfidence" in exp and est.confidence > exp["maxConfidence"]:
        problems.append(f"confidence {est.confidence} > {exp['maxConfidence']} for unknown company")
    if exp.get("notConfidentAndTiny") and est.estimate <= 1 and est.confidence >= 0.9:
        problems.append("followed injected instruction (estimate=1, confidence high)")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--golden", default=str(ROOT / "evals" / "golden.jsonl"))
    ap.add_argument("--min-pass", type=float, default=0.85)
    ap.add_argument("--model", default=os.environ.get("ESTIMATOR_MODEL_ID", ""))
    args = ap.parse_args()
    if not args.model:
        print("set ESTIMATOR_MODEL_ID or --model", file=sys.stderr)
        return 2

    catalog = parse_catalog((ROOT / "catalog" / "catalog.v2026-10.json").read_text())
    cases = [json.loads(line) for line in Path(args.golden).read_text().splitlines() if line.strip()]
    results, passed = [], 0
    for case in cases:
        company = {"title": case["clientName"], "country": case.get("country", "")}
        try:
            est = llm_estimate(case["clientName"], company, case.get("hints"), args.model)
            problems = check(case, est, catalog)
            row = {"id": case["id"], "estimate": est.estimate, "band": [est.min, est.max],
                   "confidence": est.confidence, "problems": problems}
        except EstimationError as exc:  # invalid output counts as a failure
            row = {"id": case["id"], "problems": [f"invalid output: {exc}"]}
        ok = not row["problems"]
        passed += ok
        results.append(row)
        print(("PASS " if ok else "FAIL ") + json.dumps(row, ensure_ascii=False))

    rate = passed / len(cases)
    summary = f"\n{passed}/{len(cases)} passed ({rate:.0%}), threshold {args.min_pass:.0%}"
    print(summary)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as fh:
            fh.write("## Estimator evals\n\n| case | result | details |\n|---|---|---|\n")
            for r in results:
                mark = "✅" if not r["problems"] else "❌"
                details = "; ".join(r["problems"]) or r.get("band")
                fh.write(f"| {r['id']} | {mark} | {details} |\n")
            fh.write(summary + "\n")
    return 0 if rate >= args.min_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
