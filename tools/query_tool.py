"""Strands tool: run read-only SQL against the cleaned invoice DuckDB table.

See spec/query_agent_spec.md "Safety / validation" for the rules enforced
here, and assumptions/ASSUMPTIONS.md #6 for why the SQL surface is
permissive on columns/aggregations but strict on statement type.
"""

from __future__ import annotations

import logging
import re

import duckdb
from strands import tool

from load_duckdb import DUCKDB_PATH, ensure_loaded

logger = logging.getLogger(__name__)

MAX_ROWS = 500

_FORBIDDEN_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|ATTACH|DETACH|COPY|PRAGMA|CREATE|"
    r"REPLACE|TRUNCATE|GRANT|CALL|EXPORT|IMPORT|VACUUM|SET)\b",
    re.IGNORECASE,
)


def _validate_sql(sql: str) -> str:
    """Raise ValueError if `sql` isn't a single read-only SELECT/WITH query."""
    if not sql or not sql.strip():
        raise ValueError("sql must not be empty")

    cleaned = sql.strip()
    # Allow exactly one optional trailing semicolon; reject multi-statement input.
    body = cleaned[:-1] if cleaned.endswith(";") else cleaned
    if ";" in body:
        raise ValueError("Only a single SQL statement is allowed")

    if not re.match(r"^\s*(SELECT|WITH)\b", body, re.IGNORECASE):
        raise ValueError("Only SELECT/WITH (read-only) queries are allowed")

    if _FORBIDDEN_KEYWORDS.search(body):
        raise ValueError("Query contains a forbidden keyword (DDL/DML not allowed)")

    return body


@tool
def query_invoices(sql: str) -> dict:
    """Run a read-only SQL SELECT query against the cleaned CloudNova invoice data.

    The table is named `invoices_clean`, one row per invoice, with columns:
    invoice_id, account_id, account_name, region, industry, plan_clean,
    billing_cycle_clean, seats, currency, amount_usd, discount_pct,
    status_clean, payment_method_clean, signup_date_parsed,
    invoice_date_parsed, churned_clean, csat_score, support_tickets,
    list_price_usd, mrr_usd, is_revenue, is_refund, recognized_revenue_usd,
    net_revenue_usd, date_anomaly.

    Business rules baked into this table (don't re-derive them):
    - Recognized revenue = rows where status_clean = 'paid'
      (recognized_revenue_usd is amount_usd for those rows, else 0).
    - Refunds are status_clean = 'refunded' with a NEGATIVE amount_usd;
      net_revenue_usd already nets paid + refunded together.
    - mrr_usd is already computed per invoice
      (list_price_usd * seats * (1 - discount_pct/100)); don't double-count
      by summing it across an account's many invoices — average or take one
      snapshot row per account instead.
    - An account appears on MANY invoice rows. For "per account" questions
      (e.g. average MRR per account, churn rate), first collapse to one row
      per account_id (e.g. the row with MAX(invoice_date_parsed) for that
      account) before aggregating — otherwise accounts with more invoices
      are over-weighted.
    - churned_clean and csat_score are booleans/scores per invoice row, not
      guaranteed identical across an account's invoices.
    - When flagging an account by CSAT (e.g. "flag accounts with CSAT <= 2"),
      flag it if ANY of its invoices has csat_score <= 2
      (i.e. MIN(csat_score) <= 2 across all of that account's rows) — do
      not use only the latest invoice's CSAT for this.

    Only SELECT/WITH statements are permitted; results are capped at 500 rows.

    Args:
        sql: A single read-only SQL SELECT (or WITH ... SELECT) statement
            querying the `invoices_clean` table.

    Returns:
        A dict with:
        - "rows": list of result rows as dicts (column name -> value),
          capped at 500 rows.
        - "row_count": number of rows returned (post-cap).
        - "truncated": True if the result was capped.
        - "sql": the exact SQL that was executed, for citation.
    """
    validated_sql = _validate_sql(sql)
    ensure_loaded()

    logger.info("Executing query: %s", validated_sql)
    con = duckdb.connect(str(DUCKDB_PATH), read_only=True)
    try:
        rel = con.execute(validated_sql)
        columns = [desc[0] for desc in rel.description]
        fetched = rel.fetchmany(MAX_ROWS + 1)
    finally:
        con.close()

    truncated = len(fetched) > MAX_ROWS
    fetched = fetched[:MAX_ROWS]
    rows = [dict(zip(columns, row)) for row in fetched]

    logger.info("Query returned %d row(s) (truncated=%s)", len(rows), truncated)
    return {
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
        "sql": validated_sql,
    }
