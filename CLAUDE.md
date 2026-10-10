# CLAUDE.md — Bedrock + Strands RAG Agent

Project context for Claude Code. Keep this file targeted: it records commands,
constraints, and conventions Claude can't infer from the repo. Prune anything
that stops preventing a real mistake.

## AWS / Bedrock Rules

- Credentials come from the environment — never hardcode keys. Local dev:
  `AWS_BEARER_TOKEN_BEDROCK` (Bedrock API key) or a configured AWS profile.
  Deployed: IAM roles (ECS/Lambda/EC2). Do not add `aws configure` steps to code.
- **Region and model ID are config, never hardcoded literals.** Read `AWS_REGION`
  and `BEDROCK_MODEL_ID` from `.env`. Same for `temperature` / `max_tokens` —
  keep them in config, not scattered across modules.
- **Default/target model:** `us.anthropic.claude-sonnet-4-5-20250929-v1:0`.
  This is an inference-profile ID — **confirm it is actually available before
  hardcoding it** by running:
  `aws bedrock list-inference-profiles --region <region>`
  Use the profile ID that command returns; the region's geo prefix must match `AWS_REGION`.
- **Never commit AWS credentials.** `.env` is gitignored; `.env.example`
  documents every required var (`AWS_REGION`, `BEDROCK_MODEL_ID`, …) with
  placeholder values only. Keep the two in sync — if you add a var, add it to
  `.env.example`.
- **Never commit any credentials or secrets.
- Model/embedding access must be enabled in the Bedrock console first; surface a
  clear error if a model isn't enabled rather than silently falling back.

## Strands Conventions

- Tools are plain Python functions decorated with `@tool`. The **docstring and
  type hints are the tool's contract** — the model reads them to decide when and
  how to call it. Write a one-line summary, an `Args:` block, and a `Returns:`
  line for every tool. Keep them accurate and specific.
- Validate tool inputs and raise explicit errors on bad input (e.g. reject empty
  queries) — the agent can recover from a raised error, not from a silent wrong
  answer.
- Prefer Strands **vended tools** for file edits / shell / HTTP before writing a
  custom equivalent.
- Register tools via the `tools=[...]` list on `Agent`. Keep tool definitions in
  a `tools/` module, not inline in the entrypoint.
- Every invocation returns an `AgentResult` (messages, metrics, traces). Use it
  for debugging and evals; pass `callback_handler=None` to silence streamed
  console output in non-interactive runs.

### Agent + Bedrock wiring (canonical shape)

```python
import os
from strands import Agent
from strands.models import BedrockModel

model = BedrockModel(
    model_id=os.environ["BEDROCK_MODEL_ID"],   # from .env, confirmed via list-inference-profiles
    region_name=os.environ["AWS_REGION"],      # from .env
    # temperature / max_tokens also from config, not literals
)
agent = Agent(model=model, tools=[...])        # SDK runs the loop — don't hand-roll it
```

## Code Quality

- Add type hints to public functions and tool signatures.
- Use the `logging` module, not `print()`, in application code.
- Keep functions small and focused; match existing patterns before adding new ones.
- Touch only files needed for the task; don't refactor unrelated code unless asked.
- Handle exception paths explicitly — especially around Bedrock/network calls
  (throttling, access-denied, timeouts).

## Notes & Pitfalls

- Don't over-document. If Claude can read it from `pyproject.toml` or the code,
  it doesn't belong here.
- Model IDs, SDK APIs, and defaults change — when unsure about current Strands
  APIs, consult the Strands docs (or wire up the `strands-agents-mcp-server`
  MCP server for live doc access) rather than guessing.
