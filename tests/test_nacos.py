import os
import sys
from pathlib import Path

# Ensure project root is on sys.path when running as a script: `python tests/test_nacos.py`
PROJECT_ROOT = str(Path(__file__).resolve().parents[1])
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from dotenv import find_dotenv, load_dotenv  # noqa: E402

load_dotenv(find_dotenv('.env.test'))
from src.utils.nacos import get_cached_email_list, is_email_allowlisted  # noqa: E402


def main() -> None:
    email_list_result = get_cached_email_list(cache_ttl_seconds=5)
    print(f"email_list_fetch_ok: {email_list_result.is_ok}")
    if not email_list_result.is_ok:
        print(f"email_list_error: {email_list_result.error}")
        return

    email_list = email_list_result.email_list
    print(f"email_list_results: {email_list_result}")
    print(f"email_list_entries: {email_list}")

    sample_email = os.getenv('NACOS_TEST_EMAIL', 'user@dp.tech')
    is_allowlisted = is_email_allowlisted(email=sample_email, email_list=email_list)
    daily_quota = 100 if is_allowlisted else 10
    print(
        f"email={sample_email} allowlisted={is_allowlisted} daily_quota={daily_quota}"
    )


if __name__ == '__main__':
    main()
