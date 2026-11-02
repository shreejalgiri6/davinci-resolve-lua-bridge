#!/usr/bin/env python3
"""MCP server for DaVinci Resolve that talks to CursorBridgeLua.lua running inside Resolve.

Works on Resolve Free 21.1+, where Python scripts are no longer listed in the Scripts menu.
Transport is a small request/response folder: ~/.davinci-resolve-mcp-lua
"""

import json
import os
import threading
import time
import uuid
from pathlib import Path

from mcp.server.fastmcp import FastMCP

DIR = Path.home() / ".davinci-resolve-mcp-lua"
DIR.mkdir(parents=True, exist_ok=True)

mcp = FastMCP("davinci-resolve-lua")
_lock = threading.Lock()


def lua(value):
    """Convert a Python value to a Lua literal."""
    if value is None:
        return "nil"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return _lua_str(value)
    if isinstance(value, (list, tuple)):
        return "{" + ",".join(lua(v) for v in value) + "}"
    if isinstance(value, dict):
        return "{" + ",".join("[%s]=%s" % (lua(k), lua(v)) for k, v in value.items()) + "}"
    raise TypeError("cannot convert %r to Lua" % (value,))


def _lua_str(s):
    out = ['"']
    for ch in s:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\0":
            out.append("\\0")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


LOG = (Path.home() / "Library/Application Support/Blackmagic Design/DaVinci Resolve/logs/ResolveDebug.txt")
_last_ok = 0.0


def _log_size():
    try:
        return LOG.stat().st_size
    except OSError:
        return 0


def _send(code, timeout):
    """Write a request file and wait for the matching MCPRESP line in Resolve's log."""
    rid = uuid.uuid4().hex
    level = 0
    while ("]" + "=" * level + "]") in code:
        level += 1
    eq = "=" * level
    body = 'return { id = "%s", code = [%s[%s]%s] }\n' % (rid, eq, code, eq)
    offset = _log_size()
    tmp = DIR / "request.lua.tmp"
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, DIR / "request.lua")
    marker = ("MCPRESP\t%s\t" % rid).encode()
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            size = LOG.stat().st_size
            if size < offset:  # log was rotated
                offset = 0
            if size > offset:
                with open(LOG, "rb") as f:
                    f.seek(offset)
                    chunk = f.read()
                pos = chunk.find(marker)
                if pos >= 0:
                    end = chunk.find(b"\n", pos)
                    if end >= 0:
                        return json.loads(chunk[pos + len(marker):end].decode("utf-8", "replace"))
        except OSError:
            pass
        time.sleep(0.05)
    return None


def call(code, timeout=60.0):
    """Send a Lua chunk to Resolve and return its result."""
    global _last_ok
    with _lock:
        if time.time() - _last_ok > 600:  # confirm the bridge is alive before a long wait
            if _send("return true", 20.0) is None:
                raise RuntimeError(
                    "The Lua bridge is not running. In DaVinci Resolve open a project, then choose "
                    "Workspace > Scripts > CursorBridgeLua."
                )
        data = _send(code, timeout)
        if data is None:
            raise TimeoutError("Resolve did not answer within %ss" % timeout)
        _last_ok = time.time()
        if not data.get("ok"):
            raise RuntimeError(data.get("error", "unknown Lua error"))
        return data.get("result")


# ---------------------------------------------------------------------------
# Status / project
# ---------------------------------------------------------------------------
@mcp.tool()
def get_resolve_status() -> dict:
    """Product, version, current page and open project name."""
    return call("""
local p = H.project()
return { product = resolve:GetProductName(), version = resolve:GetVersionString(),
         page = resolve:GetCurrentPage(), project = p and p:GetName() or nil }
""")


@mcp.tool()
def get_project_info() -> dict:
    """Open project: name, frame rate, resolution, timeline count, current timeline."""
    return call("""
local p = H.project()
if not p then return { error = "no project open" } end
local tl = p:GetCurrentTimeline()
return { name = p:GetName(), fps = p:GetSetting("timelineFrameRate"),
         width = p:GetSetting("timelineResolutionWidth"),
         height = p:GetSetting("timelineResolutionHeight"),
         timeline_count = p:GetTimelineCount(), current_timeline = tl and tl:GetName() or nil }
""")


@mcp.tool()
def list_projects() -> list:
    """Projects in the current project-manager folder."""
    return call("return H.pm():GetProjectListInCurrentFolder()")


@mcp.tool()
def load_project(name: str) -> bool:
    """Open a project by name (from the current folder)."""
    return call("return H.pm():LoadProject(%s) ~= nil" % lua(name))


@mcp.tool()
def create_project(name: str) -> bool:
    """Create and open a new project."""
    return call("return H.pm():CreateProject(%s) ~= nil" % lua(name))


@mcp.tool()
def save_project() -> bool:
    """Save the open project."""
    return call("return H.pm():SaveProject()") is True


@mcp.tool()
def open_page(page: str) -> bool:
    """Switch page: media, cut, edit, fusion, color, fairlight or deliver."""
    return call("return resolve:OpenPage(%s)" % lua(page))


