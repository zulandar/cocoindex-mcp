# CocoIndex MCP v1.0.2 Migration & Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade `cocoindex` from unpinned 0.1.x to pinned `==1.0.2` (a complete API rewrite), pin all other dependencies, fix the issues identified in the repo review, land it on the `fix/install-cleanup-and-pin-deps` branch, open a PR.

**Architecture:** Replace the v0.1 flow API in `templates/main.py` with v1.0's `coco.App` + async / `asyncpg` model from the upstream `code_embedding` example at https://github.com/cocoindex-io/cocoindex/blob/v1.0.2/examples/code_embedding/main.py. Refactor `templates/mcp_server.py` to async tools backed by `asyncpg` with pgvector type registration. Adopt v1.0's explicit `start_line` / `end_line` schema (which deletes the `_format_location` parsing helper). Update `install.sh` to (a) generate v1.0-compatible glob patterns (`**/*.py`), (b) drop the now-redundant `cocoindex setup -f` (v1.0's `mount_table_target` self-creates schema), (c) repair the dead-branch / misleading-message bugs in the `.mcp.json` configurator, and (d) prefer `printf` over `echo` for arbitrary template content. Pin `cocoindex[postgres,sentence_transformers]==1.0.2`, others with `~=`.

**Tech Stack:** Python 3.13+, cocoindex 1.0.2 (async), asyncpg, pgvector Postgres (Docker), MCP (FastMCP), pytest + pytest-asyncio, bash 4+, shellcheck.

**Risk note:** v1.0.2 is classified Alpha on PyPI and the upstream example pins `>=1.0.0a30`. Most failure modes will surface during the end-to-end smoke test (Task 12), not unit tests. Expect to iterate on Tasks 3–4 based on what the smoke test exposes.

---

## File Structure

**Modify (templates/):**
- `templates/main.py` — full rewrite for v1.0 API (`coco.App` + `coco.fn` + `coco.lifespan`)
- `templates/mcp_server.py` — async refactor, asyncpg, pgvector type registration, new schema columns
- `templates/.env` — rename `COCOINDEX_DATABASE_URL` → `POSTGRES_URL` (matches upstream)
- `templates/requirements.txt` — pin `cocoindex[postgres,sentence_transformers]==1.0.2` + others
- `templates/docker-compose.yml` — no change

**Modify (project root):**
- `install.sh` — pattern format `**/*.{ext}`, drop `cocoindex setup`, env-var rename, MCP_STATUS fix, `printf` over `echo`, drop `cocoindex.yaml` from `TEMPLATES` array
- `README.md` — `main` → `main.py`
- `pyproject.toml` — add `[project.optional-dependencies]` test extras, configure pytest-asyncio
- `.gitignore` — add `__pycache__/`, `.venv/`, `.pytest_cache/`, `*.pyc`
- `tests/conftest.py` — new mock list (asyncpg, cocoindex.ops.*, no psycopg_pool), POSTGRES_URL env, `AsyncMock` for cursor
- `tests/test_helpers.py` — drop `TestFormatLocation` (function removed), keep `TestBuildTree`/`TestRenderTree`
- `tests/test_tools.py` — async tests, asyncpg-style row dicts, schema columns

**Delete:**
- `templates/cocoindex.yaml` — dead placeholder file (install.sh generates the user-facing yaml inline)

---

## Tasks

### Task 1: Verify cocoindex v1.0.2 CLI and import surface in scratch venv

Independent sanity check before committing to the rewrite. Validates: install succeeds, `cocoindex update <flow_file>` exists, the imports we plan to use in `main.py` are real. If anything here surprises us, stop and re-plan.

**Files:** none (scratch venv at `/tmp/cocoindex-verify-venv`)

- [ ] **Step 1: Create scratch venv and install pinned cocoindex**

```bash
python3.13 -m venv /tmp/cocoindex-verify-venv
/tmp/cocoindex-verify-venv/bin/pip install --upgrade pip
/tmp/cocoindex-verify-venv/bin/pip install "cocoindex[postgres,sentence_transformers]==1.0.2" "asyncpg~=0.31" "pgvector~=0.4" "numpy~=2.4"
```

Expected: clean install, no errors. If wheel resolution fails (e.g., no prebuilt wheel for the platform), STOP and report.

- [ ] **Step 2: Inspect CLI**

```bash
/tmp/cocoindex-verify-venv/bin/cocoindex --help
/tmp/cocoindex-verify-venv/bin/cocoindex update --help 2>&1 | head -40
```

Expected: `cocoindex update <flow_file>` accepts a python file path. Note in `/tmp/cocoindex-v102-cli-notes.txt` whether a `setup` subcommand still exists (we plan to drop it).

- [ ] **Step 3: Verify the imports we will use in main.py**

```bash
/tmp/cocoindex-verify-venv/bin/python - <<'PY'
import cocoindex as coco
from cocoindex.connectors import localfs, postgres
from cocoindex.ops.text import RecursiveSplitter, detect_code_language
from cocoindex.ops.sentence_transformers import SentenceTransformerEmbedder
from cocoindex.resources.chunk import Chunk
from cocoindex.resources.file import FileLike, PatternFilePathMatcher
from cocoindex.resources.id import IdGenerator
print("all imports ok")
print("App:", hasattr(coco, "App"))
print("AppConfig:", hasattr(coco, "AppConfig"))
print("ContextKey:", hasattr(coco, "ContextKey"))
print("EnvironmentBuilder:", hasattr(coco, "EnvironmentBuilder"))
print("fn:", hasattr(coco, "fn"))
print("lifespan:", hasattr(coco, "lifespan"))
print("map:", hasattr(coco, "map"))
print("mount_each:", hasattr(coco, "mount_each"))
print("runtime:", hasattr(coco, "runtime"))
print("show_progress:", hasattr(coco, "show_progress"))
print("use_context:", hasattr(coco, "use_context"))
PY
```

Expected: every line prints `True` after `all imports ok`. If any returns `False` or any import errors, STOP — the upstream example uses an API surface that doesn't match the published wheel, and Tasks 3–4 need replanning before proceeding.

- [ ] **Step 4: Verify pgvector + asyncpg type registration helper exists**

```bash
/tmp/cocoindex-verify-venv/bin/python - <<'PY'
from pgvector.asyncpg import register_vector
print("register_vector:", register_vector)
PY
```

Expected: prints a function reference. If ImportError, the asyncpg path in `mcp_server.py` needs a different vector adapter; STOP and replan.

- [ ] **Step 5: Tear down scratch venv**

```bash
rm -rf /tmp/cocoindex-verify-venv
```

(no commit — pure verification)

---

### Task 2: TDD scaffold — write failing tests for new module shape

Update tests BEFORE rewriting `mcp_server.py`. Existing `_format_location` tests are removed (the function is gone in the new schema where `start_line`/`end_line` are explicit columns). `_build_tree` and `_render_tree` tests are unchanged. `search_code` and `get_project_structure` become async + use new column shape.

**Files:**
- Modify: `pyproject.toml`
- Modify: `tests/conftest.py`
- Modify: `tests/test_helpers.py`
- Modify: `tests/test_tools.py`

- [ ] **Step 1: Update pyproject.toml**

Replace contents of `pyproject.toml` with:

```toml
[project]
name = "cocoindex-mcp"
requires-python = ">=3.13"

[project.optional-dependencies]
test = ["pytest", "pytest-asyncio~=1.3", "pyyaml~=6.0"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
```

- [ ] **Step 2: Rewrite tests/conftest.py**

Replace entire contents with:

```python
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
```

- [ ] **Step 3: Rewrite tests/test_helpers.py**

Replace entire contents with (drops `TestFormatLocation`):

```python
"""Tests for pure helper functions in mcp_server.py."""


class TestBuildTree:
    def test_empty_list(self, mcp_server_module):
        assert mcp_server_module._build_tree([]) == {}

    def test_flat_files(self, mcp_server_module):
        tree = mcp_server_module._build_tree(["a.py", "b.py"])
        assert tree == {"a.py": {}, "b.py": {}}

    def test_nested_paths(self, mcp_server_module):
        tree = mcp_server_module._build_tree(["src/main.py", "src/utils/helpers.py"])
        assert tree == {
            "src": {
                "main.py": {},
                "utils": {
                    "helpers.py": {},
                },
            },
        }

    def test_duplicated_prefixes(self, mcp_server_module):
        tree = mcp_server_module._build_tree(["src/a.py", "src/b.py"])
        assert tree == {"src": {"a.py": {}, "b.py": {}}}


class TestRenderTree:
    def test_empty_tree(self, mcp_server_module):
        assert mcp_server_module._render_tree({}) == []

    def test_single_file(self, mcp_server_module):
        lines = mcp_server_module._render_tree({"file.py": {}})
        assert lines == ["└── file.py"]

    def test_two_files_sorted(self, mcp_server_module):
        lines = mcp_server_module._render_tree({"b.py": {}, "a.py": {}})
        assert lines == ["├── a.py", "└── b.py"]

    def test_nested_directory(self, mcp_server_module):
        tree = {"src": {"main.py": {}}}
        lines = mcp_server_module._render_tree(tree)
        assert lines == ["└── src", "    └── main.py"]

    def test_box_drawing_with_siblings(self, mcp_server_module):
        tree = {"src": {"a.py": {}, "b.py": {}}, "README.md": {}}
        lines = mcp_server_module._render_tree(tree)
        assert lines == [
            "├── README.md",
            "└── src",
            "    ├── a.py",
            "    └── b.py",
        ]
```

- [ ] **Step 4: Rewrite tests/test_tools.py**

Replace entire contents with:

```python
"""Tests for MCP tool functions (search_code, get_project_structure)."""


class TestSearchCode:
    async def test_returns_snippets_by_default(self, mcp_server_module, mock_pool_and_embedder):
        mock_conn, _ = mock_pool_and_embedder
        mock_conn.fetch.return_value = [
            {"filename": "src/main.py", "code": "def main():\n    pass\n",
             "distance": 0.1, "start_line": 1, "end_line": 10},
        ]

        results = await mcp_server_module.search_code("main function")

        assert len(results) == 1
        assert results[0]["filename"] == "src/main.py"
        assert results[0]["snippet"] == "def main():\n    pass\n"
        assert results[0]["score"] == 0.9
        assert results[0]["location"] == "L1-L10"
        assert "code" not in results[0]

    async def test_include_code_flag(self, mcp_server_module, mock_pool_and_embedder):
        mock_conn, _ = mock_pool_and_embedder
        mock_conn.fetch.return_value = [
            {"filename": "src/main.py", "code": "def main():\n    pass\n",
             "distance": 0.1, "start_line": 1, "end_line": 10},
        ]

        results = await mcp_server_module.search_code("main", include_code=True)

        assert len(results) == 1
        assert results[0]["code"] == "def main():\n    pass\n"

    async def test_min_score_filters(self, mcp_server_module, mock_pool_and_embedder):
        mock_conn, _ = mock_pool_and_embedder
        mock_conn.fetch.return_value = [
            {"filename": "a.py", "code": "low relevance",
             "distance": 0.85, "start_line": 1, "end_line": 5},
            {"filename": "b.py", "code": "high relevance",
             "distance": 0.05, "start_line": 1, "end_line": 5},
        ]

        results = await mcp_server_module.search_code("query", min_score=0.3)

        assert len(results) == 1
        assert results[0]["filename"] == "b.py"

    async def test_empty_results(self, mcp_server_module, mock_pool_and_embedder):
        mock_conn, _ = mock_pool_and_embedder
        mock_conn.fetch.return_value = []

        results = await mcp_server_module.search_code("nonexistent")

        assert results == []

    async def test_snippet_truncated_to_200(self, mcp_server_module, mock_pool_and_embedder):
        mock_conn, _ = mock_pool_and_embedder
        long_code = "x" * 500
        mock_conn.fetch.return_value = [
            {"filename": "a.py", "code": long_code,
             "distance": 0.05, "start_line": 1, "end_line": 100},
        ]

        results = await mcp_server_module.search_code("query")

        assert len(results[0]["snippet"]) == 200


class TestGetProjectStructure:
    async def test_returns_tree_string(self, mcp_server_module, mock_pool_and_embedder):
        mock_conn, _ = mock_pool_and_embedder
        mock_conn.fetch.return_value = [
            {"filename": "src/main.py"},
            {"filename": "src/utils.py"},
        ]

        result = await mcp_server_module.get_project_structure()

        assert "src" in result
        assert "main.py" in result
        assert "utils.py" in result

    async def test_empty_table(self, mcp_server_module, mock_pool_and_embedder):
        mock_conn, _ = mock_pool_and_embedder
        mock_conn.fetch.return_value = []

        result = await mcp_server_module.get_project_structure()

        assert result == "(no files indexed)"
```

- [ ] **Step 5: Install test deps and run — expect failures**

```bash
cd /home/ctrower/projects/cocoindex-mcp
pip install -e ".[test]"
pytest -v
```

Expected: `test_helpers.py` tests pass against the OLD `mcp_server.py` (the helpers haven't been touched yet). `test_tools.py` tests will fail with `AttributeError: ... has no attribute 'search_code'` once invoked through the async harness, OR pass for the old sync `search_code` but with shape mismatches. Either way: at least one failure expected. Proceeding without rewrite is wrong.

- [ ] **Step 6: Commit failing tests**

```bash
git add tests/ pyproject.toml
git commit -m "test: scaffold tests for v1.0 async API and new schema"
```

---

### Task 3: Rewrite templates/main.py for cocoindex v1.0.2

**Files:** Modify `templates/main.py` (full rewrite)

- [ ] **Step 1: Replace file contents**

Write `templates/main.py` with:

```python
"""CocoIndex flow for code embedding (v1.0.2)."""
from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass
from typing import Annotated, AsyncIterator

import asyncpg
import yaml
from numpy.typing import NDArray

import cocoindex as coco
from cocoindex.connectors import localfs, postgres
from cocoindex.ops.sentence_transformers import SentenceTransformerEmbedder
from cocoindex.ops.text import RecursiveSplitter, detect_code_language
from cocoindex.resources.chunk import Chunk
from cocoindex.resources.file import FileLike, PatternFilePathMatcher
from cocoindex.resources.id import IdGenerator


def load_config():
    config_path = os.path.join(os.path.dirname(__file__), "cocoindex.yaml")
    with open(config_path) as f:
        return yaml.safe_load(f)


CONFIG = load_config()
DATABASE_URL = os.environ["POSTGRES_URL"]
TABLE_NAME = "code_embeddings"
PG_SCHEMA_NAME = f"{CONFIG['project']}_cocoindex"
EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

PG_DB = coco.ContextKey[asyncpg.Pool]("code_embedding_db")
EMBEDDER = coco.ContextKey[SentenceTransformerEmbedder]("embedder", detect_change=True)

_splitter = RecursiveSplitter()


@dataclass
class CodeEmbedding:
    id: int
    filename: str
    code: str
    embedding: Annotated[NDArray, EMBEDDER]
    start_line: int
    end_line: int


@coco.lifespan
async def coco_lifespan(builder: coco.EnvironmentBuilder) -> AsyncIterator[None]:
    async with await asyncpg.create_pool(DATABASE_URL) as pool:
        builder.provide(PG_DB, pool)
        builder.provide(EMBEDDER, SentenceTransformerEmbedder(EMBED_MODEL))
        yield


@coco.fn
async def process_chunk(
    chunk: Chunk,
    filename: pathlib.PurePath,
    id_gen: IdGenerator,
    table: postgres.TableTarget[CodeEmbedding],
) -> None:
    embedding = await coco.use_context(EMBEDDER).embed(chunk.text)
    table.declare_row(
        row=CodeEmbedding(
            id=await id_gen.next_id(chunk.text),
            filename=str(filename),
            code=chunk.text,
            embedding=embedding,
            start_line=chunk.start.line,
            end_line=chunk.end.line,
        ),
    )


@coco.fn(memo=True)
async def process_file(
    file: FileLike,
    table: postgres.TableTarget[CodeEmbedding],
) -> None:
    text = await file.read_text()
    language = detect_code_language(filename=str(file.file_path.path.name))
    chunks = _splitter.split(
        text,
        chunk_size=1000,
        min_chunk_size=300,
        chunk_overlap=300,
        language=language,
    )
    id_gen = IdGenerator()
    await coco.map(process_chunk, chunks, file.file_path.path, id_gen, table)


@coco.fn
async def app_main(sourcedir: pathlib.Path) -> None:
    target_table = await postgres.mount_table_target(
        PG_DB,
        table_name=TABLE_NAME,
        table_schema=await postgres.TableSchema.from_class(
            CodeEmbedding,
            primary_key=["id"],
        ),
        pg_schema_name=PG_SCHEMA_NAME,
    )
    target_table.declare_vector_index(column="embedding")

    files = localfs.walk_dir(
        sourcedir,
        recursive=True,
        path_matcher=PatternFilePathMatcher(
            included_patterns=CONFIG["patterns"]["included"],
            excluded_patterns=CONFIG["patterns"]["excluded"],
        ),
    )
    await coco.mount_each(process_file, files.items(), target_table)


app = coco.App(
    coco.AppConfig(name=f"{CONFIG['project']}_cocoindex"),
    app_main,
    sourcedir=pathlib.Path(__file__).parent.parent,
)
```

- [ ] **Step 2: Commit**

```bash
git add templates/main.py
git commit -m "feat: rewrite templates/main.py for cocoindex v1.0.2 API"
```

---

### Task 4: Rewrite templates/mcp_server.py (async, asyncpg, pgvector)

**Files:** Modify `templates/mcp_server.py`

- [ ] **Step 1: Replace file contents**

Write `templates/mcp_server.py` with:

```python
"""CocoIndex MCP server for semantic code search (v1.0.2)."""
import os
import sys

import asyncpg
import yaml
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from pgvector.asyncpg import register_vector

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

sys.path.insert(0, os.path.dirname(__file__))

from cocoindex.ops.sentence_transformers import SentenceTransformerEmbedder
from main import EMBED_MODEL, PG_SCHEMA_NAME, TABLE_NAME


def load_config():
    config_path = os.path.join(os.path.dirname(__file__), "cocoindex.yaml")
    with open(config_path) as f:
        return yaml.safe_load(f)


CONFIG = load_config()

if "POSTGRES_URL" not in os.environ:
    raise SystemExit(
        "POSTGRES_URL is not set. Make sure cocoindex/.env exists and is loaded."
    )

DATABASE_URL = os.environ["POSTGRES_URL"]

mcp = FastMCP(f"{CONFIG['project']}_cocoindex")

_pool: asyncpg.Pool | None = None
_embedder: SentenceTransformerEmbedder | None = None


async def _init_conn(conn):
    await register_vector(conn)


async def _get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DATABASE_URL, init=_init_conn)
    return _pool


def _get_embedder() -> SentenceTransformerEmbedder:
    global _embedder
    if _embedder is None:
        _embedder = SentenceTransformerEmbedder(EMBED_MODEL)
    return _embedder


@mcp.tool(
    description="Semantic search over project source code. "
    "Use this to understand how features work, find implementations, or explore architecture. "
    "Returns snippets by default — set include_code=True for full chunks."
)
async def search_code(
    query: str,
    top_k: int = 10,
    include_code: bool = False,
    min_score: float = 0.3,
) -> list[dict]:
    """Search source code semantically. Returns matching code chunks ranked by relevance."""
    embedder = _get_embedder()
    query_vec = await embedder.embed(query)
    pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT filename, code, embedding <=> $1 AS distance, start_line, end_line
            FROM "{PG_SCHEMA_NAME}"."{TABLE_NAME}"
            ORDER BY distance ASC
            LIMIT $2
            """,
            query_vec,
            top_k,
        )
    results = []
    for r in rows:
        score = round(1.0 - float(r["distance"]), 4)
        if score < min_score:
            continue
        entry = {
            "filename": r["filename"],
            "location": f"L{r['start_line']}-L{r['end_line']}",
            "snippet": r["code"][:200],
            "score": score,
        }
        if include_code:
            entry["code"] = r["code"]
        results.append(entry)
    return results


def _build_tree(filenames: list[str]) -> dict:
    tree: dict = {}
    for path in filenames:
        parts = path.strip("/").split("/")
        node = tree
        for part in parts:
            node = node.setdefault(part, {})
    return tree


def _render_tree(tree: dict, prefix: str = "") -> list[str]:
    lines = []
    entries = sorted(tree.keys())
    for i, name in enumerate(entries):
        is_last = i == len(entries) - 1
        connector = "└── " if is_last else "├── "
        lines.append(f"{prefix}{connector}{name}")
        if tree[name]:
            extension = "    " if is_last else "│   "
            lines.extend(_render_tree(tree[name], prefix + extension))
    return lines


@mcp.tool(
    description="Get the file structure of the indexed project. "
    "Use this to understand project layout before searching for specific code."
)
async def get_project_structure() -> str:
    """Return a tree-formatted view of all indexed source files."""
    pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f'SELECT DISTINCT filename FROM "{PG_SCHEMA_NAME}"."{TABLE_NAME}" ORDER BY filename'
        )
    filenames = [r["filename"] for r in rows]
    if not filenames:
        return "(no files indexed)"
    tree = _build_tree(filenames)
    return "\n".join(_render_tree(tree))


if __name__ == "__main__":
    mcp.run(transport="stdio")
```

- [ ] **Step 2: Run unit tests — they should now pass**

```bash
pytest -v
```

Expected: all tests in `test_tools.py` and `test_helpers.py` pass.

If any test fails, FIX the test or implementation before commit. Common pitfalls:
- `AsyncMock` returning the wrong shape → adjust fixture
- `register_vector` import failure → was caught in Task 1 but if it shows up here, mock the module in `MOCKED_MODULES` (already there) — re-run

- [ ] **Step 3: Commit**

```bash
git add templates/mcp_server.py
git commit -m "feat: refactor mcp_server.py async with asyncpg + pgvector type registration"
```

---

### Task 5: Pin templates/requirements.txt

**Files:** Modify `templates/requirements.txt`

- [ ] **Step 1: Replace contents**

Write `templates/requirements.txt` with:

```
cocoindex[postgres,sentence_transformers]==1.0.2
asyncpg~=0.31
pgvector~=0.4
numpy~=2.4
mcp~=1.27
python-dotenv~=1.2
pyyaml~=6.0
```

- [ ] **Step 2: Commit**

```bash
git add templates/requirements.txt
git commit -m "feat: pin templates/requirements.txt for reproducible installs"
```

---

### Task 6: Rename env var in templates/.env

**Files:** Modify `templates/.env`

- [ ] **Step 1: Replace contents**

Write `templates/.env` with:

```
POSTGRES_URL=postgresql://cocoindex:cocoindex@localhost:{{PORT}}/cocoindex
```

- [ ] **Step 2: Commit**

```bash
git add templates/.env
git commit -m "feat: rename COCOINDEX_DATABASE_URL -> POSTGRES_URL (matches upstream)"
```

---

### Task 7: Delete dead templates/cocoindex.yaml placeholder

**Files:** Delete `templates/cocoindex.yaml`

- [ ] **Step 1: Delete**

```bash
git rm templates/cocoindex.yaml
```

- [ ] **Step 2: Commit**

```bash
git commit -m "chore: remove dead templates/cocoindex.yaml (install.sh generates inline)"
```

---

### Task 8: install.sh — pattern format `**/*.{ext}` and remove cocoindex.yaml from TEMPLATES

**Files:** Modify `install.sh`

- [ ] **Step 1: Drop `cocoindex.yaml` from TEMPLATES array**

Find at install.sh:9:

```bash
TEMPLATES=("docker-compose.yml" "main.py" "mcp_server.py" "requirements.txt" ".gitignore" ".env" "cocoindex.yaml")
```

Replace with:

```bash
TEMPLATES=("docker-compose.yml" "main.py" "mcp_server.py" "requirements.txt" ".gitignore" ".env")
```

- [ ] **Step 2: Move yaml generation out of the template loop and emit `**/`-prefixed patterns**

Find the in-loop block (install.sh:255–269):

```bash
    # Generate cocoindex.yaml directly instead of sed substitution
    if [ "$tmpl" = "cocoindex.yaml" ]; then
        {
            echo "project: $PROJECT_NAME"
            echo "port: $PORT"
            echo "patterns:"
            echo "  included:"
            for pat in "${INCLUDED[@]}"; do
                echo "    - \"$pat\""
            done
            echo "  excluded:"
            for pat in "${DEFAULT_EXCLUDES[@]}"; do
                echo "    - \"$pat\""
            done
        } > "cocoindex/$tmpl"
        continue
    fi
```

Delete that block entirely (the loop no longer iterates over `cocoindex.yaml`).

After the closing `done` of the templates loop (just before `info "Files created."` at line 281), add:

```bash
# Generate cocoindex.yaml with v1.0-compatible glob patterns
{
    echo "project: $PROJECT_NAME"
    echo "port: $PORT"
    echo "patterns:"
    echo "  included:"
    for pat in "${INCLUDED[@]}"; do
        echo "    - \"**/$pat\""
    done
    echo "  excluded:"
    for pat in "${DEFAULT_EXCLUDES[@]}"; do
        echo "    - \"**/$pat\""
    done
} > "cocoindex/cocoindex.yaml"
```

- [ ] **Step 3: Run shellcheck**

```bash
shellcheck install.sh
```

Expected: no warnings. If a warning appears, fix it (do not silence with directives).

- [ ] **Step 4: Commit**

```bash
git add install.sh
git commit -m "feat(install): emit '**/' glob patterns and move yaml gen out of template loop"
```

---

### Task 9: install.sh — drop `cocoindex setup`, fix MCP_STATUS, swap echo→printf

**Files:** Modify `install.sh`

- [ ] **Step 1: Drop the `cocoindex setup main.py -f` line**

Find at install.sh:341–344:

```bash
cd cocoindex
.venv/bin/cocoindex setup main.py -f
.venv/bin/cocoindex update main.py
cd ..
```

Replace with (v1.0 `mount_table_target` self-creates schema; no separate setup):

```bash
cd cocoindex
.venv/bin/cocoindex update main.py
cd ..
```

- [ ] **Step 2: Repair MCP_RESULT logic**

Find at install.sh:357–403 (the Python heredoc and surrounding bash):

```bash
$PYTHON_CMD - "$MCP_JSON" "$SERVER_NAME" "$COCOINDEX_DIR" << 'PYEOF'
import json
import sys

mcp_json_path = sys.argv[1]
server_name = sys.argv[2]
cocoindex_dir = sys.argv[3]

server_config = {
    "command": f"{cocoindex_dir}/.venv/bin/python",
    "args": [f"{cocoindex_dir}/mcp_server.py"]
}

try:
    with open(mcp_json_path) as f:
        config = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    config = {}

if "mcpServers" not in config:
    config["mcpServers"] = {}

if server_name in config["mcpServers"]:
    print("already_configured")
else:
    config["mcpServers"][server_name] = server_config
    with open(mcp_json_path, "w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")
    print("configured")
PYEOF

MCP_RESULT=$?
if [ $MCP_RESULT -eq 0 ]; then
    info "MCP server '${SERVER_NAME}' added to ${MCP_JSON}"
else
    warn "Could not update .mcp.json automatically. Add this manually:"
    echo ""
    echo "  {"
    echo "    \"mcpServers\": {"
    echo "      \"${SERVER_NAME}\": {"
    echo "        \"command\": \"${COCOINDEX_DIR}/.venv/bin/python\","
    echo "        \"args\": [\"${COCOINDEX_DIR}/mcp_server.py\"]"
    echo "      }"
    echo "    }"
    echo "  }"
fi
```

Replace with (capture stdout, use it to drive accurate messages; the unreachable `$?` branch is gone since `set -e` already aborts on Python failure):

```bash
MCP_STATUS=$($PYTHON_CMD - "$MCP_JSON" "$SERVER_NAME" "$COCOINDEX_DIR" << 'PYEOF'
import json
import sys

mcp_json_path = sys.argv[1]
server_name = sys.argv[2]
cocoindex_dir = sys.argv[3]

server_config = {
    "command": f"{cocoindex_dir}/.venv/bin/python",
    "args": [f"{cocoindex_dir}/mcp_server.py"],
}

try:
    with open(mcp_json_path) as f:
        config = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    config = {}

if "mcpServers" not in config:
    config["mcpServers"] = {}

if server_name in config["mcpServers"]:
    print("already_configured")
else:
    config["mcpServers"][server_name] = server_config
    with open(mcp_json_path, "w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")
    print("configured")
PYEOF
)

case "$MCP_STATUS" in
    configured)
        info "MCP server '${SERVER_NAME}' added to ${MCP_JSON}" ;;
    already_configured)
        info "MCP server '${SERVER_NAME}' already present in ${MCP_JSON} — no change" ;;
    *)
        warn "Unexpected MCP setup result: '${MCP_STATUS}'" ;;
esac
```

- [ ] **Step 3: Swap `echo "$content"` → `printf '%s\n' "$content"`**

Find at install.sh:278:

```bash
    echo "$content" > "cocoindex/$tmpl"
```

Replace with:

```bash
    printf '%s\n' "$content" > "cocoindex/$tmpl"
```

- [ ] **Step 4: Run shellcheck**

```bash
shellcheck install.sh
```

Expected: no warnings.

- [ ] **Step 5: Commit**

```bash
git add install.sh
git commit -m "fix(install): drop redundant cocoindex setup, repair MCP_STATUS detection, prefer printf"
```

---

### Task 10: Update README.md

**Files:** Modify `README.md`

- [ ] **Step 1: Fix the `main` → `main.py` reference**

Edit `README.md` line 42:

Find:
```
cd cocoindex && .venv/bin/cocoindex update main
```

Replace with:
```
cd cocoindex && .venv/bin/cocoindex update main.py
```

(No other content changes — feature descriptions still accurate. The `main.py` filename in line 60 is already correct.)

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: fix README cocoindex update command (main -> main.py)"
```

---

### Task 11: Update .gitignore

**Files:** Modify `.gitignore`

- [ ] **Step 1: Replace contents**

Write `.gitignore` with:

```
docs/plans/
__pycache__/
*.pyc
.pytest_cache/
.venv/
.mypy_cache/
*.egg-info/
```

- [ ] **Step 2: Commit**

```bash
git add .gitignore
git commit -m "chore: extend .gitignore with common Python paths"
```

---

### Task 12: End-to-end smoke test

Validates the full install flow against the rewritten templates. This is where most failures will surface — the unit tests can't catch real cocoindex API mismatches.

**Files:** none (scratch test in `/tmp/cocoindex-mcp-smoke`)

- [ ] **Step 1: Create scratch repo with a few file types**

```bash
rm -rf /tmp/cocoindex-mcp-smoke
mkdir -p /tmp/cocoindex-mcp-smoke && cd /tmp/cocoindex-mcp-smoke
git init -q
printf 'def hello():\n    return "hi"\n' > a.py
printf 'function hello() {\n    return "hi";\n}\n' > b.js
printf '# Project README\n' > README.md
git add . && git commit -q -m "init"
```

- [ ] **Step 2: Run install.sh from the local branch**

```bash
cd /tmp/cocoindex-mcp-smoke
bash /home/ctrower/projects/cocoindex-mcp/install.sh
```

When prompted: confirm directory; choose default port; accept detected patterns; **decline** the post-commit hook.

Expected output ends with `Setup Complete!` and no `[error]` lines.

Verify:

```bash
cat cocoindex/cocoindex.yaml
# expect: included entries like "**/*.py", "**/*.js", "**/*.md"
# expect: excluded entries like "**/.git", "**/node_modules", etc.

cat cocoindex/.env
# expect: POSTGRES_URL=postgresql://...

cat .mcp.json
# expect: mcpServers entry pointing at cocoindex/.venv/bin/python
```

- [ ] **Step 3: Verify Postgres came up and the index populated**

```bash
docker compose -f cocoindex/docker-compose.yml ps
# expect: 1 container, healthy

docker compose -f cocoindex/docker-compose.yml exec -T cocoindex-postgres \
  psql -U cocoindex -d cocoindex -c \
  'SELECT COUNT(*) FROM "cocoindex_mcp_smoke_cocoindex"."code_embeddings";'
# expect: count >= 1
```

- [ ] **Step 4: Invoke the MCP tool functions directly**

```bash
cocoindex/.venv/bin/python - <<'PY'
import asyncio
import sys
sys.path.insert(0, "cocoindex")
from mcp_server import search_code, get_project_structure

async def main():
    print("--- search_code('hello function') ---")
    for hit in await search_code("hello function", top_k=5, min_score=0.0):
        print(hit)
    print("--- get_project_structure() ---")
    print(await get_project_structure())

asyncio.run(main())
PY
```

Expected:
- `search_code` returns at least one hit referencing `a.py` or `b.js` with `location` like `L1-L3`.
- `get_project_structure` returns a tree containing `a.py`, `b.js`, and `README.md`.

- [ ] **Step 5: Tear down**

```bash
cd /tmp/cocoindex-mcp-smoke
docker compose -f cocoindex/docker-compose.yml down -v
cd /
rm -rf /tmp/cocoindex-mcp-smoke
```

If anything in steps 2–4 failed: STOP, diagnose, return to Task 3 or 4 to fix the API usage, re-run smoke test. Do not proceed until smoke test passes end-to-end. Likely failure modes and where to look:
- `cocoindex update` errors → `templates/main.py` API usage; re-check upstream example
- `PatternFilePathMatcher` rejects patterns → adjust `**/` formatting in install.sh Task 8
- `asyncpg` errors decoding `vector` → `register_vector` setup in `_get_pool` (Task 4)
- Schema/table not found → `mount_table_target` arguments; PG_SCHEMA_NAME mismatch
- `cocoindex setup` regression: if removing `setup` broke something, restore the call in install.sh

(no commit — validation step)

---

### Task 13: Final verification — full unit tests + shellcheck

**Files:** none

- [ ] **Step 1: Run unit tests**

```bash
cd /home/ctrower/projects/cocoindex-mcp
pytest -v
```

Expected: all tests pass.

- [ ] **Step 2: Run shellcheck**

```bash
shellcheck install.sh
```

Expected: no warnings.

- [ ] **Step 3: If anything was fixed in step 1 or 2, stage and commit**

```bash
git status
git add -p
git commit -m "fix: resolve verification issues from final pass"
```

(skip if nothing changed)

- [ ] **Step 4: Confirm commit graph is clean**

```bash
git log --oneline main..HEAD
```

Expected: ~10 commits, each with a clear message describing one logical change.

---

### Task 14: Push branch and open PR

**Files:** none

- [ ] **Step 1: Push branch**

```bash
git push -u origin fix/install-cleanup-and-pin-deps
```

- [ ] **Step 2: Open PR**

```bash
gh pr create --title "Migrate to cocoindex v1.0.2 + repo cleanup" --body "$(cat <<'EOF'
## Summary
- Rewrite `templates/main.py` and `templates/mcp_server.py` for cocoindex v1.0 API (async, asyncpg, pgvector type registration, new schema with explicit `start_line`/`end_line`).
- Pin `cocoindex[postgres,sentence_transformers]==1.0.2` and tilde-pin other deps for reproducible installs.
- Rename `COCOINDEX_DATABASE_URL` → `POSTGRES_URL` to match upstream conventions.
- Delete dead `templates/cocoindex.yaml` placeholder file (install.sh generates the user yaml inline).
- Fix `install.sh` MCP_STATUS detection: dead-branch removed, accurate "already configured" message.
- Drop `cocoindex setup -f` (v1.0 `mount_table_target` self-creates schema on first update).
- Update glob patterns to v1.0 format (`**/*.py`, `**/.git`, etc).
- README, .gitignore, pyproject.toml, tests updated; pytest-asyncio added.

## Test plan
- [x] Unit tests pass (`pytest -v`)
- [x] Shellcheck passes (`shellcheck install.sh`)
- [x] End-to-end: install.sh against scratch repo creates working MCP server; `search_code` returns ranked snippets; `get_project_structure` returns tree
EOF
)"
```

Expected: PR URL printed. Report URL back to user.

---
