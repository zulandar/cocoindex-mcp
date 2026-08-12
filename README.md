# CocoIndex MCP

Add semantic code search to any project via [CocoIndex](https://cocoindex.io) + [MCP](https://modelcontextprotocol.io). One command from your repo root gives Claude (or any MCP client) tools to understand your codebase.

## Prerequisites

- Docker
- Python 3.13+
- Git

## Install

From your project's root directory:

```bash
curl -sSL https://raw.githubusercontent.com/zulandar/cocoindex-mcp/refs/heads/main/install.sh | bash
```

The installer will:

1. Confirm the target directory
2. Check for Python 3.13+ (offers to install if missing)
3. Ask which port to use for the CocoIndex Postgres database
4. Auto-detect file types in your repo and let you confirm
5. Optionally install a post-commit hook for auto-indexing
6. Create a `cocoindex/` directory with all necessary files
7. Set up a Python venv and install dependencies
8. Start Postgres (pgvector) via Docker
9. Clear any index left by a previous install, then enable pgvector
10. Run the initial index
11. Auto-configure `.mcp.json` in your repo root

### Re-running the installer

Re-running the installer over an existing `cocoindex/` directory rebuilds the
index from scratch. It has to: that directory holds `cocoindex.db`, which is
CocoIndex's only record of what it has already created in Postgres, and
replacing the directory discards it. The installer therefore drops the
project's `<project>_cocoindex` schema before indexing, so the two always
agree. That schema holds nothing but derived embeddings.

Each project also gets its own Compose project (`<project>_cocoindex`) and
volume (`<project>_cocoindex_data`), so repos on one machine never share a
database. Installs predating that shared a single volume named
`cocoindex_cocoindex_data`; once every repo has been re-installed, it is safe to
reclaim with `docker volume rm cocoindex_cocoindex_data`.

## Usage

After install, the MCP server is automatically configured in `.mcp.json` at your repo root. Two tools are available:

- **`search_code`** — Semantic search over your indexed files. Returns ranked snippets by default; set `include_code=True` for full chunks. Supports `top_k` and `min_score` parameters.
- **`get_project_structure`** — Returns a tree view of all indexed source files.

### Manual re-index

```bash
cd cocoindex && .venv/bin/cocoindex update main.py
```

### Edit indexed patterns

Edit `cocoindex/cocoindex.yaml` to add or remove file extensions and exclude patterns, then re-index.

## What gets created

```
your-project/
  .mcp.json               # MCP server config (auto-created/updated)
  cocoindex/
    .venv/                # Python virtual environment
    .env                  # Database connection string
    .gitignore            # Ignores .venv and __pycache__
    cocoindex.yaml        # Include/exclude patterns and config
    docker-compose.yml    # pgvector Postgres service
    main.py               # CocoIndex flow definition
    mcp_server.py         # MCP server with search_code + get_project_structure
    requirements.txt      # Python dependencies
```
