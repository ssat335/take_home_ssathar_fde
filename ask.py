"""Command-line entry point for the CloudNova invoice query agent.

Usage:
    python ask.py "What was our total recognized revenue in USD for paid invoices in 2024?"
    python ask.py --quiet "How much have we given back in refunds?"
"""

from __future__ import annotations

import argparse
import logging
import sys

from agent import ask


def main() -> None:
    parser = argparse.ArgumentParser(description="Ask a question about CloudNova invoices.")
    parser.add_argument("question", help="Natural-language question to ask.")
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress the agent's streamed tool-call output; print only the final answer.",
    )
    parser.add_argument(
        "--log-level",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level for query_tool/load_duckdb (default: WARNING).",
    )
    args = parser.parse_args()

    logging.basicConfig(level=args.log_level, format="%(levelname)s: %(message)s")

    answer = ask(args.question, verbose=not args.quiet)
    print()
    print(answer)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - top-level CLI error surface
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
