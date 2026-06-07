"""
threedmd.parser
~~~~~~~~~~~~~~~

Parses depth-tagged Markdown into a ``Document`` AST.

Depth tag syntax
----------------

Block-level (must appear at the start of a line, alone):

    {depth=N}   or shortform  {dN}
    This entire paragraph belongs to layer N.

Span (multi-block) depth context:

    {start depth=N}  or shortform  {sdN}
    All blocks up to the matching {end} / {e} belong to layer N.
    {end}

Inline (within a paragraph):

    Some text{depth=2} and more text{depth=3}.
    Some text{d2} and more text{d3}.            (shortform)

Untagged content has ``depth=None`` and renders at every level.

The parser operates in two passes:
1. Split the raw text into ``Block`` objects (preserving fenced code blocks
   and other verbatim regions untouched).
2. For each non-verbatim block, scan for inline depth tags.

Span tags are normalised into per-block depth tags before split/parse.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterator

# ---------------------------------------------------------------------------
# Regular expressions
# ---------------------------------------------------------------------------

# A block-level depth tag: a line that is exactly "{depth=N}" or "{dN}" (with
# optional surrounding whitespace), followed immediately by block content.
# Pattern  \{d(?:epth=)?N\}  matches both long form {depth=N} and short {dN}.
_BLOCK_TAG_RE = re.compile(
    r"^[ \t]*\{d(?:epth=)?(?P<depth>\d+)\}[ \t]*\n(?P<body>(?:.|\n)*?)(?=\n\n|\n[ \t]*\{d(?:epth=)?\d+\}|\Z)",
    re.MULTILINE,
)

# Combined inline marker regex.  Three token types:
#   trailing tag     {depth=N} / {dN}              — text before the tag has depth N
#   inline span open {start depth=N} / {sdN}       — same shortform as block span (§3.2)
#                                                    but matched anywhere in text
#   inline span end  {end} / {e}                   — closes innermost open inline span
#                                                    (standalone-line occurrences that
#                                                     close a block span are already
#                                                     consumed by _normalize_span_tags)
_INLINE_MARKER_RE = re.compile(
    r"\{d(?:epth=)?(?P<trail>\d+)\}"               # trailing tag
    r"|\{(?:start[ \t]+depth=|sd)(?P<open>\d+)\}"  # inline span open
    r"|\{(?:end|e)\}"                              # inline span end
)

# Keep the simple regex for code that only needs to detect *any* inline depth annotation.
_INLINE_TAG_RE = re.compile(r"\{d(?:epth=)?(?P<depth>\d+)\}")

# Span start tag: {start depth=N} or {sdN} on a line by itself.
_SPAN_START_RE = re.compile(r"^[ \t]*\{(?:start[ \t]+depth=|sd)(?P<depth>\d+)\}[ \t]*$")

# Span end tag: {end} or {e} on a line by itself.
_SPAN_END_RE = re.compile(r"^[ \t]*\{(?:end|e)\}[ \t]*$")

# Fenced code block: ``` or ~~~ with optional info string
_FENCE_RE = re.compile(r"^(?P<fence>`{3,}|~{3,})[^\n]*\n.*?\n(?P=fence)", re.MULTILINE | re.DOTALL)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class InlineSpan:
    """A run of inline text with an optional depth constraint."""

    text: str
    depth: int | None = None  # None → always visible


@dataclass
class Block:
    """A Markdown block (paragraph, heading, list, fenced code, …).

    ``depth``      — block-level constraint (None → always visible).
    ``spans``      — inline structure; if non-empty, ``raw`` should be
                     reconstructed from spans for rendering.
    ``raw``        — original source text of the block (no depth tag line).
    ``verbatim``   — True for fenced code blocks / HTML blocks that must not
                     be modified by inline processing.
    """

    raw: str
    depth: int | None = None
    spans: list[InlineSpan] = field(default_factory=list)
    verbatim: bool = False

    def max_inline_depth(self) -> int | None:
        depths = [s.depth for s in self.spans if s.depth is not None]
        return max(depths, default=None)

    def min_depth(self) -> int | None:
        """Effective depth: block depth takes priority over inline."""
        return self.depth


@dataclass
class Document:
    """Parsed representation of a .3dmd file."""

    blocks: list[Block]
    source: str

    def depth_levels(self) -> list[int]:
        """Return the sorted list of distinct depth values present."""
        levels: set[int] = set()
        for b in self.blocks:
            if b.depth is not None:
                levels.add(b.depth)
            for s in b.spans:
                if s.depth is not None:
                    levels.add(s.depth)
        return sorted(levels)


# ---------------------------------------------------------------------------
# Parser implementation
# ---------------------------------------------------------------------------


def _normalize_span_tags(text: str) -> str:
    """Expand {start depth=N}/{sdN}...{end}/{e} regions into per-block {depth=N} tags.

    Must be called after _protect_fences so fence placeholders are opaque tokens
    that won't be mistaken for depth tags and don't need special handling here.
    """
    lines = text.split("\n")
    result: list[str] = []
    active_depth: int | None = None
    at_block_start = True

    for line in lines:
        m = _SPAN_START_RE.match(line)
        if m:
            active_depth = int(m.group("depth"))
            at_block_start = True
            continue  # drop the start tag line

        if _SPAN_END_RE.match(line):
            if active_depth is not None:
                active_depth = None
                at_block_start = True
                continue  # drop {e} only when it is closing an open block span
            else:
                result.append(line)  # no block span open: pass through for inline processing
                continue

        if active_depth is not None:
            if line.strip() == "":
                at_block_start = True
                result.append(line)
            else:
                if at_block_start:
                    result.append(f"{{depth={active_depth}}}")
                    at_block_start = False
                result.append(line)
        else:
            result.append(line)

    return "\n".join(result)


def _protect_fences(text: str) -> tuple[str, dict[str, str]]:
    """Replace fenced code blocks with placeholder tokens to prevent
    depth-tag regex from matching inside them."""
    vault: dict[str, str] = {}
    counter = 0

    def _replace(m: re.Match) -> str:  # type: ignore[type-arg]
        nonlocal counter
        token = f"\x00FENCE{counter}\x00"
        vault[token] = m.group(0)
        counter += 1
        return token

    protected = _FENCE_RE.sub(_replace, text)
    return protected, vault


def _restore_fences(text: str, vault: dict[str, str]) -> str:
    for token, original in vault.items():
        text = text.replace(token, original)
    return text


def _split_blocks(text: str) -> Iterator[tuple[str, int | None]]:
    """Yield ``(block_text, depth_or_None)`` pairs by splitting on block-level
    depth tags.  Untagged text is further split on blank lines so that headings
    and depth-tagged paragraphs are never merged into one block."""

    last_end = 0
    for m in _BLOCK_TAG_RE.finditer(text):
        preceding = text[last_end : m.start()]
        for para in re.split(r"\n{2,}", preceding):
            para = para.strip()
            if para:
                yield para, None
        yield m.group("body").strip(), int(m.group("depth"))
        last_end = m.end()

    tail = text[last_end:]
    for para in re.split(r"\n{2,}", tail):
        para = para.strip()
        if para:
            yield para, None


def _parse_inline(
    raw: str,
    entry_depth: int | None = None,
) -> tuple[list[InlineSpan], int | None]:
    """Split a block's text into ``InlineSpan`` objects.

    Handles three marker types (via ``_INLINE_MARKER_RE``):

    * **Trailing tag** ``{dN}`` / ``{depth=N}`` — text preceding the tag gets
      depth N (classic suffix-annotation style).
    * **Inline span open** ``{sdN}`` / ``{start depth=N}`` — opens a depth
      context for all subsequent text until the matching close.
    * **Inline span close** ``{e}`` / ``{end}`` — closes the innermost open
      inline span.

    ``entry_depth`` carries an already-open inline span depth in from the
    previous block (cross-block spans).  The function returns the list of
    ``InlineSpan`` objects together with an ``exit_depth`` that must be
    forwarded as the ``entry_depth`` of the next block (``None`` if no span
    is open at block end).

    Trailing tags and inline span opens interact as follows: a trailing tag
    annotates the text segment that precedes it, using the tag's own depth.
    An inline span open sets the *current span depth* for all subsequent text
    until closed.  Trailing tags take precedence within their own segment (the
    trailing depth is used, not the span depth); the span depth resumes for
    text after the trailing tag.

    Example (no entry span)::

        "We found three hits{d1}, all stable{d2}."

    → ``([InlineSpan("We found three hits", 1), InlineSpan(", all stable", 2),
           InlineSpan(".", None)], None)``

    Example (inline span)::

        "Plain. {sd2}Detail here.{e} Plain again."

    → ``([InlineSpan("Plain. ", None), InlineSpan("Detail here.", 2),
           InlineSpan(" Plain again.", None)], None)``
    """
    spans: list[InlineSpan] = []
    # Stack of open inline span depths (innermost last).
    span_stack: list[int] = [entry_depth] if entry_depth is not None else []
    pos = 0

    def _current_span_depth() -> int | None:
        return span_stack[-1] if span_stack else None

    for m in _INLINE_MARKER_RE.finditer(raw):
        text_before = raw[pos : m.start()]

        if m.lastgroup == "trail":
            # Trailing depth tag: text_before gets this explicit depth.
            depth = int(m.group("trail"))
            if text_before:
                spans.append(InlineSpan(text=text_before, depth=depth))
            pos = m.end()

        elif m.lastgroup == "open":
            # Inline span open: flush text_before at current span depth, then push.
            if text_before:
                spans.append(InlineSpan(text=text_before, depth=_current_span_depth()))
            span_stack.append(int(m.group("open")))
            pos = m.end()

        else:
            # Inline span close {e} / {end}: flush text_before at current span
            # depth, then pop the stack (if non-empty).
            if text_before:
                spans.append(InlineSpan(text=text_before, depth=_current_span_depth()))
            if span_stack:
                span_stack.pop()
            pos = m.end()

    # Remaining text after the last marker uses the current (possibly still open) span depth.
    tail = raw[pos:]
    if tail:
        spans.append(InlineSpan(text=tail, depth=_current_span_depth()))

    exit_depth = span_stack[-1] if span_stack else None
    return spans, exit_depth


def parse(source: str) -> Document:
    """Parse *source* text and return a :class:`Document`.

    Parameters
    ----------
    source:
        Raw text of a ``.md`` or ``.3dmd`` file.
    """
    protected, vault = _protect_fences(source)
    normalized = _normalize_span_tags(protected)
    blocks: list[Block] = []

    # Carry open inline spans across block boundaries (§3.3 cross-block spans).
    inline_span_depth: int | None = None

    for raw_block, block_depth in _split_blocks(normalized):
        # Restore fences inside the block text before further processing
        raw_block = _restore_fences(raw_block, vault)

        # Detect verbatim blocks (fenced code that survived as-is)
        is_verbatim = bool(_FENCE_RE.fullmatch(raw_block.strip()))

        if is_verbatim or block_depth is not None:
            # Don't inline-parse verbatim blocks or already-block-tagged content.
            # Inline span depth state is preserved unchanged across these blocks.
            blocks.append(Block(raw=raw_block, depth=block_depth, verbatim=is_verbatim))
        else:
            spans, inline_span_depth = _parse_inline(raw_block, entry_depth=inline_span_depth)
            # Only attach spans if any have a depth tag
            has_tagged = any(s.depth is not None for s in spans)
            blocks.append(
                Block(
                    raw=raw_block,
                    depth=None,
                    spans=spans if has_tagged else [],
                )
            )

    return Document(blocks=blocks, source=source)
