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
