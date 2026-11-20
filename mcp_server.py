#!/usr/bin/env python3
import sys
import json
import os
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

import urllib.request
from app.mcp_service import MCP_TOOLS, execute_mcp_tool

BOARD_API = os.environ.get("BOARD_API", "http://localhost:1000/api").rstrip("/")


def call_tool(name: str, arguments: dict) -> dict:
    """Run the tool through the board's HTTP API so every change is broadcast live
    (WebSocket) to the operator. Falls back to direct DB access if the server is down."""
    body = json.dumps({"name": name, "arguments": arguments}).encode()
    req = urllib.request.Request(f"{BOARD_API}/mcp/call", data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except Exception as exc:
        log(f"HTTP API unavailable ({exc}); executing tool locally (no live broadcast).")
        return execute_mcp_tool(name, arguments)

def log(msg: str):
    """Write debug logs to stderr to keep stdout strictly for JSON-RPC."""
    sys.stderr.write(f"[AgenticBoard-MCP] {msg}\n")
    sys.stderr.flush()

def handle_message(msg: dict) -> dict:
    msg_id = msg.get("id")
    method = msg.get("method")
    params = msg.get("params", {})

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {
                        "listChanged": False
                    }
                },
                "serverInfo": {
                    "name": "agentic-board-mcp",
                    "version": "1.0.0"
                }
            }
        }

    elif method == "notifications/initialized":
        log("Client initialized connection.")
        return None  # Notifications do not return a response

    elif method == "ping":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {}
        }

    elif method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "tools": MCP_TOOLS
            }
        }

    elif method == "tools/call":
        tool_name = params.get("name")
        tool_args = params.get("arguments", {})
        log(f"Calling tool: {tool_name} with args: {tool_args}")
        
        result = call_tool(tool_name, tool_args)
        is_error = not result.get("success", True)
        
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(result, indent=2, ensure_ascii=False)
                    }
                ],
                "isError": is_error
            }
        }

    else:
        log(f"Method not found: {method}")
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {
                "code": -32601,
                "message": f"Method '{method}' not found"
            }
        }

def main():
    log("Starting Agentic Board stdio MCP Server...")
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            res = handle_message(req)
            if res is not None:
                sys.stdout.write(json.dumps(res) + "\n")
                sys.stdout.flush()
        except Exception as e:
            log(f"Error handling message: {e}")
            if "id" in locals() and req.get("id"):
                err_res = {
                    "jsonrpc": "2.0",
                    "id": req.get("id"),
                    "error": {"code": -32603, "message": str(e)}
                }
                sys.stdout.write(json.dumps(err_res) + "\n")
                sys.stdout.flush()

if __name__ == "__main__":
    main()
