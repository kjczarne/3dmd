"""
threedmd.renderer
~~~~~~~~~~~~~~~~~

Renders a :class:`~threedmd.parser.Document` at a given depth threshold,
producing standard Markdown text.

Rendering semantics
-------------------

``render(doc, max_depth=N)`` includes:

* All blocks with ``depth=None``   (untagged → always visible)
* All blocks with ``depth <= N``
* Within included blocks, all inline spans with ``depth=None`` or
  ``depth <= N``

Empty blocks that result from stripping all inline content are omitted.
"""

from __future__ import annotations

from threedmd.parser import Block, Document, InlineSpan


def _render_block(block: Block, max_depth: int) -> str | None:
    """Return the rendered text for *block* at *max_depth*, or ``None`` if
    the block should be omitted entirely."""

    # Block-level gate
    if block.depth is not None and block.depth > max_depth:
        return None

    # No inline annotations → emit as-is
    if not block.spans:
        return block.raw

    # Rebuild text from inline spans, dropping those above max_depth
    parts: list[str] = []
    for span in block.spans:
        if span.depth is None or span.depth <= max_depth:
            parts.append(span.text)

    text = "".join(parts).strip()
    return text if text else None


def render(doc: Document, max_depth: int) -> str:
    """Render *doc* filtered to *max_depth*.

    Parameters
    ----------
    doc:
        A parsed :class:`~threedmd.parser.Document`.
    max_depth:
        Include content tagged ``depth <= max_depth``.  Untagged content is
        always included.

    Returns
    -------
    str
        Flattened Markdown text suitable for passing to Pandoc or saving as
        ``.md``.
    """
    output_blocks: list[str] = []

    for block in doc.blocks:
        rendered = _render_block(block, max_depth)
        if rendered is not None:
            output_blocks.append(rendered)

    return "\n\n".join(output_blocks)


def render_annotated(doc: Document, max_depth: int, mark_hidden: bool = True) -> str:
    """Like :func:`render` but retains hidden blocks as HTML comments.

    Useful for the Obsidian plugin / web editor to show hidden content in a
    dimmed state without losing it from the document.

    Parameters
    ----------
    mark_hidden:
        When True, out-of-scope blocks are wrapped in
        ``<!-- depth=N hidden -->…<!-- /hidden -->`` so a renderer can style
        them differently.
    """
    output_blocks: list[str] = []

    for block in doc.blocks:
        rendered = _render_block(block, max_depth)
        if rendered is not None:
            output_blocks.append(rendered)
        elif mark_hidden and block.raw.strip():
            depth_label = f"depth={block.depth}" if block.depth else "depth=?"
            output_blocks.append(
                f"<!-- {depth_label} hidden -->\n{block.raw}\n<!-- /hidden -->"
            )

    return "\n\n".join(output_blocks)


def depth_summary(doc: Document) -> dict[int | None, int]:
    """Return a mapping of depth → block count for inspection / CLI output."""
    counts: dict[int | None, int] = {}
    for block in doc.blocks:
        counts[block.depth] = counts.get(block.depth, 0) + 1
    return counts
