# Testing Patterns

**Analysis Date:** 2026-03-21

## Test Framework

**Runner:**
- pytest 9.0.2+
- Config: `/Users/kealdoom/Developer/dp/matmaster/matmaster-evo/.worktrees/refactor-v2/pytest.ini`

**Configuration Details:**
```ini
[pytest]
minversion = 1.0
addopts = -s                    # Show print output
pythonpath = .                  # Add root to path for imports
asyncio_mode = auto             # Auto-detect async tests
testpaths = tests               # Look for tests in ./tests/
```

**Assertion Library:**
- Python standard `assert` statements
- Comparison using `==` for equality checks

**Run Commands:**
```bash
pytest                          # Run all tests in tests/ directory
pytest tests/test_specific.py   # Run single test file
pytest -k test_name             # Run tests matching pattern
pytest -s                       # Show print/log output (default with addopts)
pytest --asyncio-mode=auto      # Async test support enabled
```

## Test File Organization

**Location:**
- Tests live in `/tests/` directory at project root
- Separate from source code: `tests/` not alongside `src/`

**Naming:**
- `test_*.py` files are test modules
- Test functions: `def test_*(...)`
- Test classes: `class Test*:` (less common, functional tests preferred)

**File Structure:**
```
tests/
├── test_llm_reasoning_stream.py
├── test_llm_reasoning_response.py
├── test_llm_thinking_adapters.py
├── test_streaming_thought_protocol.py
├── test_chat_stream_direct.py
├── test_chat_history_reasoning_state.py
├── test_chat_event_source.py
├── test_builtin_tools_without_think.py
├── test_evomaster_config_migration.py
├── test_reasoning_state_roundtrip.py
└── __init__.py
```

## Test Structure

**Suite Organization:**

Tests are functional (not class-based) with fixtures and helpers as functions. Example pattern:

```python
# test_llm_reasoning_stream.py
import logging
from types import SimpleNamespace
from evomaster.utils.llm import LLMConfig, OpenAILLM
from evomaster.utils.types import Dialog, UserMessage

def _make_chunk(*, reasoning_content=None, content=None, finish_reason=None):
    """Helper to construct mock chunk objects."""
    delta = SimpleNamespace(...)
    choice = SimpleNamespace(...)
    return SimpleNamespace(choices=[choice])

class _CreateStub:
    """Mock for OpenAI client.chat.completions."""
    def __init__(self, chunks):
        self._chunks = chunks
    def create(self, **kwargs):
        return self._chunks

def test_openai_stream_accumulates_reasoning_content():
    """Test that streaming LLM correctly accumulates reasoning."""
    llm = OpenAILLM.__new__(OpenAILLM)
    llm.config = LLMConfig(...)
    llm.logger = logging.getLogger(__name__)
    llm.client = SimpleNamespace(...)

    deltas = []
    msg = llm.query_stream(Dialog(...), on_token=deltas.append)

    assert deltas == ['r1', 'r2']
    assert msg.content == 'final answer'
    assert msg.meta['reasoning_content'] == 'r1r2'
```

**Patterns:**

1. **Setup Pattern:**
   - Inline initialization: Create test objects inside test function
   - Mock objects using `SimpleNamespace` for simple stubs
   - `MagicMock` from `unittest.mock` for more complex mocking
   - Patches applied at function scope via context managers

2. **Fixture Pattern:**
   - Helper functions prefixed with underscore: `_mock_sessions_table()`, `_check_quota_noop()`
   - Return mock objects configured for test scenarios
   - Reused across multiple tests in same file

3. **Assertion Pattern:**
   - Direct `assert` statements: `assert deltas == ['r1', 'r2']`
   - Multiple assertions per test to verify all aspects
   - Test names describe what is being asserted: `test_openai_stream_accumulates_reasoning_content`

4. **Teardown Pattern:**
   - Minimal explicit teardown (pytest handles cleanup)
   - For patches, use context manager or `patch.start()/stop()` manually within try/finally
   - Example from `test_chat_stream_direct.py`:
     ```python
     for p in patches:
         p.start()
     try:
         # test code
     finally:
         for p in patches:
             p.stop()
     ```

## Mocking

**Framework:** `unittest.mock` (standard library)

**Patterns:**

1. **Simple Stub with SimpleNamespace:**
   ```python
   llm.client = SimpleNamespace(
       chat=SimpleNamespace(
           completions=_CreateStub([...])
       )
   )
   ```

