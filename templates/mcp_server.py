"""CocoIndex MCP server for semantic code search.

Provides a search_code tool for AI agents to query indexed source code.
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


@mcp.tool(
    description="Semantic search over project source code. "
    "Use this to understand how features work, find implementations, or explore architecture."
)
def search_code(query: str, top_k: int = 10) -> list[dict]:
    """Search source code semantically. Returns matching code chunks ranked by relevance."""
    query_vector = code_to_embedding.eval(query)
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT filename, code, embedding <=> %s::vector AS distance
                FROM {TABLE_NAME}
                ORDER BY distance
                LIMIT %s
                """,
                (query_vector, top_k),
            )
            return [
                {"filename": row[0], "code": row[1], "score": round(1.0 - row[2], 4)}
                for row in cur.fetchall()
            ]


if __name__ == "__main__":
    mcp.run(transport="stdio")
