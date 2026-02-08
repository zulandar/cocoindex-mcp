#!/usr/bin/env bash
set -euo pipefail

# When piped via curl | bash, stdin is the script itself.
# Reopen stdin from the terminal so interactive prompts work.
exec < /dev/tty

REPO_URL="https://raw.githubusercontent.com/zulandar/cocoindex-mcp/refs/heads/main"
TEMPLATES=("docker-compose.yml" "main.py" "mcp_server.py" "requirements.txt" ".gitignore" ".env" "cocoindex.yaml")

# Default exclude patterns — common dirs/files to skip
DEFAULT_EXCLUDES=(".git" "node_modules" ".venv" "venv" "vendor" "dist" "build" "__pycache__" "cocoindex" ".env" ".DS_Store")

# ─── Helpers ──────────────────────────────────────────────────────────────────

info()  { printf "\033[1;34m[info]\033[0m  %s\n" "$1"; }
warn()  { printf "\033[1;33m[warn]\033[0m  %s\n" "$1"; }
error() { printf "\033[1;31m[error]\033[0m %s\n" "$1"; exit 1; }
ask()   { printf "\033[1;36m[?]\033[0m %s " "$1"; }

confirm() {
    ask "$1 [Y/n]"
    read -r reply
    reply="${reply:-Y}"
    [[ "$reply" =~ ^[Yy]$ ]]
}

# ─── Step 1: Confirm directory ────────────────────────────────────────────────

PROJECT_DIR="$(pwd)"
PROJECT_NAME="$(basename "$PROJECT_DIR" | tr '[:upper:]' '[:lower:]' | tr ' -' '_' | tr -cd 'a-z0-9_')"

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║         CocoIndex MCP Installer              ║"
echo "╚══════════════════════════════════════════════╝"
echo ""

if ! confirm "Set up CocoIndex for '$PROJECT_DIR'?"; then
    echo "Aborted."
    exit 0
fi

if [ -d "cocoindex" ]; then
    warn "A cocoindex/ directory already exists here."
    if ! confirm "Overwrite it?"; then
        echo "Aborted."
        exit 0
    fi
    rm -rf cocoindex
fi

# ─── Step 2: Check Python >= 3.13 ────────────────────────────────────────────

info "Checking Python version..."

PYTHON_CMD=""
for cmd in python3.13 python3 python; do
    if command -v "$cmd" &>/dev/null; then
        version=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0.0")
        major="${version%%.*}"
        minor="${version#*.}"
        if [ "$major" -ge 3 ] && [ "$minor" -ge 13 ]; then
            PYTHON_CMD="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    warn "Python 3.13+ not found."

    # Detect OS and suggest install command
    INSTALL_CMD=""
    if [ -f /etc/os-release ]; then
        # shellcheck source=/dev/null
        . /etc/os-release
        case "$ID" in
            ubuntu|debian)
                INSTALL_CMD="sudo add-apt-repository ppa:deadsnakes/ppa -y && sudo apt update && sudo apt install -y python3.13 python3.13-venv python3.13-dev"
                ;;
            fedora)
                INSTALL_CMD="sudo dnf install -y python3.13"
                ;;
            arch|manjaro)
                INSTALL_CMD="sudo pacman -S python"
                ;;
            *)
                INSTALL_CMD=""
                ;;
        esac
    elif [[ "$(uname)" == "Darwin" ]]; then
        INSTALL_CMD="brew install python@3.13"
    fi

    if [ -n "$INSTALL_CMD" ]; then
        echo ""
        info "Detected install command:"
        echo "  $INSTALL_CMD"
        echo ""
        if confirm "Run this now? (requires sudo)"; then
            eval "$INSTALL_CMD"
            # Re-detect after install
            for cmd in python3.13 python3 python; do
                if command -v "$cmd" &>/dev/null; then
                    version=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0.0")
                    major="${version%%.*}"
                    minor="${version#*.}"
                    if [ "$major" -ge 3 ] && [ "$minor" -ge 13 ]; then
                        PYTHON_CMD="$cmd"
                        break
                    fi
                fi
            done
        fi
    fi

    if [ -z "$PYTHON_CMD" ]; then
        error "Python 3.13+ is required. Please install it and re-run."
    fi
