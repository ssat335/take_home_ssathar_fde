# Assumptions — CloudNova Invoice Cleaning

Decisions made where `docs/BUSINESS_CONTEXT.md` is silent or explicitly calls
the case ambiguous. Each entry says what was decided, why, and what it
affects — so a different call can be made later without re-deriving context.

## 1. Ambiguous `DD-MM-YYYY` vs `MM-DD-YYYY` dates

**Decision:** Confirmed by user (2026-10-10). For `\d\d-\d\d-\d{4}` date
strings:
- If the first component is **> 12**, it's unambiguously a day, so parse as
  `DD-MM-YYYY` (only one valid reading).
- Otherwise (both components ≤ 12 — genuinely ambiguous), parse as
  **`MM-DD-YYYY`** (US convention).

**Why:** Consistent with the unambiguous `MM/DD/YYYY` rows elsewhere in the
file, and no other signal (e.g. a locale field) to decide otherwise. The
first bullet isn't a judgment call — forcing `MM-DD-YYYY` on *all* dash
dates (the initial implementation) produced invalid months (e.g.
`22-11-2025` → month 22) and wrongly rejected 143 valid rows as
unparseable; fixed in `clean_invoices.py` by checking the first component
before picking a format.

**Affects:** `signup_date` and `invoice_date` parsing in `clean_invoices.py`
(`parse_date`). Implemented and run: 179 rows in the final clean output
carry `date_anomaly="ambiguous_date_assumed_mdy"` (genuinely ambiguous,
parsed per this rule — some of the originally-flagged 201 were overwritten
by the higher-priority `invoice_before_signup`/`future_dated` anomaly flags
on the same row). 0 rows rejected as unparseable after the fix.

## 2. Currency column vs. inline amount-string suffix

**Decision:** Confirmed by user (2026-10-10). The `currency` column is
**canonical**. If `amount` has a trailing currency-code suffix (e.g.
`"645,840.00 EUR"`) that disagrees with `currency`, trust `currency` and log
a warning — do not silently rewrite `currency` to match the suffix.

**Why:** `currency` is a dedicated, structured field; the suffix is a
free-text artifact of inconsistent string formatting in `amount` and is the
less trustworthy of the two.

**Affects:** `amount` → `amount_usd` normalization in `clean_invoices.py`
(FX conversion keys off `currency`, not the suffix). Implemented and run:
500 rows have an inline currency suffix in `amount`; **0 disagree** with the
`currency` column in this dataset, so the rule hasn't yet had to override
anything in practice — but the code path (`currency` wins, mismatch logged
as a warning) is in place for if/when it does.

## 3. Near-duplicate `invoice_id` tie-break

**Status:** Implemented and run, **not yet explicitly confirmed by user** —
flagging again since it directly changes recognized-revenue totals. See
`spec/clean_invoices_spec.md` §2 and `resolve_near_duplicates()` in
`clean_invoices.py`. Rule: prefer the more-complete row (fewest blank
fields), then prefer terminal statuses (`paid`/`refunded`/`failed`/`void`)
over `pending`, then keep the first row encountered.

**Observed effect:** of 5,125 raw rows, 82 are full duplicates (dropped
first, uncontroversial) and a further 43 rows are near-duplicates (same
`invoice_id`, conflicting `status`/`amount`) resolved by this rule, leaving
5,000 clean rows. Recognized revenue under this rule: $120,145,168.20;
refunds: -$8,923,203.77; net: $111,221,964.43.

---

# Assumptions — NL → DuckDB Query Agent

Defaults picked by Claude per user instruction (2026-10-10): "assume what
is best" for the open questions in `spec/query_agent_spec.md`, flagged here
for later review rather than confirmed. User has explicitly accepted that
the two account-grain questions (avg MRR/account, churn rate) may come out
wrong in this first version — not blocking implementation on getting those
exactly right.

## 4. Account snapshot rule (avg MRR/account, churn rate)

**Assumed default (not yet confirmed):** use each account's *most recent*
invoice (`MAX(invoice_date_parsed)` per `account_id`) as that account's
current snapshot for `plan_clean`, `region`, `mrr_usd`, `churned_clean`.

**Why:** simplest well-defined rule that avoids double-counting an account
once per invoice; "most recent state" is the most intuitive reading of
"per account" when the docs don't say otherwise.

