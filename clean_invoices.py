"""Clean the CloudNova raw invoice export per spec/clean_invoices_spec.md.

Usage:
    python clean_invoices.py
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent / "data"
RAW_PATH = DATA_DIR / "cloudnova_invoices.csv"
CLEAN_PATH = DATA_DIR / "cloudnova_invoices_clean.csv"
REJECTS_PATH = DATA_DIR / "cloudnova_invoices_rejects.csv"

FX_TO_USD = {"USD": 1.0, "EUR": 1.08, "GBP": 1.27}
LIST_PRICE_USD = {"Starter": 49.0, "Pro": 99.0, "Enterprise": 299.0}

PLAN_MAP = {
    "starter": "Starter", "start": "Starter", "tier 1": "Starter",
    "pro": "Pro", "professional": "Pro", "tier 2": "Pro",
    "enterprise": "Enterprise", "ent": "Enterprise", "tier 3": "Enterprise",
}
STATUS_MAP = {
    "paid": "paid", "complete": "paid", "completed": "paid", "success": "paid",
    "pending": "pending", "in_review": "pending", "awaiting": "pending",
    "failed": "failed", "declined": "failed", "error": "failed",
    "refunded": "refunded", "refund": "refunded", "charged_back": "refunded",
    "void": "void", "cancelled": "void", "canceled": "void",
}
CYCLE_MAP = {
    "monthly": "monthly",
    "annual": "annual", "annually": "annual", "yearly": "annual",
}
PAYMENT_METHOD_MAP = {
    "credit_card": "credit_card", "credit card": "credit_card",
    "ach": "ach",
    "wire": "wire", "wire transfer": "wire",
    "invoice": "invoice",
}
CHURNED_TRUE = {"1", "y", "yes", "true"}
CHURNED_FALSE = {"0", "n", "no", "false"}

STATUS_PRIORITY = {"paid": 0, "refunded": 0, "failed": 1, "void": 1, "pending": 2, "unknown": 3}

REQUIRED_FIELDS = [
    "invoice_id", "account_id", "plan_clean", "status_clean",
    "amount_usd", "invoice_date_parsed",
]

AMOUNT_SUFFIX_RE = re.compile(r"\s*([A-Z]{3})$")
DASH_DATE_RE = re.compile(r"^(\d{2})-(\d{2})-(\d{4})$")


def norm_plan(raw: str) -> str:
    return PLAN_MAP.get(raw.strip().lower(), "unknown")


def norm_status(raw: str) -> str:
    return STATUS_MAP.get(raw.strip().lower(), "unknown")


def norm_cycle(raw: str) -> str:
    return CYCLE_MAP.get(raw.strip().lower(), "unknown")


def norm_payment_method(raw: str | float) -> str | None:
    if pd.isna(raw) or str(raw).strip() == "":
        return None
    return PAYMENT_METHOD_MAP.get(str(raw).strip().lower(), "unknown")


def norm_churned(raw: str) -> bool | None:
    val = raw.strip().lower()
    if val in CHURNED_TRUE:
        return True
    if val in CHURNED_FALSE:
        return False
    return None


def parse_amount(raw: str) -> tuple[float | None, str | None]:
    """Parse a dirty amount string into (value, inline_currency_suffix)."""
    s = raw.strip()
    suffix_match = AMOUNT_SUFFIX_RE.search(s)
    suffix = None
    if suffix_match:
        suffix = suffix_match.group(1)
        s = s[: suffix_match.start()]
    s = s.replace(",", "").replace("$", "").replace("£", "").replace("€", "").strip()
    try:
        return float(s), suffix
    except ValueError:
        return None, suffix


def parse_date(raw: str) -> date | None:
    """Parse a dirty date string. Ambiguous DD-MM vs MM-DD dashes assumed
    MM-DD-YYYY per assumptions/ASSUMPTIONS.md #1."""
    s = raw.strip()
    fmt = None
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        fmt = "%Y-%m-%d"
    elif re.match(r"^\d{4}/\d{2}/\d{2}$", s):
        fmt = "%Y/%m/%d"
    elif re.match(r"^\d{2}/\d{2}/\d{4}$", s):
        fmt = "%m/%d/%Y"
    elif (m := DASH_DATE_RE.match(s)) is not None:
        first = int(m.group(1))
        # First component > 12 is unambiguously DD-MM-YYYY. Otherwise this is
        # the genuinely-ambiguous case, assumed MM-DD-YYYY per
        # assumptions/ASSUMPTIONS.md #1.
        fmt = "%d-%m-%Y" if first > 12 else "%m-%d-%Y"
    elif re.match(r"^[A-Za-z]{3} \d{1,2} \d{4}$", s):
        fmt = "%b %d %Y"
    if fmt is None:
        return None
    try:
        return datetime.strptime(s, fmt).date()
    except ValueError:
        return None


