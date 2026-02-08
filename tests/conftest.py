"""Fixtures for testing mcp_server.py without its heavy dependencies."""

import importlib
import importlib.util
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
MCP_SERVER_PATH = TEMPLATE_DIR / "mcp_server.py"

# Modules that mcp_server.py imports but aren't available in CI
MOCKED_MODULES = [
    "cocoindex",
    "cocoindex.utils",
    "dotenv",
    "mcp",
    "mcp.server",
    "mcp.server.fastmcp",
    "psycopg_pool",
    "main",
]

VALID_CONFIG = {
    "project": "testproject",
    "port": 5434,
    "patterns": {
        "included": ["*.py"],
        "excluded": [".git"],
    },
}


@pytest.fixture(scope="session")
def mcp_server_module():
    """Import templates/mcp_server.py with all heavy deps mocked out."""
    saved = {}
    injected = {}

    # Save any pre-existing entries so we can restore them
    for mod_name in MOCKED_MODULES:
        if mod_name in sys.modules:
            saved[mod_name] = sys.modules[mod_name]

    # Build mock modules
    for mod_name in MOCKED_MODULES:
        mock = MagicMock()
        sys.modules[mod_name] = mock
        injected[mod_name] = mock

    # Make FastMCP().tool() a passthrough decorator so decorated fns stay callable
    fast_mcp_instance = MagicMock()
    fast_mcp_instance.tool.return_value = lambda fn: fn
    sys.modules["mcp.server.fastmcp"].FastMCP.return_value = fast_mcp_instance

    # Make dotenv.load_dotenv a no-op
    sys.modules["dotenv"].load_dotenv = MagicMock()

    # Mock cocoindex.init and cocoindex.utils
    sys.modules["cocoindex"].init = MagicMock()
    sys.modules["cocoindex"].utils.get_target_storage_default_name.return_value = (
        "test_table"
    )

    # Set env var that mcp_server.py reads at module level
    env_key = "COCOINDEX_DATABASE_URL"
    had_env = env_key in os.environ
    old_env = os.environ.get(env_key)
    os.environ[env_key] = "postgresql://test:test@localhost:5432/test"

    # Patch yaml.safe_load to return valid config, and open() to succeed
    with (
        patch("yaml.safe_load", return_value=VALID_CONFIG),
        patch("builtins.open", MagicMock()),
    ):
        spec = importlib.util.spec_from_file_location("mcp_server", MCP_SERVER_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

    yield module

    # Cleanup env
    if had_env:
        os.environ[env_key] = old_env
    else:
        os.environ.pop(env_key, None)

    # Cleanup sys.modules
    for mod_name in MOCKED_MODULES:
        if mod_name in saved:
            sys.modules[mod_name] = saved[mod_name]
        else:
            sys.modules.pop(mod_name, None)


@pytest.fixture()
def mock_pool(mcp_server_module):
    """Provide a mock connection pool whose cursor returns configurable rows."""
    mock_conn = MagicMock()
    mock_cur = MagicMock()

    # Wire up context managers: pool.connection() -> conn, conn.cursor() -> cur
    mcp_server_module.pool.connection.return_value.__enter__ = MagicMock(
        return_value=mock_conn
    )
    mcp_server_module.pool.connection.return_value.__exit__ = MagicMock(
        return_value=False
    )
    mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    return mock_cur