# ---------------------------------------------------------------------------
# Timelines
# ---------------------------------------------------------------------------
@mcp.tool()
def list_timelines() -> list:
    """Names of all timelines in the project."""
    return call("""
local p, out = H.project(), {}
for i = 1, p:GetTimelineCount() do out[#out + 1] = p:GetTimelineByIndex(i):GetName() end
return out
""")


@mcp.tool()
def get_timeline_info() -> dict:
    """Current timeline: name, start/end frame, track counts, current timecode."""
    return call("""
local tl = H.timeline()
if not tl then return { error = "no timeline open" } end
return { name = tl:GetName(), start_frame = tl:GetStartFrame(), end_frame = tl:GetEndFrame(),
         video_tracks = tl:GetTrackCount("video"), audio_tracks = tl:GetTrackCount("audio"),
         subtitle_tracks = tl:GetTrackCount("subtitle"), timecode = tl:GetCurrentTimecode() }
""")


@mcp.tool()
def switch_timeline(name: str) -> bool:
    """Make a timeline current by name."""
    return call("""
local tl = H.find_timeline(%s)
if not tl then return false end
return H.project():SetCurrentTimeline(tl)
""" % lua(name))


@mcp.tool()
def create_timeline(name: str) -> bool:
    """Create an empty timeline."""
    return call("return H.pool():CreateEmptyTimeline(%s) ~= nil" % lua(name))


@mcp.tool()
def create_timeline_from_clips(name: str, clip_names: list[str]) -> dict:
    """Create a timeline from media-pool clips, given by clip name."""
    return call("""
local clips, missing = H.find_clips(%s)
if #clips == 0 then return { created = false, missing = missing } end
local tl = H.pool():CreateTimelineFromClips(%s, clips)
return { created = tl ~= nil, missing = missing }
""" % (lua(clip_names), lua(name)))


@mcp.tool()
def get_timeline_clips(track_type: str = "video") -> list:
    """Clips on every track of the current timeline. track_type: video, audio or subtitle."""
    return call("return H.track_items(%s)" % lua(track_type))


@mcp.tool()
def append_to_timeline(clip_names: list[str]) -> dict:
    """Append media-pool clips (by name) to the end of the current timeline."""
    return call("""
local clips, missing = H.find_clips(%s)
if #clips == 0 then return { appended = 0, missing = missing } end
local r = H.pool():AppendToTimeline(clips)
return { appended = r and #r or 0, missing = missing }
""" % lua(clip_names))


@mcp.tool()
def delete_timeline_clips(track_type: str, track: int, index: int, ripple: bool = False) -> bool:
    """Delete the clip at 1-based position `index` on the given track."""
    return call("""
local tl = H.timeline()
local items = tl:GetItemListInTrack(%s, %s)
if not items or not items[%s] then return false end
return tl:DeleteClips({ items[%s] }, %s)
""" % (lua(track_type), lua(track), lua(index), lua(index), lua(ripple)))


@mcp.tool()
def set_playhead(timecode: str) -> bool:
    """Move the playhead, e.g. '01:00:05:00'."""
    return call("return H.timeline():SetCurrentTimecode(%s)" % lua(timecode))


# ---------------------------------------------------------------------------
# Clip properties
# ---------------------------------------------------------------------------
@mcp.tool()
def get_clip_properties(track: int, index: int, track_type: str = "video") -> dict:
    """Properties of the clip at position `index` on a track (Pan, Zoom, Opacity, ...)."""
    return call("""
local items = H.timeline():GetItemListInTrack(%s, %s)
local it = items and items[%s]
if not it then return { error = "clip not found" } end
return { name = it:GetName(), properties = it:GetProperty() }
""" % (lua(track_type), lua(track), lua(index)))


@mcp.tool()
def set_clip_property(track: int, index: int, key: str, value: float, track_type: str = "video") -> bool:
    """Set a property such as Pan, Tilt, ZoomX, ZoomY, Opacity, RotationAngle, CropLeft."""
    return call("""
local items = H.timeline():GetItemListInTrack(%s, %s)
local it = items and items[%s]
if not it then return false end
return it:SetProperty(%s, %s)
""" % (lua(track_type), lua(track), lua(index), lua(key), lua(value)))


@mcp.tool()
def set_clip_enabled(track: int, index: int, enabled: bool, track_type: str = "video") -> bool:
    """Enable or disable a clip on the timeline."""
    return call("""
local items = H.timeline():GetItemListInTrack(%s, %s)
local it = items and items[%s]
if not it then return false end
return it:SetClipEnabled(%s)
""" % (lua(track_type), lua(track), lua(index), lua(enabled)))


@mcp.tool()
def set_clip_color(track: int, index: int, color: str, track_type: str = "video") -> bool:
    """Set a timeline clip's color label (Orange, Apricot, Yellow, Lime, Olive, Green, Teal, Navy, Blue, Purple, Violet, Pink, Tan, Beige, Brown, Chocolate)."""
    return call("""
local items = H.timeline():GetItemListInTrack(%s, %s)
local it = items and items[%s]
if not it then return false end
return it:SetClipColor(%s)
""" % (lua(track_type), lua(track), lua(index), lua(color)))


