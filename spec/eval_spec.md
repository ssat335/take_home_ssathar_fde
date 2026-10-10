# Spec: Golden-Set Eval for the Query Agent

Status: **implemented and run** — see Results at the bottom.

## Goal

A regression check for `agent.py`/`tools/query_tool.py`: run a fixed set of
questions with independently-verified correct answers through the live
agent, and catch it automatically if a prompt tweak, docstring edit, or
model swap breaks a previously-correct answer (exactly what happened twice
already during manual testing — the CSAT-snapshot bug and the
`account_name`-grouping bug, both in `assumptions/ASSUMPTIONS.md` §5 and
§8). Manual testing caught those by luck of which questions were tried;
an eval set catches them every time, automatically.

## Golden set format — `eval/golden_set.json`

A JSON list of 10 questions. Each entry:

```json
{
  "id": "Q1",
  "category": "revenue",
  "question": "...",
  "ground_truth_sql": "-- the independently-vetted SQL that computes the answer",
  "checks": [
    {"type": "scalar", "value": 32686371.0497, "tolerance_pct": 1.0,
     "description": "total recognized revenue, USD, 2024"}
  ],
  "notes": "..."
}
```

- **`ground_truth_sql`** was run directly against `invoices_clean` once,
  by hand, to produce `checks[].value` — independent of anything the
  agent generates. This is the source of truth, not a second LLM's
  opinion.
- **`checks`** — two types:
  - `scalar`: passes if **any** numeric value in the tool-call result rows
    the agent actually used is within `tolerance_pct` of `value`. Checking
    "any value in the rows" rather than parsing the NL answer text is
    deliberate — the agent's prose varies, its underlying tool-call data
    doesn't.
  - `keyword`: passes if a case-insensitive substring appears in the
    agent's final natural-language answer (used for categorical answers
    like "which region" or "which payment method," where the identifying
    value is a label, not a number).
- **`notes`** flags questions that exercise an unconfirmed assumption
  (account-grain snapshot, `assumptions/ASSUMPTIONS.md` §4) — a fail there
  is informative, not necessarily a regression.

## The 10 questions

1. Total recognized revenue (USD, paid, 2024) — from the original six.
2. Region with highest average MRR per account — original six;
   **account-grain risk** (§4).
3. Total refunds given back — original six.
4. Churn rate, Enterprise vs. Starter — original six; **account-grain
   risk** (§4).
5. Top 5 accounts by revenue + CSAT ≤ 2 flag — original six; exercises the
   `account_name`-grouping fix (§8) and the CSAT `MIN()` rule (§5).
6. Pending/failed invoice count + exposed $ — original six.
7. *(new)* Total churned accounts overall — same account-grain pattern as
   #2/#4 but simpler (no plan split), a cleaner signal if the snapshot
   rule itself ever changes.
8. *(new)* Average discount % on Enterprise invoices — invoice-grain, no
   account collapsing needed; a "simple" control question.
9. *(new)* Most common payment method on paid invoices — categorical
   answer, exercises the `keyword` check type and nulls in
   `payment_method_clean`.
10. *(new)* Count of invoices with missing `region` — a data-quality-aware
    question, exercises `IS NULL` handling.

Four new questions (#7–#10) were added beyond the original six so the eval
set isn't exclusively the same questions the agent was hand-tuned against
— #8–#10 in particular don't touch the account-grain logic at all, so a
regression there means something more fundamental broke (schema, FX,
business rules), not just the harder per-account aggregation.

## Harness — `eval/run_eval.py`

For each golden question:
1. Build a fresh agent (`callback_handler=None`, no streamed output).
2. Run the question.
3. Walk `agent.messages` for `toolUse`/`toolResult` blocks on
   `query_invoices` and collect every numeric value from every row the
   agent actually received back from DuckDB.
4. Evaluate each of the question's `checks` against that collected data
   (`scalar`) or the final answer text (`keyword`).
5. Record PASS/FAIL per check, plus the SQL the agent ran, for
   diagnosis — a `scalar` fail shows the closest value found so a reader
   can tell "off by a rounding difference" from "completely wrong query."

Prints a per-question summary table and writes the full detail to
`eval/results/<timestamp>.json`. Exits non-zero if any question fails, so
it can be wired into CI later if useful.

## Run it

```bash
python eval/run_eval.py
```

## Out of scope

- No semantic/LLM-graded check of the natural-language prose itself —
  correctness is judged on the underlying data the agent retrieved, not
  how it phrased the sentence.
- Not wired into CI yet — run manually for now.

## Results (most recent run)

See `eval/results/` for the timestamped JSON from each run, and the
summary printed to stdout. As of this run: **10/10 passed** after fixing
the `account_name`-grouping bug (§8) that the golden set's Q5 ground truth
surfaced during construction, before the harness was even run once.