fi

info "Using $PYTHON_CMD ($($PYTHON_CMD --version))"

# ─── Step 3: Ask Postgres port ────────────────────────────────────────────────

while true; do
    ask "Port for CocoIndex Postgres [5434]:"
    read -r PORT
    PORT="${PORT:-5434}"

    # Validate it's a number
    if ! [[ "$PORT" =~ ^[0-9]+$ ]]; then
        warn "Invalid port number."
        continue
    fi

    # Check if port is in use
    PORT_IN_USE=false
    if command -v ss &>/dev/null; then
        if ss -tlnp 2>/dev/null | grep -q ":${PORT} "; then
            PORT_IN_USE=true
        fi
    elif command -v lsof &>/dev/null; then
        if lsof -i :"$PORT" &>/dev/null; then
            PORT_IN_USE=true
        fi
    elif command -v netstat &>/dev/null; then
        if netstat -tlnp 2>/dev/null | grep -q ":${PORT} "; then
            PORT_IN_USE=true
        fi
    fi

    if $PORT_IN_USE; then
        warn "Port $PORT is already in use. Please choose another."
    else
        info "Port $PORT is available."
        break
    fi
done

# ─── Step 4: Auto-detect file patterns ───────────────────────────────────────

info "Scanning repository for file types..."

# Find all unique extensions in the repo (skip hidden dirs, node_modules, vendor, etc.)
DETECTED_EXTS=$(find "$PROJECT_DIR" \
    -not -path '*/.git/*' \
    -not -path '*/node_modules/*' \
    -not -path '*/.venv/*' \
    -not -path '*/venv/*' \
    -not -path '*/vendor/*' \
    -not -path '*/dist/*' \
    -not -path '*/build/*' \
    -not -path '*/__pycache__/*' \
    -not -path '*/cocoindex/*' \
    -type f -name '*.*' | \
    sed 's/.*\.//' | sort -u | tr '[:upper:]' '[:lower:]')

# Map extensions to glob patterns, filter to code/doc files
INCLUDED=()
for ext in $DETECTED_EXTS; do
    case "$ext" in
        py|js|ts|tsx|jsx|go|rs|rb|php|java|kt|swift|c|cpp|h|hpp|cs|scala|lua|r|pl|sh|bash|zsh)
            INCLUDED+=("*.$ext") ;;
        vue|svelte|html|htm|css|scss|sass|less)
            INCLUDED+=("*.$ext") ;;
        md|mdx|txt|rst|yaml|yml|toml|json|jsonl|xml|csv)
            INCLUDED+=("*.$ext") ;;
        sql|graphql|gql|proto)
            INCLUDED+=("*.$ext") ;;
    esac
done