2. **MagicMock for Complex Objects:**
   ```python
   mock_sessions = MagicMock()
   mock_sessions.get_session.return_value = None
   mock_sessions.create_session.return_value = None
   ```

3. **Patch Decorator/Context Manager:**
   ```python
   patches = [
       patch('src.apis.chat_api.REDIS_URL', None),
       patch('src.services.sessions_service.get_chat_sessions_table',
             return_value=mock_sessions),
   ]
   for p in patches:
       p.start()
   ```

4. **Custom Mock Classes:**
   ```python
   class _NoDbConnection:
       """Placeholder context manager to block real DB connections."""
       def __enter__(self):
           raise RuntimeError('DB disabled in test (use mock tables only)')
       def __exit__(self, *args):
           pass
   ```

**What to Mock:**
- External service clients (OpenAI LLM, Redis, database connections)
- Configuration values (environment variables, REDIS_URL)
- File I/O for filesystem operations

**What NOT to Mock:**
- Dataclass constructors and simple data containers
- Internal functions and methods (test the integration)
- Standard library functions (use mocks only for actual I/O)

## Fixtures and Factories

**Test Data:**

Fixture functions return mock objects configured for specific scenarios:

```python
def _mock_sessions_table():
    """Factory for mocked chat sessions table."""
    t = MagicMock()
    t.get_session.return_value = None
    t.create_session.return_value = None
    t.set_session_status.return_value = True
    # ... more setup ...
    return t

async def _check_quota_noop(user_id: str) -> int:
    """Async fixture returning dummy quota."""
    return 10
```

**Location:**
- Fixture functions in same test file (not in conftest.py observed)
- Private fixtures prefixed with underscore
- Organized at top of file after imports, before tests

**Pattern:**
- Factories return fully configured mock objects
- Test functions call fixtures to get test objects
- Minimal setup in test itself; complexity in fixtures

## Coverage

**Requirements:**
- Not explicitly enforced (no coverage threshold in pytest.ini)
- Tests are written for critical paths (LLM streaming, event protocol, chat integration)

**View Coverage:**
```bash
pytest --cov=playground.mat_master --cov-report=term-missing tests/
```

## Test Types

**Unit Tests:**
- Scope: Single function or small component (LLM handler, message conversion)
- Approach: Mock all external dependencies
- Example: `test_openai_stream_accumulates_reasoning_content` verifies streaming behavior with mocked client
- Example: `test_llm_response_to_assistant_message_preserves_reasoning` verifies data transformation

**Integration Tests:**
- Scope: Multiple components working together (chat stream, events, sessions)
- Approach: Mock external services (DB, Redis) but use real code paths
- Example: `test_chat_stream_returns_503_when_redis_url_missing` verifies API behavior with mocked tables
- Database mocking: Use `MagicMock()` with configured return values instead of real DB

**E2E Tests:**
- Framework: Not explicitly used (no test runner like Playwright/Cypress found)
- Manual testing or CI pipeline integration tests may exist outside pytest

## Common Patterns

**Async Testing:**

pytest's `asyncio_mode = auto` enables async test detection. Functions defined as `async def` are automatically run with event loop:

```python
async def _check_quota_noop(user_id: str) -> int:
    """Async fixture (called automatically by pytest-asyncio)."""
    return 10
```

**Error Testing:**

Tests verify both success and error paths. Example pattern:

```python
def test_function_returns_false_when_skill_md_missing():
    """Verify validation fails appropriately."""
    skill_path = Path('/nonexistent/skill')
    # setup for missing file
    result = register_dynamic_skill(skill_path)
    assert result is False
```

**Mocking with Context Managers:**

For tests requiring multiple patches with teardown:

```python
patches = [
    patch('module.CONSTANT', value),
    patch('module.function', side_effect=mock_function),
]
for p in patches:
    p.start()

try:
    # test code using patched modules
    from module import something
    # assertions
finally:
    for p in patches:
        p.stop()
```

**Incremental Fixture Setup:**

Helper functions build up test state:

```python
mock_sessions = _mock_sessions_table()  # base mock
mock_sessions.get_session.return_value = {  # customize for test
    'id': 'session-123',
    'user_id': 'test-user'
}

# use configured mock in test
```

---

*Testing analysis: 2026-03-21*
