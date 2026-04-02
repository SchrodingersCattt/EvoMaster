---
name: ask-human
description: Ask the human user a question and return their reply. Tool ask_human(question str, context str) -> str. Use when you need user input (preferences, confirmations, missing parameters). Scripts ask.py (usage: python ask.py "Question" or echo "Question" | python ask.py).
skill_type: operator
---

# Ask Human Skill

Allows the agent to pause and ask the human a question, then continue with the user's reply.

## Tool

- **ask_human(question: str, context: str) -> str**

  Ask the user a question with optional context; returns the user's input.

  Parameters:
  - `question` (required): The question to ask the user.
  - `context` (optional): Additional context to display alongside the question.

  Wait behavior (how long to wait for a reply) is controlled by **server configuration** (`mat_master.ask_human.mode` and `timeout_seconds` in config.yaml), not by the agent. When disabled (`ask_human.enabled: false`), all ask-human calls short-circuit immediately without sending any event.

## Scripts

- **ask.py** — Prints the question (and optional context) to stdout as a JSON envelope so the callback can extract the question cleanly. The actual waiting for user input is handled by the backend callback pipeline, not by this script.

  Usage: `python ask.py "Your question"` or pass question as first argument; optional second argument is context.

## When to use

- When a decision requires human preference or confirmation.
- When a parameter is missing and must be supplied by the user.
- When the agent needs explicit approval before a destructive or costly action.
- After `monitor_job` returns `status='failed'` and you cannot identify the root cause from `log_tail` — describe the failure and paste the relevant log lines; on timeout, abort the task with finish(task_completed=false).