def is_ambiguous_dash_date(raw: str) -> bool:
    m = DASH_DATE_RE.match(raw.strip())
    if not m:
        return False
    a, b = int(m.group(1)), int(m.group(2))
    return a <= 12 and b <= 12 and a != b


def resolve_near_duplicates(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Keep one row per invoice_id. Tie-break: most complete row, then
    terminal status over pending, then first-seen. See
    assumptions/ASSUMPTIONS.md #3."""
    blank_count = df.isna().sum(axis=1) + (df == "").sum(axis=1)
    status_rank = df["status"].str.strip().str.lower().map(STATUS_MAP).fillna("unknown").map(STATUS_PRIORITY)
    order = pd.DataFrame({
        "blank_count": blank_count,
        "status_rank": status_rank,
        "orig_order": np.arange(len(df)),
    }, index=df.index)
    sort_key = order.sort_values(["blank_count", "status_rank", "orig_order"]).index
    df_sorted = df.loc[sort_key]
    keep_mask = ~df_sorted["invoice_id"].duplicated(keep="first")
    kept = df_sorted[keep_mask].sort_index()
    dropped = df_sorted[~keep_mask].copy()
    dropped["reject_reason"] = "near_duplicate_invoice_id"
    return kept, dropped


def clean(raw: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rejects_parts: list[pd.DataFrame] = []

    n_raw = len(raw)
    df = raw.drop_duplicates()
    logger.info("Dropped %d full-duplicate rows", n_raw - len(df))

    df, near_dupe_rejects = resolve_near_duplicates(df)
    logger.info("Dropped %d near-duplicate invoice_id rows", len(near_dupe_rejects))
    if len(near_dupe_rejects):
        rejects_parts.append(near_dupe_rejects)

    df = df.copy()

    df["plan_clean"] = df["plan"].apply(norm_plan)
    df["status_clean"] = df["status"].apply(norm_status)
    df["billing_cycle_clean"] = df["billing_cycle"].apply(norm_cycle)
    df["payment_method_clean"] = df["payment_method"].apply(norm_payment_method)
    df["churned_clean"] = df["churned"].apply(norm_churned)

    for col in ("plan_clean", "status_clean", "billing_cycle_clean"):
        n_unknown = (df[col] == "unknown").sum()
        if n_unknown:
            logger.warning("%d rows have unmapped %s value", n_unknown, col)

    amounts = df["amount"].apply(parse_amount)
    df["amount_value"] = amounts.apply(lambda t: t[0])
    df["amount_suffix_currency"] = amounts.apply(lambda t: t[1])
    mismatch = df["amount_suffix_currency"].notna() & (df["amount_suffix_currency"] != df["currency"])
    if mismatch.any():
        logger.warning(
            "%d rows have amount-string currency suffix disagreeing with `currency` column; "
            "trusting `currency` column per assumptions/ASSUMPTIONS.md #2",
            mismatch.sum(),
        )
    df["amount_usd"] = df["amount_value"] * df["currency"].map(FX_TO_USD)

    df["signup_date_parsed"] = df["signup_date"].apply(parse_date)
    df["invoice_date_parsed"] = df["invoice_date"].apply(parse_date)
    df["date_anomaly"] = None
    ambiguous_mask = df["invoice_date"].apply(is_ambiguous_dash_date) | df["signup_date"].apply(is_ambiguous_dash_date)
    df.loc[ambiguous_mask, "date_anomaly"] = "ambiguous_date_assumed_mdy"
    before_signup_mask = (
        df["invoice_date_parsed"].notna()
        & df["signup_date_parsed"].notna()
        & (df["invoice_date_parsed"] < df["signup_date_parsed"])
    )
    df.loc[before_signup_mask, "date_anomaly"] = "invoice_before_signup"
    future_mask = df["invoice_date_parsed"].notna() & (df["invoice_date_parsed"] > date.today())
    df.loc[future_mask, "date_anomaly"] = "future_dated"
    logger.info("Ambiguous dash-format dates (kept, flagged): %d", ambiguous_mask.sum())
    logger.info("Invoice-before-signup anomalies (kept, flagged): %d", before_signup_mask.sum())
    logger.info("Future-dated invoices (kept, flagged): %d", future_mask.sum())

    def clean_email(raw: object) -> str | None:
        if pd.isna(raw) or str(raw).strip() == "":
            return None
        return str(raw).strip().replace("_at_", "@")

    df["contact_email_clean"] = df["contact_email"].apply(clean_email)

    email_pattern = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

    def is_email_malformed(v: object) -> bool:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return False
        return not bool(email_pattern.match(str(v)))

    df["email_malformed"] = df["contact_email_clean"].apply(is_email_malformed)

    df["list_price_usd"] = df["plan_clean"].map(LIST_PRICE_USD)
    seats = pd.to_numeric(df["seats"], errors="coerce")
    discount_pct = pd.to_numeric(df["discount_pct"], errors="coerce")
    df["mrr_usd"] = df["list_price_usd"] * seats * (1 - discount_pct / 100)
    df["is_revenue"] = df["status_clean"] == "paid"
    df["is_refund"] = df["status_clean"] == "refunded"
    df["recognized_revenue_usd"] = df["amount_usd"].where(df["is_revenue"], 0.0)
    df["net_revenue_usd"] = df["amount_usd"].where(df["status_clean"].isin(["paid", "refunded"]), 0.0)

    unparseable_date = df["invoice_date_parsed"].isna()
    missing_required = df[REQUIRED_FIELDS].isna().any(axis=1) | (df["plan_clean"] == "unknown") | (df["status_clean"] == "unknown")
    reject_mask = unparseable_date | missing_required
    if reject_mask.any():
        bad = df[reject_mask].copy()
        bad["reject_reason"] = np.where(
            unparseable_date[reject_mask], "unparseable_date", "missing_required_field"
        )
        rejects_parts.append(bad)
        logger.info("Moved %d rows to rejects for missing/invalid required fields", len(bad))
    df = df[~reject_mask]

    rejects = pd.concat(rejects_parts, ignore_index=True) if rejects_parts else pd.DataFrame()
    return df, rejects


def main() -> None:
    raw = pd.read_csv(RAW_PATH, dtype=str)
    logger.info("Loaded %d raw rows from %s", len(raw), RAW_PATH)

    clean_df, rejects_df = clean(raw)

    clean_df.to_csv(CLEAN_PATH, index=False)
    rejects_df.to_csv(REJECTS_PATH, index=False)

    logger.info("Wrote %d clean rows to %s", len(clean_df), CLEAN_PATH)
    logger.info("Wrote %d rejected rows to %s", len(rejects_df), REJECTS_PATH)
    assert len(clean_df) + len(rejects_df) + (len(raw) - len(raw.drop_duplicates())) >= len(raw) - 0, \
        "row-count reconciliation sanity check failed"

    recognized = clean_df.loc[clean_df["is_revenue"], "amount_usd"].sum()
    refunds = clean_df.loc[clean_df["is_refund"], "amount_usd"].sum()
    logger.info("Recognized revenue (paid, USD): %.2f", recognized)
    logger.info("Refunds (USD): %.2f", refunds)
    logger.info("Net revenue (paid + refunds, USD): %.2f", recognized + refunds)


if __name__ == "__main__":
    main()
