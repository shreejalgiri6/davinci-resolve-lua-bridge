-- CursorBridgeLua: bridge running INSIDE DaVinci Resolve (Workspace > Scripts > CursorBridgeLua).
-- Built for Resolve Free 21.1+, where Python scripts are not listed and Lua has no io/os.execute.
--
-- Channels (both are things Resolve's restricted Lua still allows):
--   MCP server -> Resolve : request.lua in ~/.davinci-resolve-mcp-lua, loaded with dofile().
--                           It returns { id = "...", code = "<lua source>" }.
--   Resolve -> MCP server : one line "MCPRESP<TAB><id><TAB><json>" written with printerr(),
--                           which ends up in Resolve's ResolveDebug.txt log that the server tails.
-- Stop: the server sends a request whose code is "__STOP__".

local HOME = os.getenv and os.getenv("HOME")
if not HOME then
  print("[CursorBridgeLua] ERROR: HOME is not set, so the request folder cannot be found.")
  return
end
local REQUEST = HOME .. "/.davinci-resolve-mcp-lua/request.lua"

local function log(msg)
  print("[CursorBridgeLua] " .. tostring(msg))
end

-- ---------------------------------------------------------------------------
-- Resolve bootstrap
-- ---------------------------------------------------------------------------
local resolve = _G.resolve
local function init_resolve()
  local attempts = {
    function() return resolve end,
    function() return Resolve() end,
    function() return fu:GetResolve() end,
    function() return fusion:GetResolve() end,
    function() return bmd.scriptapp("Resolve") end,
  }
  for _, fn in ipairs(attempts) do
    local ok, r = pcall(fn)
    if ok and r then return r end
  end
  return nil
end
resolve = init_resolve()

-- ---------------------------------------------------------------------------
-- JSON encoder
-- ---------------------------------------------------------------------------
local escapes = {
  ['"'] = '\\"', ['\\'] = '\\\\', ['\b'] = '\\b', ['\f'] = '\\f',
  ['\n'] = '\\n', ['\r'] = '\\r', ['\t'] = '\\t',
}
local function esc(s)
  s = s:gsub('[%c"\\]', function(c)
    return escapes[c] or string.format("\\u%04x", c:byte())
  end)
  return s
end

local encode
local function is_array(t)
  local n = 0
  for k, _ in pairs(t) do
    if type(k) ~= "number" or k < 1 or math.floor(k) ~= k then return false end
    n = n + 1
  end
  -- Resolve API lists are 1-indexed and contiguous; an empty table encodes as [].
  for i = 1, n do
    if t[i] == nil then return false end
  end
  return true
end

encode = function(v, depth)
  depth = depth or 0
  if depth > 12 then return '"<max depth>"' end
  local t = type(v)
  if t == "nil" then return "null" end
  if t == "boolean" then return tostring(v) end
  if t == "number" then
    if v ~= v or v == math.huge or v == -math.huge then return "null" end
    return tostring(v)
  end
  if t == "string" then return '"' .. esc(v) .. '"' end
  if t == "table" then
    if is_array(v) then
      local out = {}
      for i = 1, #v do out[#out + 1] = encode(v[i], depth + 1) end
      return "[" .. table.concat(out, ",") .. "]"
    end
    local out = {}
    for k, val in pairs(v) do
      out[#out + 1] = '"' .. esc(tostring(k)) .. '":' .. encode(val, depth + 1)
    end
    return "{" .. table.concat(out, ",") .. "}"
  end
  -- userdata / function / etc.
  return '"' .. esc(tostring(v)) .. '"'
end

-- ---------------------------------------------------------------------------
-- Helpers exposed to request chunks
-- ---------------------------------------------------------------------------
local H = {}

function H.pm() return resolve and resolve:GetProjectManager() end
function H.project() local pm = H.pm(); return pm and pm:GetCurrentProject() end
function H.timeline() local p = H.project(); return p and p:GetCurrentTimeline() end
function H.pool() local p = H.project(); return p and p:GetMediaPool() end

function H.all_clips(folder, acc)
  local pool = H.pool()
  if not pool then return {} end
  folder = folder or pool:GetRootFolder()
  acc = acc or {}
  for _, c in ipairs(folder:GetClipList() or {}) do acc[#acc + 1] = c end
  for _, sub in ipairs(folder:GetSubFolderList() or {}) do H.all_clips(sub, acc) end
  return acc
end

function H.find_clips(names)
  local all = H.all_clips()
  local found, missing = {}, {}
  for _, n in ipairs(names) do
    local hit = nil
    for _, c in ipairs(all) do
      if c:GetName() == n then hit = c; break end
    end
    if hit then found[#found + 1] = hit else missing[#missing + 1] = n end
  end
  return found, missing
end

function H.find_timeline(name)
  local p = H.project()
  if not p then return nil end
  for i = 1, p:GetTimelineCount() do
    local tl = p:GetTimelineByIndex(i)
    if tl and tl:GetName() == name then return tl end
  end
  return nil
end

function H.track_items(kind)
  local tl = H.timeline()
  if not tl then return {} end
  local out = {}
  for t = 1, (tl:GetTrackCount(kind) or 0) do
    local items = {}
    for _, it in ipairs(tl:GetItemListInTrack(kind, t) or {}) do
      items[#items + 1] = {
        name = it:GetName(), start = it:GetStart(), ["end"] = it:GetEnd(),
        duration = it:GetDuration(),
      }
    end
    out[#out + 1] = { track = t, name = tl:GetTrackName(kind, t), items = items }
  end
  return out
end

-- ---------------------------------------------------------------------------
-- Request execution
-- ---------------------------------------------------------------------------
local function make_env()
  local env = setmetatable({}, { __index = _G })
  env.resolve = resolve
  env.H = H
  env.fusion = fusion
  env.json = encode
  return env
end

local function handle_request(code)
  local fn, err = (loadstring or load)(code, "request")
  if not fn then
    return encode({ ok = false, error = "syntax error: " .. tostring(err) })
  end
  if setfenv then setfenv(fn, make_env()) end
  local ok, result = pcall(fn)
  if not ok then
    return encode({ ok = false, error = tostring(result) })
  end
  return encode({ ok = true, result = result })
end

local function respond(id, json)
  printerr("MCPRESP\t" .. id .. "\t" .. json)
end

local function sleep(sec)
  if bmd and bmd.wait then
    local ok = pcall(bmd.wait, sec)
    if ok then return end
  end
  local t = os.clock()
  while os.clock() - t < sec do end
end

local function read_request()
  if not bmd.fileexists(REQUEST) then return nil end
  local ok, req = pcall(dofile, REQUEST)
  if ok and type(req) == "table" and req.id and req.code then return req end
  return nil
end

if not resolve then
  log("ERROR: could not obtain the Resolve object. Run this from Workspace > Scripts.")
  return
end

-- Single instance: the newest launch claims ownership through Fusion's app-wide data store,
-- and older copies of the loop exit on their next iteration.
local me = tostring(os.time()) .. "-" .. tostring(math.random(1, 1e9))
local has_data = pcall(function() fu:SetData("CursorBridgeLua.owner", me) end)
local function still_owner()
  if not has_data then return true end
  local ok, owner = pcall(function() return fu:GetData("CursorBridgeLua.owner") end)
  return not ok or owner == nil or owner == me
end

-- Ignore any request left over from a previous session.
local first = read_request()
local last_id = first and first.id or nil

log("Connected to Resolve: " .. tostring(resolve:GetProductName()) .. " " .. tostring(resolve:GetVersionString()))
log("Bridge is running. Request file: " .. REQUEST)
printerr("MCPBRIDGE started")

while true do
  if not still_owner() then
    printerr("MCPBRIDGE replaced by a newer launch")
    break
  end
  local req = read_request()
  if req and req.id ~= last_id then
    last_id = req.id
    if req.code == "__STOP__" then
      respond(req.id, encode({ ok = true, result = "stopped" }))
      break
    end
    respond(req.id, handle_request(req.code))
  end
  sleep(0.1)
end

printerr("MCPBRIDGE stopped")
log("Bridge stopped.")
