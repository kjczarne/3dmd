--[[
threedmd.lua — Pandoc Lua filter for 3DMarkdown depth filtering
================================================================

Usage:
    pandoc --lua-filter threedmd.lua -M depth=2 input.3dmd -o output.pdf
    pandoc --lua-filter threedmd.lua -M depth=2 input.3dmd -o output.md

The filter reads the `depth` metadata variable and removes any block or
inline whose depth tag exceeds the threshold.

Depth tag syntax recognised by this filter
------------------------------------------

Block-level: a standalone paragraph whose sole text is one of:
  {depth=N}     long form
  {dN}          short form

Span (multi-block) context:
  {start depth=N}  or  {sdN}   — all following blocks belong to depth N
  {end}            or  {e}     — end the span context

Inline span context (§3.3):
  {start depth=N}  or  {sdN}   — within text, opens an inline span
  {end}            or  {e}     — within text, closes the innermost inline span
  These carry state across Str nodes (and across block boundaries).

Inline trailing tags: suffix annotations of the form {depth=N} or {dN}
appended directly after a text run; the preceding text gets that depth.

NOTE: Because Pandoc converts `{depth=N}\nParagraph` into two separate
      AST nodes (a Para containing the tag, then the following Para), this
      filter must be stateful: it remembers when it has seen a depth tag and
      applies it to the next block.
--]]

local max_depth = math.huge  -- default: show everything

-- Read the `depth` metadata variable
function Meta(meta)
  if meta.depth then
    max_depth = tonumber(pandoc.utils.stringify(meta.depth)) or math.huge
  end
end

-- State: depth of the *next* block, active block-span depth, inline span stack.
local pending_depth = nil
local span_depth    = nil
local inline_span_stack = {}  -- innermost open inline-span depth is at the end

local function current_inline_depth()
  if #inline_span_stack > 0 then return inline_span_stack[#inline_span_stack] end
  return nil
end

-- ---------------------------------------------------------------------------
-- Tag detection helpers (block-level patterns, anchored to full string)
-- ---------------------------------------------------------------------------

local function extract_depth_tag(text)
  local n = text:match("^%s*{depth=(%d+)}%s*$")
  if n then return tonumber(n) end
  n = text:match("^%s*{d(%d+)}%s*$")
  if n then return tonumber(n) end
  return nil
end

local function extract_span_start(text)
  local n = text:match("^%s*{start%s+depth=(%d+)}%s*$")
  if n then return tonumber(n) end
  n = text:match("^%s*{sd(%d+)}%s*$")
  if n then return tonumber(n) end
  return nil
end

local function is_span_end(text)
  return text:match("^%s*{end}%s*$") ~= nil or text:match("^%s*{e}%s*$") ~= nil
end

-- ---------------------------------------------------------------------------
-- Block filter
-- ---------------------------------------------------------------------------

function Block(el)
  if el.tag == "Para" then
    local text = pandoc.utils.stringify(el)

    local n = extract_span_start(text)
    if n then
      span_depth = n
      return {}
    end

    if is_span_end(text) then
      if span_depth ~= nil then
        span_depth = nil
      elseif #inline_span_stack > 0 then
        -- Standalone {e} with no block span open: close innermost inline span (§3.3).
        table.remove(inline_span_stack)
      end
      return {}
    end

    n = extract_depth_tag(text)
    if n then
      pending_depth = n
      return {}
    end
  end

  local d = nil
  if pending_depth ~= nil then
    d = pending_depth
    pending_depth = nil
  elseif span_depth ~= nil then
    d = span_depth
  end

  if d ~= nil and d > max_depth then
    return {}
  end

  if el.tag == "Div" and el.attributes.depth then
    local d2 = tonumber(el.attributes.depth)
    if d2 and d2 > max_depth then
      return {}
    end
    el.attributes.depth = nil
    return el
  end

  return el
end

-- ---------------------------------------------------------------------------
-- Inline filter: Pandoc native [text]{depth=N} spans
-- ---------------------------------------------------------------------------

