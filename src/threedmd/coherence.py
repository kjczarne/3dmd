"""
threedmd.coherence
~~~~~~~~~~~~~~~~~~

Optional LLM-assisted utilities.  Requires ``pip install threedmd[llm]`` and
``ANTHROPIC_API_KEY`` in the environment.

Functions
---------

``autotag(source)``
    Given plain Markdown text, return a depth-tagged version.

``repair(rendered, original_depth)``
    Given a rendered (depth-filtered) document, smooth transition prose that
    may have become abrupt after dropping higher-depth blocks.

``generate_layer(doc, target_depth)``
    Given a Document, generate missing content for *target_depth* by
    summarising deeper blocks that lack a shallower counterpart.
"""

from __future__ import annotations

import os

from threedmd.parser import Document, parse
from threedmd.renderer import render

_MODEL = "claude-sonnet-4-20250514"
_AUTOTAG_SYSTEM = """\
You are a document depth-tagger for the 3DMarkdown format.

3DMarkdown uses {depth=N} tags to annotate content with an abstraction level:
- depth=1 : one-line summary / abstract bullet
- depth=2 : short summary paragraph
- depth=3 : standard report section
- depth=4 : detailed protocol / methods
- depth=5+: raw notes, edge cases, decision log

Rules:
- Add block-level tags as a line containing only {depth=N} immediately before the paragraph.
- Add inline tags as a suffix {depth=N} immediately after a clause or sentence.
- Content that is structural (headings, figure captions, always-relevant anchors) should NOT be tagged.
- Preserve all original text verbatim — do not rephrase or omit anything.
- Return ONLY the tagged Markdown, no explanation.
"""

_REPAIR_SYSTEM = """\
You are editing a Markdown document that was produced by filtering a detailed
source document down to a shallower abstraction level. Some transitions may
feel abrupt because content has been removed.

Your task:
- Smooth any jarring transitions by lightly rewriting or adding bridging sentences.
- Do NOT add new information — only repair connective tissue.
- Keep the document terse and at the same level of technical detail.
- Return ONLY the repaired Markdown, no explanation.
"""

_GENERATE_SYSTEM = """\
You are generating a shallow summary layer for a 3DMarkdown document.
You will be given the full depth of the document and asked to write
content at depth={target_depth}.

Rules:
- Each generated block must faithfully summarise the deeper content.
- Be terse. depth=1 means a single sentence. depth=2 means ≤ 3 sentences.
- Format output as 3DMarkdown with {depth=N} tags preceding each block.
- Return ONLY the Markdown blocks you are generating, no explanation.
"""


def _client():  # type: ignore[return]
    try:
        import anthropic  # type: ignore[import]
    except ImportError as exc:
        raise ImportError(
            "LLM features require 'anthropic'. Install with: pip install threedmd[llm]"
        ) from exc
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY environment variable is not set.")
    return anthropic.Anthropic(api_key=api_key)


def autotag(source: str) -> str:
    """Auto-tag plain Markdown *source* with depth annotations.

    Parameters
    ----------
    source:
        Raw Markdown text without depth tags.

    Returns
    -------
    str
        Depth-tagged 3DMarkdown text.
    """
    client = _client()
    message = client.messages.create(
        model=_MODEL,
        max_tokens=4096,
        system=_AUTOTAG_SYSTEM,
        messages=[{"role": "user", "content": source}],
    )
    return message.content[0].text  # type: ignore[index]


def repair(rendered: str, context: str = "") -> str:
    """Smooth abrupt transitions in a depth-rendered document.

    Parameters
    ----------
    rendered:
        Markdown text produced by :func:`~threedmd.renderer.render`.
    context:
        Optional: the original full-depth source for reference.

    Returns
    -------
    str
        Repaired Markdown text.
    """
    client = _client()
    user_content = rendered
    if context:
        user_content = (
            f"<original_full_depth>\n{context}\n</original_full_depth>\n\n"
            f"<rendered>\n{rendered}\n</rendered>"
        )
    message = client.messages.create(
        model=_MODEL,
        max_tokens=4096,
        system=_REPAIR_SYSTEM,
        messages=[{"role": "user", "content": user_content}],
    )
    return message.content[0].text  # type: ignore[index]


def generate_layer(doc: Document, target_depth: int) -> str:
    """Generate summary blocks at *target_depth* for sections that lack them.

    Parameters
    ----------
    doc:
        Parsed :class:`~threedmd.parser.Document`.
    target_depth:
        The depth level to generate (must be shallower than the document's
        deepest content).

    Returns
    -------
    str
        3DMarkdown text containing only the newly generated blocks, suitable
        for merging back into the source document.
    """
    client = _client()
    full_text = render(doc, max_depth=max(doc.depth_levels() or [target_depth]))
    system = _GENERATE_SYSTEM.replace("{target_depth}", str(target_depth))
    message = client.messages.create(
        model=_MODEL,
        max_tokens=2048,
        system=system,
        messages=[{"role": "user", "content": full_text}],
    )
    return message.content[0].text  # type: ignore[index]
