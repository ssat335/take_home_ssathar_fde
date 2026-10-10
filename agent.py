"""Strands agent that answers questions about CloudNova invoices via DuckDB.

See spec/query_agent_spec.md for the design and
assumptions/ASSUMPTIONS.md #4-#6 for the grain/CSAT/SQL-surface defaults
baked into the system prompt below.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv
from strands import Agent
from strands.models import BedrockModel

from tools.query_tool import query_invoices

load_dotenv()

SYSTEM_PROMPT = """\
You are a data analyst for CloudNova, a B2B SaaS company. Answer questions
about its invoices using the `query_invoices` tool, which runs read-only
SQL against the `invoices_clean` DuckDB table. The tool's own docstring
describes the schema and business rules — read it before writing SQL.

Rules you must follow:
- Always query `invoices_clean` via the tool. Never fabricate numbers.
- For any question about a quantity "per account" (e.g. average MRR per
  account, churn rate by plan), collapse to one row per account_id first
  (the row with the latest invoice_date_parsed for that account), per the
  tool's docstring — do not average or rate across raw invoice rows, that
  double-counts accounts with many invoices.
- For "total revenue" type questions, use recognized_revenue_usd (already
  zero for non-paid rows) or filter status_clean = 'paid' explicitly.
- Refunds are negative in amount_usd; when asked "how much have we given
  back in refunds," report the positive magnitude.
- After getting results, answer in plain language, and always state the
  exact SQL you ran so the answer can be audited.
- If a question is ambiguous (e.g. no year specified for a trend), say so
  and state the assumption you made.
"""


def build_agent(*, verbose: bool = True) -> Agent:
    model = BedrockModel(
        model_id=os.environ["BEDROCK_MODEL_ID"],
        region_name=os.environ["AWS_REGION"],
        temperature=float(os.environ.get("BEDROCK_TEMPERATURE", "0.0")),
        max_tokens=int(os.environ.get("BEDROCK_MAX_TOKENS", "2048")),
    )
    kwargs = {} if verbose else {"callback_handler": None}
    return Agent(
        model=model,
        tools=[query_invoices],
        system_prompt=SYSTEM_PROMPT,
        **kwargs,
    )


def ask(question: str, *, verbose: bool = True) -> str:
    agent = build_agent(verbose=verbose)
    result = agent(question)
    return str(result)
