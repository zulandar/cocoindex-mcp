# CocoIndex MCP

Add semantic code search to any project via [CocoIndex](https://cocoindex.io) + [MCP](https://modelcontextprotocol.io). One command from your repo root gives Claude (or any MCP client) a `search_code` tool that understands your codebase.

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
9. Run the initial index
10. Print the MCP config snippet to add to Claude

## Usage

After install, add the printed config to your Claude MCP settings. The `search_code` tool will be available for semantic search over your indexed files.

### Manual re-index

```bash
cd cocoindex && .venv/bin/cocoindex update main
```

### Edit indexed patterns

Edit `cocoindex/cocoindex.yaml` to add or remove file extensions and exclude patterns, then re-index.

## What gets created

```
your-project/
  cocoindex/
    .venv/              # Python virtual environment
    .env                # Database connection string
    .gitignore          # Ignores .venv and __pycache__
    cocoindex.yaml      # Include/exclude patterns and config
    docker-compose.yml  # pgvector Postgres service
    main.py             # CocoIndex flow definition
    mcp_server.py       # MCP server with search_code tool
    requirements.txt    # Python dependencies
```