# ---------------------------------------------------------------------------
# Markers
# ---------------------------------------------------------------------------
@mcp.tool()
def add_marker(frame: int, color: str = "Blue", name: str = "", note: str = "", duration: int = 1) -> bool:
    """Add a timeline marker at `frame` (frames from timeline start)."""
    return call("return H.timeline():AddMarker(%s, %s, %s, %s, %s)" % (
        lua(frame), lua(color), lua(name), lua(note), lua(duration)))


@mcp.tool()
def get_timeline_markers() -> dict:
    """All markers on the current timeline, keyed by frame."""
    return call("""
local out = {}
for frame, m in pairs(H.timeline():GetMarkers() or {}) do
  out[tostring(frame)] = m
end
return out
""")


@mcp.tool()
def delete_markers(color: str = "All") -> bool:
    """Delete timeline markers of a color, or all of them."""
    return call("return H.timeline():DeleteMarkersByColor(%s)" % lua(color))


# ---------------------------------------------------------------------------
# Media pool
# ---------------------------------------------------------------------------
@mcp.tool()
def get_media_pool() -> list:
    """Every clip in the media pool (name, duration, resolution, path)."""
    return call("""
local out = {}
for _, c in ipairs(H.all_clips()) do
  out[#out + 1] = { name = c:GetName(), duration = c:GetClipProperty("Duration"),
                    resolution = c:GetClipProperty("Resolution"), fps = c:GetClipProperty("FPS"),
                    path = c:GetClipProperty("File Path") }
end
return out
""")


@mcp.tool()
def import_media(paths: list[str]) -> list:
    """Import files into the media pool by absolute path. Returns imported clip names."""
    return call("""
local ms = resolve:GetMediaStorage()
local imported = ms:AddItemListToMediaPool(%s)
local out = {}
for _, c in ipairs(imported or {}) do out[#out + 1] = c:GetName() end
return out
""" % lua(paths))


@mcp.tool()
def create_media_pool_folder(name: str) -> bool:
    """Create a bin under the media pool's current folder."""
    pool_call = "local pool = H.pool(); return pool:AddSubFolder(pool:GetCurrentFolder(), %s) ~= nil" % lua(name)
    return call(pool_call)


@mcp.tool()
def delete_media_pool_clips(clip_names: list[str]) -> bool:
    """Delete media-pool clips by name (cannot be undone from here)."""
    return call("""
local clips = H.find_clips(%s)
if #clips == 0 then return false end
return H.pool():DeleteClips(clips)
""" % lua(clip_names))


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
@mcp.tool()
def get_render_formats() -> dict:
    """Available render container formats."""
    return call("return H.project():GetRenderFormats()")


@mcp.tool()
def get_render_codecs(format: str) -> dict:
    """Codecs available for a render format, e.g. 'mp4'."""
    return call("return H.project():GetRenderCodecs(%s)" % lua(format))


@mcp.tool()
def set_render_settings(settings: dict) -> bool:
    """Set render settings, e.g. {"TargetDir": "/Users/me/out", "CustomName": "final", "SelectAllFrames": true}."""
    return call("return H.project():SetRenderSettings(%s)" % lua(settings))


@mcp.tool()
def set_render_format(format: str, codec: str) -> bool:
    """Set render format and codec, e.g. 'mp4' and 'H265'."""
    return call("return H.project():SetCurrentRenderFormatAndCodec(%s, %s)" % (lua(format), lua(codec)))


@mcp.tool()
def add_render_job() -> str:
    """Queue the current timeline with current render settings. Returns the job id."""
    return call("return H.project():AddRenderJob()")


@mcp.tool()
def start_rendering() -> bool:
    """Start rendering all queued jobs."""
    return call("return H.project():StartRendering()")


@mcp.tool()
def get_render_status(job_id: str = "") -> dict:
    """Status of a render job (or whether any render is in progress when job_id is empty)."""
    return call("""
local p = H.project()
local id = %s
return { rendering = p:IsRenderingInProgress(), job = id ~= "" and p:GetRenderJobStatus(id) or nil,
         jobs = p:GetRenderJobList() }
""" % lua(job_id))


@mcp.tool()
def stop_rendering() -> bool:
    """Stop the current render."""
    call("H.project():StopRendering() return true")
    return True


# ---------------------------------------------------------------------------
# Escape hatch + bridge control
# ---------------------------------------------------------------------------
@mcp.tool()
def run_lua(code: str) -> object:
    """Run arbitrary Lua inside Resolve and return its `return` value. `resolve` and helper table `H`
    (H.project(), H.timeline(), H.pool(), H.all_clips(), H.find_clips(names)) are in scope.
    Use for anything the other tools do not cover. This can modify or delete project data."""
    return call(code)


@mcp.tool()
def stop_bridge() -> bool:
    """Stop the Lua bridge inside Resolve (re-launch it from Workspace > Scripts)."""
    call("__STOP__")
    return True


if __name__ == "__main__":
    mcp.run()
