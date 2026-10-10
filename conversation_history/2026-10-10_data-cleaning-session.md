# Conversation log — 2026-10-10 — CloudNova invoice cleaning

Record of the prompts given to Claude Code in this session and what was done
in response, kept for traceability on how the spec/assumptions/script came
to be. Summarized, not a verbatim transcript.

---

**1. "Analyse the data file and run a quick quality check and summry on the
data. For context the data disctionary is defined in docs business
context"**

Read `docs/BUSINESS_CONTEXT.md` and `data/cloudnova_invoices.csv` (5,125
rows). Ran a pandas-based quality scan: found 163 full-duplicate rows, 124
repeated `invoice_id`s (39 with real status/amount conflicts), 18 raw
spellings of `plan`, 24 of `status`, messy `amount` strings (`$`, commas,
inline currency suffixes), 5 date format families with 575 genuinely
ambiguous day/month rows, malformed emails (`_at_` instead of `@`), and
various null counts. Also ran a quick normalized revenue summary
(recognized revenue ≈ $120.0M, refunds ≈ -$8.9M).

**2. "Create a initial spec file in spec folder that cleans the dataset
using a python script and show me the spec"**

Created `spec/clean_invoices_spec.md` — a design spec for
`clean_invoices.py` covering dedup, categorical normalization, amount/date
parsing, derived revenue fields, and validation. Flagged three decisions
needing sign-off: near-dup tie-break rule, ambiguous-date parsing, and
currency-column vs. inline-suffix priority.

**3. "Use MM-DD-YYYY - add this in the assiumptions folder and use currency
column"**

Confirmed two of the three flagged decisions. Created
`assumptions/ASSUMPTIONS.md` documenting them with rationale, and updated
the spec to mark those two confirmed and point at the assumptions file. The
near-dup tie-break rule was left open.

**4. "yes create and run clean_invoice.py"**

Confirmed the proposed near-dup tie-break rule by implication ("yes"), then
implemented `clean_invoices.py` per the spec and ran it. First run
mis-rejected 143 rows: the code forced `MM-DD-YYYY` on *every* dash-format
date, including unambiguous ones with a first component >12 (e.g.
`22-11-2025`), producing an invalid month. Fixed to only apply the
MM-DD-YYYY assumption to genuinely ambiguous dates and parse unambiguous
ones as `DD-MM-YYYY`. Final run: 5,125 raw → 5,000 clean + 43 rejected
(near-dup conflicts only), recognized revenue $120,145,168.20, refunds
-$8,923,203.77, net $111,221,964.43.

**5. "Update teh assumptions"**

Updated `assumptions/ASSUMPTIONS.md` and `spec/clean_invoices_spec.md` to
reflect the implemented/run behavior rather than pre-implementation
estimates: corrected the date rule description, recorded real row counts
(179 ambiguous-date rows, 0 currency mismatches, 43 near-dup rows dropped),
and marked the near-dup tie-break as "implemented, not yet explicitly
confirmed."

**6. "Is teh currency discrepancy resolved?"**

Checked the clean output directly: 500 rows have an inline currency suffix
in `amount`, 0 disagree with the `currency` column. Confirmed resolved —
the earlier "discrepancy" was inconsistent formatting, not inconsistent
data.

**7. "How is the currency conversion value defined?"**

Explained the fixed FX table (`FX_TO_USD = {"USD": 1.0, "EUR": 1.08, "GBP":
1.27}` in `clean_invoices.py`), sourced from `docs/BUSINESS_CONTEXT.md`'s
stated reference rates, applied as a static multiplier keyed off the
canonical `currency` column.

**8. "Now commit the current version of the code base and we can got to the
next step. Allso keep a history of my prompt conversation so far in a
folder and commit it as well."**

This log. Committing `spec/`, `assumptions/`, `clean_invoices.py`, and
`conversation_history/`. `data/` stays gitignored (pre-existing repo
convention) — raw and generated CSVs are not tracked.
