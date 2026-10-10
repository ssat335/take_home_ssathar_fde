# Conversation log — 2026-10-10 — NL query agent (DuckDB + Strands)

Continuation of `2026-10-10_data-cleaning-session.md`, covering the
question-answering agent built on top of the cleaned invoice data.
Summarized, not a verbatim transcript.

---

**9. "Now suggest a way that we can get users to query the cleaned data
for [the six stakeholder questions] - can duck db do it - users ask
question strands agent convert that to duck db query and the response
answers the question?"**

Recommended DuckDB (embedded, no server, fast enough at this row count) +
a single Strands `@tool` that runs validated read-only SQL, with the
schema and business rules baked into the tool's docstring rather than
relying on free-form NL-to-SQL alone. Flagged the main risk up front:
"average MRR per account" and "churn rate" need the data collapsed to one
row per account first, or an account with many invoices gets
over-weighted.

**10. "Create a SPEC file first and let me review it"**

Created `spec/query_agent_spec.md`: architecture (DuckDB table → Strands
tool → agent → CLI), full schema reference, the account-grain risk spelled
out in detail, a worked SQL approach for each of the six target questions
(feasibility check), safety/validation rules for the SQL tool, and four
open questions needing sign-off (account snapshot rule, CSAT flag rule,
how permissive the SQL surface should be, DuckDB file location).

**11. "The two question can be wrong in the first verion that we built.
For the open questions assume what is best and put that in the
assumptions folder for later review."**

User accepted the two account-grain questions (avg MRR/account, churn
rate) may come out wrong in v1 — not blocking on getting those exactly
right. Picked defaults for all four open questions and documented them as
`assumptions/ASSUMPTIONS.md` §4–§7 (most-recent-invoice account snapshot;
flag CSAT ≤ 2 if *any* invoice qualifies; permissive SQL surface gated by
statement-type validation, not column allow-listing; DuckDB file inside
the existing gitignored `data/` folder). Updated the spec's status to
"ready to implement."

**12. "yes build it now. the code must be run command line as well"**

Installed `strands-agents`, `duckdb`, `python-dotenv`. Confirmed AWS
credentials were live and that
`us.anthropic.claude-sonnet-4-5-20250929-v1:0` is an ACTIVE inference
profile in `us-west-2` before writing it into `.env`/`.env.example`, per
CLAUDE.md's rule. Built:
- `load_duckdb.py` — loads the clean CSV into a persisted DuckDB table,
  rebuilding only when the CSV is newer.
- `tools/query_tool.py` — the Strands `@tool query_invoices(sql)`:
  validates single read-only `SELECT`/`WITH` statements, blocks DDL/DML,
  caps rows, logs every query; docstring carries the schema and business
  rules.
- `agent.py` — Bedrock-backed Strands `Agent` wired per CLAUDE.md's
  canonical shape (model/region/temperature from `.env`).
- `ask.py` — CLI entry point (`python ask.py "<question>"`, `--quiet`
  flag).

Ran all six target questions end-to-end. One bug caught and fixed during
testing: the "top 5 accounts, flag CSAT ≤ 2" question initially used only
an account's *latest* CSAT (the tool docstring didn't state the CSAT rule
explicitly, so the agent inferred the same "snapshot" pattern used for
MRR/churn). Added an explicit CSAT rule to the docstring and confirmed the
regenerated SQL used `MIN(csat_score)` across all of an account's
invoices instead. Committed as `aa924d5`.

**13. "Write a read me file The README should let a customer stakeholder
understand what you built and why in 3 minutes. How to run it as well."**

Wrote `README.md`: what was built and why (dirty data + an
`assumptions/`-driven approach to keep every judgment call visible rather
than silently baked in), a map of the repo, the headline cleaned numbers,
known limitations to flag before relying on the answers, and step-by-step
run instructions.

**14. "Commit it now"**

Committed `README.md` as `6808918`.

**15. "Save the conversation so far and commit it"**

This log.
