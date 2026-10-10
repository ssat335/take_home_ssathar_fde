"""Run the golden-set eval against the live query agent.

See spec/eval_spec.md for the design. Checks are evaluated against the
actual DuckDB rows the agent retrieved via query_invoices (not the
natural-language prose), except `keyword` checks which also look at the
final answer text for categorical/label answers.

Usage:
    python eval/run_eval.py
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent import build_agent  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

GOLDEN_SET_PATH = Path(__file__).parent / "golden_set.json"
RESULTS_DIR = Path(__file__).parent / "results"


def _collect_tool_data(messages: list[dict]) -> tuple[list[float], list[str], list[str], str]:
    """Pull numeric/string values, SQL statements, and the final answer text
    out of an agent's message history."""
    numeric_values: list[float] = []
    string_values: list[str] = []
    sql_statements: list[str] = []

    for message in messages:
        for block in message.get("content", []):
            if "toolUse" in block and block["toolUse"].get("name") == "query_invoices":
                sql = block["toolUse"].get("input", {}).get("sql")
                if sql:
                    sql_statements.append(sql)
            if "toolResult" in block:
                for item in block["toolResult"].get("content", []):
                    text = item.get("text")
                    if not text:
                        continue
                    try:
                        payload = json.loads(text)
                    except (ValueError, TypeError):
                        continue
                    for row in payload.get("rows", []):
                        for value in row.values():
                            if isinstance(value, bool):
                                continue
                            if isinstance(value, (int, float)):
                                numeric_values.append(float(value))
                            elif value is not None:
                                string_values.append(str(value))

    final_text = ""
    if messages:
        final_text = "\n".join(
            block["text"] for block in messages[-1].get("content", []) if "text" in block
        )
    return numeric_values, string_values, sql_statements, final_text


def _numeric_match(target: float, candidates: list[float], tolerance_pct: float) -> tuple[bool, float | None]:
    """True if any candidate matches target (or a %/fraction/sign variant of
    it, to tolerate the agent reporting a percent as 0-1 or as negative)."""
    variants = {target, -target, target / 100.0, -target / 100.0, target * 100.0, -target * 100.0}
    for variant in variants:
        tol = max(abs(variant) * (tolerance_pct / 100.0), 1e-6)
        for value in candidates:
            if abs(value - variant) <= tol:
                return True, value
    return False, None


def run_check(check: dict, numeric_values: list[float], string_values: list[str], final_text: str) -> tuple[bool, Any]:
    if check["type"] == "scalar":
        return _numeric_match(check["value"], numeric_values, check["tolerance_pct"])
    if check["type"] == "keyword":
        needle = check["value"].lower()
        haystacks = [final_text.lower(), *(s.lower() for s in string_values)]
        return any(needle in h for h in haystacks), None
    raise ValueError(f"Unknown check type: {check['type']}")


def main() -> None:
    golden_set = json.loads(GOLDEN_SET_PATH.read_text())
    results: list[dict] = []
    all_passed = True

    for item in golden_set:
        agent = build_agent(verbose=False)
        try:
            agent(item["question"])
        except Exception as exc:
            print(f"[{item['id']}] ERROR running agent: {exc}")
            results.append({**item, "error": str(exc), "passed": False})
            all_passed = False
            continue

        numeric_values, string_values, sql_statements, final_text = _collect_tool_data(agent.messages)

        check_results = []
        item_passed = True
        for check in item["checks"]:
            passed, matched = run_check(check, numeric_values, string_values, final_text)
            check_results.append(
                {
                    "description": check["description"],
                    "type": check["type"],
                    "expected": check["value"],
                    "matched_value": matched,
                    "passed": passed,
                }
            )
            item_passed = item_passed and passed

        all_passed = all_passed and item_passed
        print(f"[{item['id']}] {'PASS' if item_passed else 'FAIL'} — {item['question']}")
        for check_result in check_results:
            mark = "+" if check_result["passed"] else "x"
            if check_result["matched_value"] is not None:
                found = f"got {check_result['matched_value']}"
            else:
                found = "matched" if check_result["passed"] else "not found"
            print(f"    {mark} {check_result['description']}: expected≈{check_result['expected']} ({found})")

        results.append(
            {
                "id": item["id"],
                "question": item["question"],
                "sql": sql_statements,
                "final_text": final_text,
                "checks": check_results,
                "passed": item_passed,
                "notes": item.get("notes"),
            }
        )

    RESULTS_DIR.mkdir(exist_ok=True)
    out_path = RESULTS_DIR / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    out_path.write_text(json.dumps(results, indent=2, default=str))

    n_pass = sum(1 for r in results if r.get("passed"))
    print()
    print(f"{n_pass}/{len(results)} passed. Full detail: {out_path}")
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