**Affects:** query SQL for "which region has the highest average MRR per
account" and "churn rate among Enterprise vs. Starter" in the agent's
system prompt / generated SQL. **Known risk, accepted by user:** this is
one of the two question types explicitly flagged as possibly-wrong in the
first version — a snapshot can misrepresent an account that changed plan
or region over time, or double-counts nothing but may pick a stale/odd
invoice if `invoice_date_parsed` has anomalies (see cleaning assumptions
§1 — some dates are anomaly-flagged, not excluded).

## 5. CSAT flag rule (top-5 accounts question)

**Assumed default (not yet confirmed):** flag an account if **any** of its
invoices has `csat_score <= 2` (`MIN(csat_score) <= 2` across the account's
invoices), not just its most recent.

**Why:** more conservative and more visible to a stakeholder — a past bad
CSAT score is still worth surfacing on a revenue leaderboard, and this
avoids re-deriving a second snapshot rule just for CSAT.

**Affects:** the CSAT-flag column on the "top 5 accounts by revenue" query.

**Bug caught during testing:** the first version of `query_tool.py`'s
docstring only had the general "collapse to one row per account" guidance,
with no CSAT-specific rule. The agent reasonably inferred it should use
each account's *latest* CSAT (the same pattern as the MRR/churn snapshot),
which contradicts this assumption. Fixed by adding an explicit CSAT rule
to the tool docstring ("flag if ANY invoice has csat_score <= 2, i.e.
MIN(csat_score) <= 2 — not just the latest"); confirmed fixed by rerunning
the question and checking the generated SQL uses `MIN(csat_score)` grouped
by account across all invoices, not a latest-invoice snapshot.

## 6. SQL surface exposed to the agent

**Assumed default (not yet confirmed):** let the agent generate any
read-only `SELECT`/`WITH` query against the full `invoices_clean` table —
no column allow-list — gated only by the safety validation in
`query_invoices` (single statement, no DDL/DML keywords, row cap).

**Why:** this is meant to be a general NL-query tool, not just an answer
machine for the six seed questions; restricting columns would block
reasonable follow-up questions the agent should be able to handle, and the
real risk (destructive SQL) is already closed off by the statement-type
validation, not by column restriction.

**Affects:** `tools/query_tool.py` validation logic — permissive on
columns/aggregations, strict on statement type.

## 7. DuckDB file location

**Assumed default (not yet confirmed):** persist the DuckDB file at
`data/cloudnova.duckdb`, inside the already-gitignored `data/` folder.

**Why:** consistent with the clean/rejects CSVs already living there,
untracked by git; no reason to introduce a new location for derived data.

**Affects:** `load_duckdb.py` / tool init path.

## 8. `account_name` is not a reliable grouping key — only `account_id` is

**Decision:** discovered while building ground-truth answers for
`eval/golden_set.json` (2026-10-10), not anticipated in the original spec.
`account_id` is the sole reliable account key. **Always `GROUP BY`/`JOIN
ON` `account_id` alone** when aggregating "per account." If a display name
is needed, use the `account_name` from that account's most recent invoice
(same snapshot pattern as §4), never as part of the grouping key itself.

**Why:** `account_name` varies for the same `account_id` in **976 of 3,815
accounts (~25.6%)** — e.g. `ACC-7509` appears as "Stark Inc", "Soylent
Ltd", and "Aperture Inc" across different invoices (aliases/renames/typos,
same pattern of dirtiness as the other categorical columns, just not
called out in `docs/BUSINESS_CONTEXT.md`). Grouping by
`(account_id, account_name)` together — what the agent's "top 5 accounts"
query originally did — silently splits one account's revenue across
multiple rows instead of summing it, understating the true top accounts.

**Bug caught and fixed:** the live agent's "top 5 accounts by revenue"
answer (built before this was discovered) ranked `ACC-9946` ("Initech
LLC") top at $1,205,642.75 using `GROUP BY account_id, account_name`.
After adding the explicit rule to `tools/query_tool.py`'s docstring and
re-running the same question, the agent correctly re-ranked `ACC-7509`
("Stark Inc") top at $1,210,592.75, and — materially — **surfaced a new
CSAT flag that the bug had hidden**: `ACC-1196` ("Pied Piper Partners"),
#4 by revenue, has `min_csat = 2` and should have been flagged all along.
Confirmed against the independently-computed ground truth in
`eval/golden_set.json` Q5.

**Affects:** any account-grouped query — most visibly "top 5 accounts by
revenue."
