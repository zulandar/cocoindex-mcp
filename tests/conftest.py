"""Fixtures for testing mcp_server.py without its heavy dependencies."""

import importlib.util
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
MCP_SERVER_PATH = TEMPLATE_DIR / "mcp_server.py"

# Modules mcp_server.py imports that aren't available in CI
MOCKED_MODULES = [
    "asyncpg",
    "cocoindex",
    "cocoindex.ops",
    "cocoindex.ops.sentence_transformers",
    "dotenv",
    "main",
    "mcp",
    "mcp.server",
    "mcp.server.fastmcp",
    "pgvector",
    "pgvector.asyncpg",
]

VALID_CONFIG = {
    "project": "testproject",
    "port": 5434,
    "patterns": {
        "included": ["**/*.py"],
        "excluded": ["**/.git"],
    },
}


@pytest.fixture(scope="session")
def mcp_server_module():
    """Import templates/mcp_server.py with all heavy deps mocked out."""
    saved = {}
    for mod_name in MOCKED_MODULES:
        if mod_name in sys.modules:
            saved[mod_name] = sys.modules[mod_name]

    for mod_name in MOCKED_MODULES:
        sys.modules[mod_name] = MagicMock()

    fast_mcp_instance = MagicMock()
    fast_mcp_instance.tool.return_value = lambda fn: fn
    sys.modules["mcp.server.fastmcp"].FastMCP.return_value = fast_mcp_instance
    sys.modules["dotenv"].load_dotenv = MagicMock()

    # main.py exports we depend on
    sys.modules["main"].EMBED_MODEL = "test-model"
    sys.modules["main"].PG_SCHEMA_NAME = "testproject_cocoindex"
    sys.modules["main"].TABLE_NAME = "code_embeddings"

    env_key = "POSTGRES_URL"
    had_env = env_key in os.environ
    old_env = os.environ.get(env_key)
    os.environ[env_key] = "postgresql://test:test@localhost:5432/test"

    with (
        patch("yaml.safe_load", return_value=VALID_CONFIG),
        patch("builtins.open", MagicMock()),
    ):
        spec = importlib.util.spec_from_file_location("mcp_server", MCP_SERVER_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

    yield module

    if had_env:
        os.environ[env_key] = old_env
    else:
        os.environ.pop(env_key, None)

    for mod_name in MOCKED_MODULES:
        if mod_name in saved:
            sys.modules[mod_name] = saved[mod_name]
        else:
            sys.modules.pop(mod_name, None)


@pytest.fixture()
def mock_pool_and_embedder(mcp_server_module):
    """Mocks asyncpg pool and embedder for async tests."""
    mock_conn = MagicMock()
    mock_conn.fetch = AsyncMock()

    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_embedder = MagicMock()
    mock_embedder.embed = AsyncMock(return_value=[0.0] * 384)

    # Inject lazy singletons directly to bypass _get_pool() / _get_embedder()
    mcp_server_module._pool = mock_pool
    mcp_server_module._embedder = mock_embedder

    return mock_conn, mock_embedder