if [ ${#INCLUDED[@]} -eq 0 ]; then
    warn "No recognized file types detected. Using defaults."
    INCLUDED=("*.py" "*.js" "*.ts" "*.md" "*.yaml" "*.json")
fi

echo ""
info "Detected file patterns to index:"
for pat in "${INCLUDED[@]}"; do
    echo "    + $pat"
done
echo ""
info "Default exclude patterns:"
for pat in "${DEFAULT_EXCLUDES[@]}"; do
    echo "    - $pat"
done
echo ""

if ! confirm "Use these patterns?"; then
    echo ""
    info "Enter included patterns (comma-separated, e.g. '*.py,*.js,*.md'):"
    ask "Included:"
    read -r custom_included
    IFS=',' read -ra INCLUDED <<< "$custom_included"
    # Trim whitespace
    for i in "${!INCLUDED[@]}"; do
        INCLUDED[i]="$(echo "${INCLUDED[i]}" | xargs)"
    done

    echo ""
    info "Enter excluded patterns (comma-separated, or press Enter for defaults):"
    ask "Excluded:"
    read -r custom_excluded
    if [ -n "$custom_excluded" ]; then
        IFS=',' read -ra DEFAULT_EXCLUDES <<< "$custom_excluded"
        for i in "${!DEFAULT_EXCLUDES[@]}"; do
            DEFAULT_EXCLUDES[i]="$(echo "${DEFAULT_EXCLUDES[i]}" | xargs)"
        done
    fi
fi

# ─── Step 5: Post-commit hook ────────────────────────────────────────────────

INSTALL_HOOK=false
if [ -d .git ]; then
    echo ""
    if confirm "Enable auto-index on git commit?"; then
        INSTALL_HOOK=true
    fi
fi

# ─── Step 6: Create cocoindex/ directory and files ────────────────────────────

info "Creating cocoindex/ directory..."
mkdir -p cocoindex

# Fetch and process templates
for tmpl in "${TEMPLATES[@]}"; do
    info "Fetching template: $tmpl"

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

    content=$(curl -fsSL "${REPO_URL}/templates/${tmpl}")

    # Substitute placeholders
    content="${content//\{\{PROJECT\}\}/$PROJECT_NAME}"
    content="${content//\{\{PORT\}\}/$PORT}"

    echo "$content" > "cocoindex/$tmpl"
done

info "Files created."

# ─── Step 7: Install post-commit hook ────────────────────────────────────────

if $INSTALL_HOOK; then
    info "Installing post-commit hook..."
    HOOK_FILE=".git/hooks/post-commit"
    HOOK_CONTENT="
# cocoindex auto-update
(cd \"$(pwd)/cocoindex\" && .venv/bin/cocoindex update main.py &>/dev/null &)"

    if [ -f "$HOOK_FILE" ]; then
        # Append if hook exists and doesn't already have cocoindex
        if ! grep -q "cocoindex auto-update" "$HOOK_FILE"; then
            echo "$HOOK_CONTENT" >> "$HOOK_FILE"
        fi
    else
        mkdir -p .git/hooks
        echo "#!/bin/sh" > "$HOOK_FILE"
        echo "$HOOK_CONTENT" >> "$HOOK_FILE"
    fi
    chmod +x "$HOOK_FILE"
    info "Post-commit hook installed."
fi

# ─── Step 8: Create venv and install dependencies ────────────────────────────

info "Creating Python virtual environment..."
$PYTHON_CMD -m venv cocoindex/.venv

info "Installing dependencies (this may take a few minutes)..."
cocoindex/.venv/bin/pip install --upgrade pip -q
cocoindex/.venv/bin/pip install -r cocoindex/requirements.txt -q

info "Dependencies installed."

# ─── Step 9: Check for Docker and start Postgres ─────────────────────────────

if ! command -v docker &>/dev/null; then
    error "Docker is required but not found. Please install Docker and re-run."
fi

info "Starting CocoIndex Postgres..."
docker compose -f cocoindex/docker-compose.yml up -d

# Wait for healthy
info "Waiting for Postgres to be ready..."
RETRIES=30
until docker compose -f cocoindex/docker-compose.yml exec -T cocoindex-postgres pg_isready -U cocoindex -d cocoindex &>/dev/null; do
    RETRIES=$((RETRIES - 1))
    if [ $RETRIES -le 0 ]; then
        error "Postgres failed to start. Check: docker compose -f cocoindex/docker-compose.yml logs"
    fi
    sleep 2
done
info "Postgres is ready."

# ─── Step 10: Run initial index ──────────────────────────────────────────────

info "Running initial index (this may take a while on first run)..."
cd cocoindex
.venv/bin/cocoindex setup main.py -f
.venv/bin/cocoindex update main.py
cd ..

info "Initial index complete."

# ─── Step 11: Configure .mcp.json ────────────────────────────────────────────

COCOINDEX_DIR="$(pwd)/cocoindex"
MCP_JSON="${PROJECT_DIR}/.mcp.json"
SERVER_NAME="${PROJECT_NAME}_cocoindex"

info "Configuring Claude MCP settings..."

# Use Python (already verified available) to handle JSON merge
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

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║           Setup Complete!                    ║"
echo "╚══════════════════════════════════════════════╝"
echo ""
info "To manually re-index: cd cocoindex && .venv/bin/cocoindex update main.py"
info "To edit patterns: cocoindex/cocoindex.yaml"
echo ""
