"""CocoIndex MCP server for semantic code search (v1.0.2)."""
import asyncio
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
_pool_lock = asyncio.Lock()
_embedder: SentenceTransformerEmbedder | None = None


async def _init_conn(conn):
    await register_vector(conn)


async def _get_pool() -> asyncpg.Pool:
    global _pool
    async with _pool_lock:
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
