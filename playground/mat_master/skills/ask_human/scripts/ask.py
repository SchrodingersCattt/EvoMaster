"""
Ask Human Script: format and print a question for the user.

The actual user interaction (waiting for reply) is handled by the agent's
callback pipeline, NOT by this script.  This script simply outputs a JSON
envelope so the callback can extract the question cleanly.

Usage:
  python ask.py "Your question"
  python ask.py "Your question" "Optional context"
  echo "Your question" | python ask.py

Output: a JSON object with keys ``question`` and ``context``.
"""

import json
import sys


def main() -> None:
    if len(sys.argv) >= 2:
        question = sys.argv[1]
        context = sys.argv[2] if len(sys.argv) >= 3 else None
    else:
        try:
            question = sys.stdin.read().strip() or "Please provide input:"
            context = None
        except Exception:
            question = "Please provide input:"
            context = None

    # Output structured JSON so the callback can parse it reliably.
    payload = {"question": question}
    if context:
        payload["context"] = context
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
