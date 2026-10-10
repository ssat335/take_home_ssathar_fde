# Spec: NL → DuckDB Query Agent — CloudNova Invoice Analytics

Status: **implemented and tested**. Built: `load_duckdb.py`,
`tools/query_tool.py`, `agent.py`, `ask.py` (CLI entry point). All six
target questions run successfully end-to-end — see
`assumptions/ASSUMPTIONS.md` §4–§7 for the defaults used and one bug caught
and fixed during testing (CSAT flag rule, §5).

Run from the command line:
```
python ask.py "What was our total recognized revenue in USD for paid invoices in 2024?"
python ask.py --quiet "How much have we given back in refunds?"
```

## Goal

Let a user ask a plain-English question about the cleaned invoice data and
get back a correct answer with the SQL that produced it, via a Strands
agent that translates the question into a read-only DuckDB query against
`data/cloudnova_invoices_clean.csv`, executes it, and answers.

## Why DuckDB

Embedded, no server, queries the clean CSV (or a persisted `.duckdb` file)
directly with full SQL, and is fast enough at this row count (~5,000 rows)
that every query is sub-second. No new infra to run or manage.

## Architecture

```
user question
      │
      ▼
Strands Agent (BedrockModel, model_id/region from .env per CLAUDE.md)
      │  system prompt: schema + business rules + grain warnings (below)
      ▼
tools/query_tool.py  →  query_invoices(sql: str) -> QueryResult
      │  validates read-only, single-statement SQL
      ▼
DuckDB  →  SELECT ... FROM invoices_clean ...
      │
      ▼
rows back to agent → natural-language answer + the SQL used, to the user
```

**Components:**

1. **Data layer** — a small loader (`load_duckdb.py` or lazy-init inside
   the tool) that creates a DuckDB table `invoices_clean` from
   `data/cloudnova_invoices_clean.csv`. Persist to `data/cloudnova.duckdb`
   (stays inside the already-gitignored `data/` folder) so the CSV isn't
   re-parsed on every agent run; rebuild the table when the CSV's mtime is
   newer than the DuckDB file's.
2. **Tool layer** — `tools/query_tool.py`, one Strands `@tool`:
   `query_invoices(sql: str) -> list[dict]`. Validates the query is a
   single read-only `SELECT`/`WITH` statement (see Safety below), executes
   it, returns rows (capped) as a list of dicts. Raises `ValueError` on
   invalid input — per CLAUDE.md, "the agent can recover from a raised
   error, not from a silent wrong answer."
3. **Agent layer** — `agent.py`, a Strands `Agent` wired to `BedrockModel`
   per the canonical shape in CLAUDE.md (model id, region, temperature from
   `.env`/config, not literals), `tools=[query_invoices]`. The system
   prompt carries the schema, the business rules, and the account-grain
   warning below — this is what keeps the generated SQL correct instead of
   plausible-looking-but-wrong.
4. **Entry point** — `ask.py`, takes a question string, runs the agent,
   prints the natural-language answer plus the SQL it ran.
   `callback_handler=None` for non-interactive/eval use.

## Schema exposed to the agent (table `invoices_clean`)

All columns already produced by `clean_invoices.py`: `invoice_id`,
`account_id`, `account_name`, `region`, `industry`, `plan_clean`,
`billing_cycle_clean`, `seats`, `currency`, `amount_usd`, `discount_pct`,
`status_clean`, `payment_method_clean`, `signup_date_parsed`,
`invoice_date_parsed`, `churned_clean`, `csat_score`, `support_tickets`,
`list_price_usd`, `mrr_usd`, `is_revenue`, `is_refund`,
`recognized_revenue_usd`, `net_revenue_usd`, `date_anomaly`.

The system prompt states plainly: **query `invoices_clean`, never the raw
CSV** — the raw file's dirty categories/dates/amounts aren't safe for SQL
aggregation.

## ⚠️ Account-grain vs. invoice-grain — the main correctness risk

One row per **invoice**; an account has many. Two of the six target
questions need the data collapsed to one row per **account** first, or the
answer is silently wrong (e.g. an account with 12 monthly invoices would be
counted 12× in a naive `AVG`/churn calc):

- **"average MRR per account" (by region)** — needs one MRR value per
  account before averaging. **Proposed rule:** use each account's
  *most recent* invoice (`MAX(invoice_date_parsed)` per `account_id`) as
  that account's current snapshot (plan, region, `mrr_usd`, `churned_clean`).
- **"churn rate among Enterprise vs. Starter"** — same snapshot, then
  `AVG(churned_clean::int)` grouped by `plan_clean`, over distinct accounts.
