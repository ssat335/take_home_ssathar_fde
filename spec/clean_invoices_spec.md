# Spec: `clean_invoices.py` — CloudNova Invoice Cleaning

Status: **implemented and run** (`clean_invoices.py` at repo root). All
three flagged decisions are now in `assumptions/ASSUMPTIONS.md`; #3 (near-dup
tie-break) is implemented but still awaiting explicit user confirmation
since it affects revenue totals.

## Goal

Read `data/cloudnova_invoices.csv` (raw, dirty export) and produce a cleaned,
normalized dataset that downstream analysis/RAG queries can trust, per the
rules in `docs/BUSINESS_CONTEXT.md`. One run, deterministic, no network calls.

## Input / Output

- **Input:** `data/cloudnova_invoices.csv` (5,125 rows, 19 columns, all `str`).
- **Output:**
  - `data/cloudnova_invoices_clean.csv` — one row per invoice, cleaned +
    derived columns appended.
  - `data/cloudnova_invoices_rejects.csv` — rows dropped or quarantined
    during cleaning, with a `reject_reason` column, for audit.
  - Log summary to stdout (via `logging`): row counts in/out/dropped,
    per-field null counts before/after, dedup counts.

## Processing steps (in order)

### 1. Full-duplicate removal
Drop exact full-row duplicates (`df.drop_duplicates()`). Earlier manual scan
with `keep=False` (counts every row in a dup set) found 163 rows involved;
`drop_duplicates()`'s default `keep="first"` removes only the extra copies
— **82 rows actually dropped** in the implemented run, leaving one copy of
each.

### 2. Near-duplicate `invoice_id` resolution
124 `invoice_id`s repeat in the raw data with conflicting `status`/`amount`
(39 groups have real conflicts, not just full dupes). **Tie-break rule:**

1. Prefer the row with fewer blank/null fields (more complete record wins).
2. If still tied, prefer by status priority: `paid` / `refunded` (resolved,
   terminal states) > `failed` / `void` (also terminal) > `pending`
   (incomplete/in-flight — least trustworthy when competing with a
   terminal status).
3. If still tied, keep the first row as encountered (stable, reproducible).

Every dropped row from this step is written to the rejects file with
`reject_reason="near_duplicate_invoice_id"`.

> ⚠️ **Implemented, not yet confirmed** — see `assumptions/ASSUMPTIONS.md`
> §3. This priority order is a judgment call — the source data has no
> timestamp/version field to arbitrate on. **43 rows dropped** by this rule
> in the implemented run (after full-dupe removal), directly shaping
> recognized revenue ($120,145,168.20 paid / -$8,923,203.77 refunds under
> this rule) — confirm or adjust before treating those totals as final.

### 3. Categorical normalization
Map every raw variant to one canonical value (case-insensitive, trimmed).
Unmapped values become `"unknown"` and are logged (not dropped — don't
silently guess).

| Column | Canonical values | Mapping source |
|---|---|---|
| `plan` → `plan_clean` | `Starter`, `Pro`, `Enterprise` | `starter/start/tier 1`→Starter, `pro/professional/tier 2`→Pro, `enterprise/ent/tier 3`→Enterprise |
| `status` → `status_clean` | `paid`, `pending`, `failed`, `refunded`, `void` | per table in BUSINESS_CONTEXT.md §"Status meanings" |
| `billing_cycle` → `billing_cycle_clean` | `monthly`, `annual` | `monthly`→monthly; `annual/annually/yearly`→annual |
| `payment_method` → `payment_method_clean` | `credit_card`, `ach`, `wire`, `invoice`, `unknown` | case-fold + underscore `Credit Card`→credit_card, `Wire Transfer`→wire |
| `churned` → `churned_clean` (bool) | `True`/`False` | `1/Y/Yes/true/TRUE`→True; `0/N/No/false/FALSE`→False |

### 4. Amount normalization
`amount` (raw string) → `amount_value` (float) + `amount_currency` (str):

1. Strip `$`, `£`, thousands commas, surrounding whitespace.
2. If the string has a trailing 3-letter currency code (e.g. `"645,840.00 EUR"`),
   extract it — but **the `currency` column is treated as canonical** if the
   two disagree (logged as a warning, not fixed silently).
   > ✅ **Confirmed** — see `assumptions/ASSUMPTIONS.md` §2.
3. Preserve sign (refunds are negative).
4. Convert to USD using fixed FX: `EUR→USD 1.08`, `GBP→USD 1.27`, `USD→USD 1.0`
   → `amount_usd`.