function Span(el)
  if el.attributes.depth then
    local d = tonumber(el.attributes.depth)
    if d and d > max_depth then
      return {}
    end
    el.attributes.depth = nil
    return el
  end
end

-- ---------------------------------------------------------------------------
-- Inline filter: Str nodes containing depth markers
-- ---------------------------------------------------------------------------

-- Process a Str node that may contain any mix of trailing depth tags,
-- inline span opens, and inline span closes.  Returns a list of Inlines
-- (possibly empty if everything was suppressed).
local function process_str(text)
  local result = {}
  local pos = 1

  -- Helper: find the earliest of several candidate match positions.
  local best_s, best_e, best_type, best_n

  local function try(s, e, typ, n)
    if s ~= nil and (best_s == nil or s < best_s) then
      best_s, best_e, best_type, best_n = s, e, typ, n
    end
  end

  while pos <= #text do
    best_s, best_e, best_type, best_n = nil, nil, nil, nil

    -- Trailing long form {depth=N}
    local s, e, n = text:find("{depth=(%d+)}", pos)
    try(s, e, "trail", tonumber(n))

    -- Trailing short form {dN}  — won't match {sdN} because "{sd" != "{d"
    s, e, n = text:find("{d(%d+)}", pos)
    try(s, e, "trail", tonumber(n))

    -- Inline span open long form {start depth=N}
    s, e, n = text:find("{start%s+depth=(%d+)}", pos)
    try(s, e, "open", tonumber(n))

    -- Inline span open short form {sdN}
    s, e, n = text:find("{sd(%d+)}", pos)
    try(s, e, "open", tonumber(n))

    -- Inline span close {end}
    s, e = text:find("{end}", pos, true)
    try(s, e, "close", nil)

    -- Inline span close {e}
    s, e = text:find("{e}", pos, true)
    try(s, e, "close", nil)

    if best_s == nil then
      -- No more markers: emit the rest at current inline span depth.
      local remaining = text:sub(pos)
      if remaining ~= "" then
        local d = current_inline_depth()
        if d == nil or d <= max_depth then
          table.insert(result, pandoc.Str(remaining))
        end
      end
      break
    end

    local before = text:sub(pos, best_s - 1)

    if best_type == "trail" then
      -- Text before the trailing tag gets the tag's depth.
      if before ~= "" then
        local tag_d = best_n
        if tag_d == nil or tag_d <= max_depth then
          table.insert(result, pandoc.Str(before))
        end
      end
      -- Tag itself is consumed.

    elseif best_type == "open" then
      -- Text before the open marker gets the current span depth.
      if before ~= "" then
        local d = current_inline_depth()
        if d == nil or d <= max_depth then
          table.insert(result, pandoc.Str(before))
        end
      end
      table.insert(inline_span_stack, best_n)

    elseif best_type == "close" then
      -- Text before the close marker gets the current span depth.
      if before ~= "" then
        local d = current_inline_depth()
        if d == nil or d <= max_depth then
          table.insert(result, pandoc.Str(before))
        end
      end
      if #inline_span_stack > 0 then
        table.remove(inline_span_stack)
      end
    end

    pos = best_e + 1
  end

  return result
end

function Str(el)
  local text = el.text

  -- Fast path: no marker characters at all.
  if not text:find("{") then
    local d = current_inline_depth()
    if d and d > max_depth then return {} end
    return el
  end

  -- Check whether this Str actually contains a recognised marker.
  local has_marker = (
    text:find("{depth=%d") or
    text:find("{d%d") or
    text:find("{start") or
    text:find("{sd%d") or
    text:find("{end}") or
    text:find("{e}")
  )

  if not has_marker then
    local d = current_inline_depth()
    if d and d > max_depth then return {} end
    return el
  end

  local inlines = process_str(text)
  if #inlines == 0 then return {} end
  return inlines
end

return {
  { Meta = Meta },
  { Block = Block },
  { Span = Span, Str = Str },
}
