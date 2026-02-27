---
name: ask-human
description: Ask the human user a question and return their reply. Tool ask_human(question str, context str, mode str, timeout_seconds int) -> str. Use when you need user input (preferences, confirmations, missing parameters). Scripts ask.py (usage: python ask.py "Question" or echo "Question" | python ask.py).
skill_type: operator
---

# Ask Human Skill

Allows the agent to pause and ask the human a question, then continue with the user's reply.

## Tool

- **ask_human(question: str, context: str, mode: str="timeout", timeout_seconds: int=20) -> str**

  Ask the user a question with optional context; returns the user's input.

  Parameters:
  - `question` (required): The question to ask the user.
  - `context` (optional): Additional context to display alongside the question.
  - `mode` (optional): How long to wait for a reply.
    - `"timeout"` (default): Wait up to `timeout_seconds` seconds. If no reply arrives, the agent receives a system notice and should decide autonomously (skip / retry / abort). Use this for non-blocking scenarios where the workflow must not stall indefinitely.
    - `"block"`: Wait indefinitely until the user replies or the session is released. Use this only when human confirmation is strictly required before proceeding (e.g. high-cost or irreversible operations).
  - `timeout_seconds` (optional): Seconds to wait in `timeout` mode. Overrides the system default (configured in `mat_master.ask_human.timeout_seconds`). Ignored in `block` mode.

  > **Note**: The `disabled` state is a system-level configuration (`ask_human.enabled: false` in config.yaml). It cannot be set by the agent. When disabled, all ask-human calls short-circuit immediately without sending any event.

## Scripts

- **ask.py** — Prints the question (and optional context) to stdout as a JSON envelope so the callback can extract the question cleanly. The actual waiting for user input is handled by the backend callback pipeline, not by this script.

  Usage: `python ask.py "Your question"` or pass question as first argument; optional second argument is context.

## When to use

- When a decision requires human preference or confirmation.
- When a parameter is missing and must be supplied by the user.
- When the agent needs explicit approval before a destructive or costly action — use `mode="block"` in that case.
- After `monitor_job` returns `status='failed'` and you cannot identify the root cause from `log_tail` — use `mode="timeout"`, describe the failure and paste the relevant log lines. On timeout, abort the task with finish(task_completed=false).