- **"top 5 accounts by revenue"** — different grain on purpose: this one
  *is* a sum across all of an account's invoices
  (`SUM(recognized_revenue_usd) GROUP BY account_id`), not a snapshot.
- **"flag any with CSAT ≤ 2"** on that same top-5 list — CSAT is also
  per-invoice. **Proposed rule:** flag the account if *any* of its
  invoices has `csat_score <= 2` (most conservative / most visible to a
  stakeholder), rather than only the latest.

This is the one judgment call in this spec with no stated business rule to
derive it from — flagged for sign-off below.

## Six target questions → SQL approach (feasibility check)

| # | Question | Approach |
|---|---|---|
| 1 | Total recognized revenue (USD, paid, 2024) | `SELECT SUM(recognized_revenue_usd) FROM invoices_clean WHERE EXTRACT(year FROM invoice_date_parsed) = 2024 AND status_clean = 'paid'` |
| 2 | Region with highest avg MRR/account | account snapshot (latest invoice per `account_id`) → `AVG(mrr_usd) GROUP BY region ORDER BY DESC LIMIT 1` |
| 3 | Total refunds | `SELECT SUM(amount_usd) FROM invoices_clean WHERE status_clean = 'refunded'` (negative; report as "given back $X" using `ABS()`) |
| 4 | Churn rate, Enterprise vs. Starter | account snapshot → `AVG(churned_clean::int) GROUP BY plan_clean` |
| 5 | Top 5 accounts by revenue, flag CSAT ≤ 2 | `SUM(recognized_revenue_usd) GROUP BY account_id ORDER BY DESC LIMIT 5`, joined with `MIN(csat_score) <= 2` flag per account across all their invoices |
| 6 | Pending/failed count + exposed $ | `SELECT COUNT(*), SUM(amount_usd) FROM invoices_clean WHERE status_clean IN ('pending','failed')` |

All six are expressible as a single `SELECT` once the account-grain rule
above is settled — no multi-step orchestration or extra tools needed.

## Safety / validation (in `query_invoices`)

- Reject anything not starting with `SELECT` or `WITH` (case-insensitive).
- Reject `INSERT`/`UPDATE`/`DELETE`/`DROP`/`ALTER`/`ATTACH`/`COPY`/`PRAGMA`/
  `CREATE` anywhere in the string.
- Reject multiple statements (no bare `;` except a single optional trailing
  one).
- Cap returned rows (e.g. 500) and log the executed SQL for every call.
- Open the DuckDB connection read-only where supported.

## Config (per CLAUDE.md)

- `BEDROCK_MODEL_ID`, `AWS_REGION` from `.env`, confirmed against
  `aws bedrock list-inference-profiles` before hardcoding.
- Low/zero `temperature` for SQL generation, from config.
- `logging`, not `print()`, for every query executed and every agent call.

## Testing / eval plan

- Run the 6 stakeholder questions through the agent; compare each answer
  and its SQL's result against the manual baselines already established
  while building `clean_invoices.py` (e.g. recognized revenue
  $120,145,168.20 paid / -$8,923,203.77 refunds, as a sanity cross-check
  where the question matches that scope).
- Log question → generated SQL → result for each, so a wrong answer is
  diagnosable (bad SQL vs. bad grain assumption vs. bad business rule).

## Out of scope for this spec

- UI/chat front-end.
- Multi-turn / follow-up question handling — single-shot question → SQL →
  answer for this version.
- Auto-refreshing the DuckDB table on every `clean_invoices.py` rerun
  beyond the mtime check above.

## Open questions — resolved with assumed defaults

User instruction (2026-10-10): assume the best default for each and record
it in `assumptions/ASSUMPTIONS.md` §4–§7 for later review, rather than
blocking implementation on sign-off. User has explicitly accepted that the
two account-grain questions (avg MRR/account, churn rate) may come out
wrong in this first version.

1. **Account snapshot rule** (MRR, churn questions) — defaulted to "most
   recent invoice per account." `assumptions/ASSUMPTIONS.md` §4.
2. **CSAT flag rule** (top-5 question) — defaulted to "any invoice with
   CSAT ≤ 2 flags the account." `assumptions/ASSUMPTIONS.md` §5.
3. **SQL surface** — defaulted to permissive (any `SELECT`/`WITH` over
   `invoices_clean`, no column allow-list; gated by statement-type
   validation only). `assumptions/ASSUMPTIONS.md` §6.
4. **DuckDB file location** — defaulted to `data/cloudnova.duckdb`, inside
   the already-gitignored `data/` folder. `assumptions/ASSUMPTIONS.md` §7.
