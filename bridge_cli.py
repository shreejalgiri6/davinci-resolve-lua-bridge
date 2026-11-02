#!/usr/bin/env python3
"""Run a Lua file through CursorBridgeLua from the command line and print the JSON result.

Usage: python bridge_cli.py script.lua [timeout_seconds]
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import resolve_lua_mcp as bridge  # noqa: E402

code = Path(sys.argv[1]).read_text(encoding="utf-8")
timeout = float(sys.argv[2]) if len(sys.argv) > 2 else 60.0
print(json.dumps(bridge.call(code, timeout=timeout), indent=2, ensure_ascii=False))
