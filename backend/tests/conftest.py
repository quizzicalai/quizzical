# tests/conftest.py
import os
import pytest

# Keep tests in a predictable local/dev path
os.environ.setdefault("APP_ENVIRONMENT", "local")
os.environ.setdefault("USE_MEMORY_SAVER", "1")
os.environ.setdefault("ENABLE_TURNSTILE", "false")

# Load all shared fixtures as pytest plugins (explicit is better than implicit)
pytest_plugins = [
    "tests.fixtures.db_fixtures",          # DB: null + sqlite, and dependency overrides
    "tests.fixtures.http_client",          # ASGI httpx AsyncClient (lifespan-aware)
    "tests.fixtures.redis_fixtures",       # fake_redis + override_redis_dep + helpers
    "tests.fixtures.background_tasks",     # capture_background_tasks  (if present in repo)
    "tests.fixtures.agent_graph_fixtures", # use_fake_agent_graph      (if present in repo)
    "tests.fixtures.llm_fixtures",         # fake LLM + tool patches   (if present in repo)
    "tests.fixtures.settings_fixtures",    # override settings         (if present in repo)
    "tests.fixtures.id_fixtures",           # uuid4 patching            (if present in repo)
    "tests.fixtures.tool_fixtures",       # override tool deps        (if present in repo)
    "tests.fixtures.turnstile_fixtures",  # override turnstile checks (if present in repo)
]

@pytest.fixture(scope="session")
def anyio_backend():
    # Force AnyIO’s pytest plugin to use asyncio only
    return "asyncio"

def pytest_addoption(parser):
    parser.addoption(
        "--live-tools",
        action="store_true",
        default=False,
        help="Call real tool backends (OpenAI, etc.) instead of stubs.",
    )