# DaVinci Resolve Lua Bridge

Let an AI assistant drive **DaVinci Resolve Free** through [MCP](https://modelcontextprotocol.io): create timelines, add markers, set clip properties, queue renders, or run any Resolve API call.

It is built for Resolve Free 21.1+, where Python scripts no longer appear in the Scripts menu, so the part that runs inside Resolve is written in **Lua**.

## How it works

```
Claude / any MCP client  <-- stdio -->  resolve_lua_mcp.py  <-- files -->  CursorBridgeLua.lua (inside Resolve)
```

- `CursorBridgeLua.lua` runs inside Resolve (Workspace → Scripts). It watches `~/.davinci-resolve-mcp-lua/request.lua`, runs the Lua it finds there, and writes the JSON result to Resolve's own log.
- `resolve_lua_mcp.py` is the MCP server. It writes the request file and reads the answer from `ResolveDebug.txt`.
- `bridge_cli.py` runs a Lua file through the same path from the terminal.

## Requirements

- DaVinci Resolve Free 21.1 or newer (tested on 21.1.1)
- macOS (tested). On Windows or Linux the Scripts folder and the `LOG` path at the top of `resolve_lua_mcp.py` are different and would need changing.
- Python 3.10+
- An MCP client such as Claude Code

## Install

```bash
git clone https://github.com/shreejalgiri6/davinci-resolve-lua-bridge.git
cd davinci-resolve-lua-bridge
python3.11 -m venv venv   # any Python 3.10+; macOS's built-in python3 is 3.9 and too old
venv/bin/pip install -r requirements.txt
cp CursorBridgeLua.lua ~/Library/Application\ Support/Blackmagic\ Design/DaVinci\ Resolve/Fusion/Scripts/Utility/
```

Register the server with your MCP client. For Claude Code:

```bash
claude mcp add davinci-resolve -- "$PWD/venv/bin/python" "$PWD/resolve_lua_mcp.py"
```

Then restart Resolve, open a project, choose **Workspace → Scripts → CursorBridgeLua**, and leave it running. It runs silently: Resolve's Console stays empty while the bridge is listening, so use the check below.

## Try it

Ask your assistant: *"What project is open in DaVinci Resolve?"* It should call `get_resolve_status` and answer with the Resolve version and project name.

## Tools (37)

| Area | Tools |
|---|---|
| Project | `get_resolve_status` `get_project_info` `list_projects` `load_project` `create_project` `save_project` `open_page` |
| Timelines | `list_timelines` `get_timeline_info` `switch_timeline` `create_timeline` `create_timeline_from_clips` `get_timeline_clips` `append_to_timeline` `delete_timeline_clips` `set_playhead` |
| Clips | `get_clip_properties` `set_clip_property` `set_clip_enabled` `set_clip_color` |
| Markers | `add_marker` `get_timeline_markers` `delete_markers` |
| Media pool | `get_media_pool` `import_media` `create_media_pool_folder` `delete_media_pool_clips` |
| Render | `get_render_formats` `get_render_codecs` `set_render_settings` `set_render_format` `add_render_job` `start_rendering` `get_render_status` `stop_rendering` |
| Escape hatch | `run_lua` runs any Lua inside Resolve with `resolve` and a helper table `H` in scope. `stop_bridge` stops the bridge. |

## Safety

- `run_lua` and the delete tools can change or remove project data. Try it on a copy of a project first.
- While the bridge runs, whatever is written to `~/.davinci-resolve-mcp-lua/request.lua` is executed inside Resolve. Anything that can write to your home folder can do this, so stop the bridge when you are not using it (`stop_bridge`, or quit Resolve).
- No network port is opened. The MCP server talks to your client over stdio and to Resolve through local files. Results, including clip names and paths, are written to Resolve's log file.

## Troubleshooting

- **"The Lua bridge is not running"**: open a project, then run Workspace → Scripts → CursorBridgeLua again.
- **Nothing under Workspace → Scripts**: check the file is in the `Utility` folder above, then restart Resolve.
- **Resolve did not answer within 60s**: the bridge may be stopped, or Resolve may be busy with a long task.

## Credit

This project follows [davinci-resolve-mcp](https://github.com/hiteshK03/davinci-resolve-mcp) by Hitesh Kandala (MIT): the same idea of a bridge launched from Resolve's Scripts menu, and the same tool names. That project is Python and HTTP-based, and its README lists 162 tools. This one swaps the transport for files and Lua so it runs where Python scripts are not listed. Both copyright notices are in [LICENSE](LICENSE).

## Author

Made by [Shreejal Giri](https://shreejalgiri.com.np).

## License

MIT. See [LICENSE](LICENSE).