### 5. Date normalization
`signup_date`, `invoice_date` (raw string) → `*_parsed` (ISO `YYYY-MM-DD`):

Formats observed: `YYYY-MM-DD`, `MM/DD/YYYY`, `YYYY/MM/DD`, `Mon D YYYY`,
and `DD-MM-YYYY`/`MM-DD-YYYY` (ambiguous when both parts ≤ 12 — 575 rows).

**Rule:** for the `\d\d-\d\d-\d{4}` pattern, if the first component is `>12`
it's unambiguous (must be `DD-MM-YYYY`); otherwise (genuinely ambiguous,
both parts ≤ 12) parse as **`MM-DD-YYYY`** (US convention), consistent with
the unambiguous `MM/DD/YYYY` rows elsewhere in the file.

> ✅ **Confirmed and implemented** — see `assumptions/ASSUMPTIONS.md` §1.
> 179 rows in the clean output carry
> `date_anomaly="ambiguous_date_assumed_mdy"` so downstream users can
> re-derive with a different rule if needed. (An earlier implementation
> that forced `MM-DD-YYYY` on *all* dash dates produced invalid months and
> wrongly rejected 143 rows — fixed before this run.)

Post-parse sanity checks (flag, don't drop, unless noted):
- `invoice_date_parsed < signup_date_parsed` → log + flag
  `date_anomaly="invoice_before_signup"` (16 rows found).
- `invoice_date_parsed > today` → log + flag `date_anomaly="future_dated"`
  (146 rows found) — kept in output (likely synthetic seed data, but not
  ours to delete).
- Unparseable date (after all formats tried) → row moves to rejects file
  with `reject_reason="unparseable_date"`.

### 6. Email normalization
`contact_email` → `contact_email_clean`:
- Replace literal `"_at_"` with `"@"`.
- Blank → `NaN` (kept, not dropped — email isn't required for revenue calcs).
- Validate against a basic `local@domain.tld` pattern; non-matching,
  non-blank values logged as `email_malformed=True` but **not** dropped.

### 7. Derived business fields
Computed from the cleaned columns above:

- `is_revenue` (bool): `status_clean == "paid"`.
- `is_refund` (bool): `status_clean == "refunded"`.
- `list_price_usd`: lookup from `plan_clean` (`Starter`=49, `Pro`=99,
  `Enterprise`=299).
- `mrr_usd`: `list_price_usd × seats × (1 − discount_pct/100)` — computed
  for every row regardless of `billing_cycle_clean` (per docs: "annual
  invoices still map to the same underlying MRR — don't double-count").
- `recognized_revenue_usd`: `amount_usd` where `is_revenue`, else `0.0`.
- `net_revenue_usd`: `amount_usd` where `status_clean in {"paid","refunded"}`,
  else `0.0` (refunds are already negative, so this nets automatically).

### 8. Final null handling
- `region`, `industry`, `csat_score`, `contact_email_clean`,
  `payment_method_clean` may remain null — these are legitimately missing
  per the docs, not cleaned/imputed. No fabricated defaults.
- Required-for-analysis fields (`invoice_id`, `account_id`, `plan_clean`,
  `status_clean`, `amount_usd`, `invoice_date_parsed`) must be non-null after
  cleaning, or the row moves to rejects with `reject_reason="missing_required_field"`.

## Validation / tests
- Row-count reconciliation: `len(clean) + len(rejects) == len(raw) - full_dupes_dropped`
  — checked via assertion in `main()`. Implemented run: 5,125 raw → 82 full
  dupes dropped → 43 near-dupes dropped → 5,000 clean, 43 rejected.
- Totals validated against the earlier manual summary (pre-implementation
  baseline ≈ $120.0M paid / -$8.9M refunds): implemented run gives
  $120,145,168.20 paid / -$8,923,203.77 refunds — consistent, small shift
  expected from the near-dup tie-break resolution as predicted.

## Out of scope for this spec
- Building the RAG/NL-query agent itself (separate spec).
- Imputing missing `region`/`industry`/`csat_score` — explicitly left null.
- Currency-suffix vs `currency`-column reconciliation logic beyond "log and
  trust the column" — no stated business rule to do more.

## Open questions
1. Near-duplicate tie-break order (step 2) — implemented, still awaiting
   explicit confirmation, see `assumptions/ASSUMPTIONS.md` §3.
2. ~~Ambiguous-date assumption~~ — confirmed and implemented,
   `assumptions/ASSUMPTIONS.md` §1.
3. ~~`currency` column wins over inline amount-string suffix~~ — confirmed
   and implemented, `assumptions/ASSUMPTIONS.md` §2.
