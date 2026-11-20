#!/usr/bin/env bash
# Instala dependencias, las skills (Claude Code y agy) y registra el MCP en agy.
# Uso: scripts/install.sh [URL_DEL_TABLERO]   (por defecto http://localhost:1000)
set -euo pipefail

BOARD_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BOARD_URL="${1:-http://localhost:1000}"

python3 -m pip install -r "$BOARD_DIR/requirements.txt"
chmod +x "$BOARD_DIR/run.py" "$BOARD_DIR/mcp_server.py" "$BOARD_DIR"/agents/*.py

render() {  # render <plantilla> <destino>
  mkdir -p "$(dirname "$2")"
  sed -e "s|__BOARD_DIR__|$BOARD_DIR|g" -e "s|__BOARD_URL__|$BOARD_URL|g" "$1" > "$2"
  echo "skill -> $2"
}

render "$BOARD_DIR/skills/claude/board-orchestrator/SKILL.md" "$HOME/.claude/skills/board-orchestrator/SKILL.md"
render "$BOARD_DIR/skills/agy/agentic-board/SKILL.md" "$HOME/.gemini/config/skills/agentic-board/SKILL.md"

MCP_CONFIG="$HOME/.gemini/config/mcp_config.json"
python3 - "$MCP_CONFIG" "$BOARD_DIR/mcp_server.py" <<'PY'
import json, sys
from pathlib import Path
path, server = Path(sys.argv[1]), sys.argv[2]
cfg = json.loads(path.read_text()) if path.exists() else {}
cfg.setdefault("mcpServers", {})["agentic_board"] = {"command": server, "args": []}
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(cfg, indent=2) + "\n")
print(f"MCP agentic_board -> {path}")
PY

echo "Listo. Arranca el tablero con: cd $BOARD_DIR && python3 run.py"
