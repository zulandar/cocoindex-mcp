"""CocoIndex MCP server for semantic code search.

Provides search_code and get_project_structure tools for AI agents.
"""

import os

import yaml
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from psycopg_pool import ConnectionPool

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

import cocoindex
from main import code_embedding_flow, code_to_embedding


def load_config():
    config_path = os.path.join(os.path.dirname(__file__), "cocoindex.yaml")
    with open(config_path) as f:
        return yaml.safe_load(f)


CONFIG = load_config()

cocoindex.init()

TABLE_NAME = cocoindex.utils.get_target_storage_default_name(
    code_embedding_flow, "code_embeddings"
)

pool = ConnectionPool(os.environ["COCOINDEX_DATABASE_URL"])

mcp = FastMCP(f"{CONFIG['project']}_cocoindex")


def _format_location(location) -> str:
    """Format a CocoIndex Range into a readable string like 'L10-L25'."""
    if location is None:
        return ""
    loc = str(location)
    # Already readable
    if loc.startswith("L") or loc.startswith("l"):
        return loc
    # Try to parse (start, end) offset pairs
    try:
        parts = loc.strip("()[] ").split(",")
        if len(parts) == 2:
            return f"offset {parts[0].strip()}-{parts[1].strip()}"
    except (ValueError, AttributeError):
        pass
    return loc


@mcp.tool(
    description="Semantic search over project source code. "
    "Use this to understand how features work, find implementations, or explore architecture. "
    "Returns snippets by default — set include_code=True for full chunks."
)
def search_code(
    query: str,
    top_k: int = 10,
    include_code: bool = False,
    min_score: float = 0.3,
) -> list[dict]:
    """Search source code semantically. Returns matching code chunks ranked by relevance."""
    query_vector = code_to_embedding.eval(query)
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT filename, location, code, embedding <=> %s::vector AS distance
                FROM {TABLE_NAME}
                ORDER BY distance
                LIMIT %s
                """,
                (query_vector, top_k),
            )
            results = []
            for row in cur.fetchall():
                score = round(1.0 - row[3], 4)
                if score < min_score:
                    continue
                entry = {
                    "filename": row[0],
                    "location": _format_location(row[1]),
                    "snippet": row[2][:200],
                    "score": score,
                }
                if include_code:
                    entry["code"] = row[2]
                results.append(entry)
            return results


def _build_tree(filenames: list[str]) -> dict:
    """Build a nested dict from a list of file paths."""
    tree: dict = {}
    for path in filenames:
        parts = path.strip("/").split("/")
        node = tree
        for part in parts:
            node = node.setdefault(part, {})
    return tree


def _render_tree(tree: dict, prefix: str = "") -> list[str]:
    """Render a nested dict as a tree with box-drawing characters."""
    lines = []
    entries = sorted(tree.keys())
    for i, name in enumerate(entries):
        is_last = i == len(entries) - 1
        connector = "\u2514\u2500\u2500 " if is_last else "\u251c\u2500\u2500 "
        lines.append(f"{prefix}{connector}{name}")
        if tree[name]:
            extension = "    " if is_last else "\u2502   "
            lines.extend(_render_tree(tree[name], prefix + extension))
    return lines


@mcp.tool(
    description="Get the file structure of the indexed project. "
    "Use this to understand project layout before searching for specific code."
)
def get_project_structure() -> str:
    """Return a tree-formatted view of all indexed source files."""
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT DISTINCT filename FROM {TABLE_NAME} ORDER BY filename"
            )
            filenames = [row[0] for row in cur.fetchall()]
    if not filenames:
        return "(no files indexed)"
    tree = _build_tree(filenames)
    return "\n".join(_render_tree(tree))


if __name__ == "__main__":
    mcp.run(transport="stdio")
