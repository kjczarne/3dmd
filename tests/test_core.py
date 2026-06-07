"""
Tests for threedmd parser and renderer.
"""

from __future__ import annotations

import pytest

from threedmd.parser import parse
from threedmd.renderer import depth_summary, render


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------


class TestParser:
    def test_untagged_document(self):
        source = "# Heading\n\nSome paragraph.\n\nAnother paragraph."
        doc = parse(source)
        assert all(b.depth is None for b in doc.blocks)

    def test_block_depth_tag(self):
        source = "Untagged.\n\n{depth=2}\nDeep content."
        doc = parse(source)
        depths = [b.depth for b in doc.blocks]
        assert None in depths
        assert 2 in depths

    def test_multiple_depth_levels(self):
        source = (
            "Always visible.\n\n"
            "{depth=1}\nSummary.\n\n"
            "{depth=2}\nDetail.\n\n"
            "{depth=3}\nFull detail."
        )
        doc = parse(source)
        assert doc.depth_levels() == [1, 2, 3]

    def test_inline_depth_tag(self):
        source = "We found three hits{depth=1}, all stable{depth=2}."
        doc = parse(source)
        # Should have spans
        blocks_with_spans = [b for b in doc.blocks if b.spans]
        assert len(blocks_with_spans) == 1
        spans = blocks_with_spans[0].spans
        depths = [s.depth for s in spans]
        assert 1 in depths
        assert 2 in depths

    def test_fenced_code_block_untouched(self):
        source = "```python\nx = {depth=3}\n```"
        doc = parse(source)
        # The fenced block should be verbatim and depth=None
        code_blocks = [b for b in doc.blocks if b.verbatim]
        assert len(code_blocks) == 1
        assert "{depth=3}" in code_blocks[0].raw

    def test_depth_levels_empty(self):
        doc = parse("No tags here.")
        assert doc.depth_levels() == []


# ---------------------------------------------------------------------------
# Renderer tests
# ---------------------------------------------------------------------------


class TestRenderer:
    def test_untagged_always_visible(self):
        source = "Always here."
        doc = parse(source)
        for d in [1, 2, 5]:
            assert "Always here" in render(doc, max_depth=d)

    def test_block_excluded_above_threshold(self):
        source = "Untagged.\n\n{depth=3}\nDeep only."
        doc = parse(source)
        assert "Deep only" not in render(doc, max_depth=1)
        assert "Deep only" not in render(doc, max_depth=2)
        assert "Deep only" in render(doc, max_depth=3)
        assert "Deep only" in render(doc, max_depth=4)

    def test_block_included_at_exact_threshold(self):
        source = "{depth=2}\nExact threshold content."
        doc = parse(source)
        assert "Exact threshold" in render(doc, max_depth=2)

    def test_untagged_survives_all_depths(self):
        source = "Base content.\n\n{depth=5}\nDeep content."
        doc = parse(source)
        for d in [1, 2, 3, 4, 5]:
            assert "Base content" in render(doc, max_depth=d)

    def test_inline_depth_filtering(self):
        source = "We found hits{depth=1}, with CDR3 < 15 aa{depth=2}."
        doc = parse(source)
        result_d1 = render(doc, max_depth=1)
        result_d2 = render(doc, max_depth=2)
        assert "hits" in result_d1
        assert "CDR3" not in result_d1
        assert "CDR3" in result_d2

    def test_empty_result_block_omitted(self):
        source = "{depth=3}\nOnly deep content."
        doc = parse(source)
        result = render(doc, max_depth=1)
        assert result.strip() == ""

    def test_multiple_blocks_ordering(self):
        source = (
            "# Title\n\n"
            "{depth=1}\nSummary.\n\n"
            "{depth=2}\nDetail.\n\n"
            "{depth=3}\nFull.\n"
        )
        doc = parse(source)
        result = render(doc, max_depth=2)
        assert "Summary" in result
        assert "Detail" in result
        assert "Full" not in result
        # Title heading should always appear
        assert "# Title" in result

    def test_fenced_code_block_preserved(self):
        source = "Intro.\n\n```python\ndef foo():\n    pass\n```\n\n{depth=3}\nDeep."
        doc = parse(source)
        result = render(doc, max_depth=1)
        assert "def foo" in result
        assert "Deep" not in result


# ---------------------------------------------------------------------------
# Shortform tags  {dN}
# ---------------------------------------------------------------------------


class TestShortformTags:
    def test_block_shortform_parsed(self):
        source = "Untagged.\n\n{d2}\nDeep content."
        doc = parse(source)
        assert 2 in [b.depth for b in doc.blocks]

    def test_block_shortform_renders(self):
        source = "Untagged.\n\n{d3}\nDeep only."
        doc = parse(source)
        assert "Deep only" not in render(doc, max_depth=1)
        assert "Deep only" in render(doc, max_depth=3)

    def test_inline_shortform(self):
        source = "We found hits{d1}, with CDR3 < 15 aa{d2}."
        doc = parse(source)
        result_d1 = render(doc, max_depth=1)
        result_d2 = render(doc, max_depth=2)
        assert "hits" in result_d1
        assert "CDR3" not in result_d1
        assert "CDR3" in result_d2

    def test_longform_and_shortform_coexist(self):
        source = "Untagged.\n\n{depth=2}\nLong form.\n\n{d3}\nShort form."
        doc = parse(source)
        assert doc.depth_levels() == [2, 3]


# ---------------------------------------------------------------------------
# Span (multi-block) depth tags  {start depth=N} / {sdN} … {end} / {e}
# ---------------------------------------------------------------------------


class TestSpanBlocks:
    def test_span_longform_basic(self):
        source = "Untagged.\n\n{start depth=2}\nPara A.\n\nPara B.\n{end}"
        doc = parse(source)
        result_d1 = render(doc, max_depth=1)
        result_d2 = render(doc, max_depth=2)
        assert "Para A." not in result_d1
        assert "Para B." not in result_d1
        assert "Para A." in result_d2
        assert "Para B." in result_d2

    def test_span_shortform_basic(self):
        source = "Untagged.\n\n{sd2}\nPara A.\n\nPara B.\n{e}"
        doc = parse(source)
        result_d1 = render(doc, max_depth=1)
        result_d2 = render(doc, max_depth=2)
        assert "Para A." not in result_d1
        assert "Para B." not in result_d1
        assert "Para A." in result_d2
        assert "Para B." in result_d2

    def test_span_untagged_always_visible(self):
        source = "Always here.\n\n{sd3}\nDeep A.\n\nDeep B.\n{e}\n\nAlso always here."
        doc = parse(source)
        result = render(doc, max_depth=1)
        assert "Always here." in result
        assert "Also always here." in result
        assert "Deep A." not in result

    def test_span_mixed_with_single_block_tag(self):
        source = (
            "Untagged.\n\n"
            "{start depth=2}\n"
            "Span A.\n\n"
            "Span B.\n"
            "{end}\n\n"
            "{depth=3}\n"
            "Single deep."
        )
        doc = parse(source)
        result_d2 = render(doc, max_depth=2)
        assert "Span A." in result_d2
        assert "Span B." in result_d2
        assert "Single deep." not in result_d2
        assert "Single deep." in render(doc, max_depth=3)

    def test_span_depth_levels(self):
        source = "{sd1}\nA.\n\nB.\n{e}\n\n{sd3}\nC.\n{e}"
        doc = parse(source)
        assert 1 in doc.depth_levels()
        assert 3 in doc.depth_levels()

    def test_span_no_end_runs_to_eof(self):
        source = "Before.\n\n{sd2}\nInside.\n\nAlso inside."
        doc = parse(source)
        assert "Inside." not in render(doc, max_depth=1)
        assert "Also inside." not in render(doc, max_depth=1)
        assert "Inside." in render(doc, max_depth=2)

    def test_fenced_code_inside_span(self):
        source = "Before.\n\n{sd2}\nText.\n\n```python\nx = 1\n```\n\nMore text.\n{e}"
        doc = parse(source)
        result_d1 = render(doc, max_depth=1)
        result_d2 = render(doc, max_depth=2)
        assert "x = 1" not in result_d1
        assert "x = 1" in result_d2


# ---------------------------------------------------------------------------
# Depth summary
# ---------------------------------------------------------------------------


class TestInlineSpans:
    """Inline span depth context: {sdN}/{e} and {start depth=N}/{end} in text."""

    def test_single_block_inline_span_longform(self):
        source = "Plain. {start depth=2}Detail here.{end} Plain again."
        doc = parse(source)
        result_d1 = render(doc, max_depth=1)
        result_d2 = render(doc, max_depth=2)
        assert "Detail here." not in result_d1
        assert "Plain." in result_d1
        assert "Plain again." in result_d1
        assert "Detail here." in result_d2

    def test_single_block_inline_span_shortform(self):
        source = "Plain. {sd2}Detail here.{e} Plain again."
        doc = parse(source)
        result_d1 = render(doc, max_depth=1)
        assert "Detail here." not in result_d1
        assert "Plain." in result_d1
        assert "Detail here." in render(doc, max_depth=2)

    def test_inline_span_cross_block(self):
        # Span opened mid-sentence in block 1, closed in block 2.
        source = "Plain start. {sd2}Carried\n\nacross blocks.{e} Plain end."
        doc = parse(source)
        result_d1 = render(doc, max_depth=1)
        assert "Carried" not in result_d1
        assert "across blocks." not in result_d1
        assert "Plain start." in result_d1
        assert "Plain end." in result_d1
        result_d2 = render(doc, max_depth=2)
        assert "Carried" in result_d2
        assert "across blocks." in result_d2

    def test_inline_span_nested(self):
        source = "{sd2}outer {sd3}inner{e} back to 2{e} untagged"
        doc = parse(source)
        result_d1 = render(doc, max_depth=1)
        result_d2 = render(doc, max_depth=2)
        result_d3 = render(doc, max_depth=3)
        assert "inner" not in result_d1
        assert "outer" not in result_d1
        assert "untagged" in result_d1
        assert "outer" in result_d2
        assert "back to 2" in result_d2
        assert "inner" not in result_d2
        assert "inner" in result_d3

    def test_inline_span_no_close_runs_to_eof(self):
        source = "Before. {sd2}deep stuff"
        doc = parse(source)
        assert "deep stuff" not in render(doc, max_depth=1)
        assert "Before." in render(doc, max_depth=1)
        assert "deep stuff" in render(doc, max_depth=2)

    def test_inline_span_coexists_with_trailing_tag(self):
        # Trailing tag depth takes precedence for its own segment.
        source = "A{d1}. {sd2}B{d3} rest.{e}"
        doc = parse(source)
        result_d1 = render(doc, max_depth=1)
        result_d2 = render(doc, max_depth=2)
        result_d3 = render(doc, max_depth=3)
        assert "A" in result_d1
        assert "." in result_d1
        assert "B" not in result_d1
        assert "rest." not in result_d1
        assert "rest." in result_d2
        assert "B" not in result_d2
        assert "B" in result_d3

    def test_inline_span_depth_in_depth_levels(self):
        source = "Text. {sd3}Deep.{e} More."
        doc = parse(source)
        assert 3 in doc.depth_levels()

    def test_inline_span_standalone_e_closes_block_span(self):
        # {e} on a standalone line should close the block span (not be passed
        # to _parse_inline), so paragraphs after it are untagged.
        source = "{sd2}\n\nBlock span content.\n\n{e}\n\nUntagged after."
        doc = parse(source)
        assert "Block span content." not in render(doc, max_depth=1)
        assert "Untagged after." in render(doc, max_depth=1)


class TestDepthSummary:
    def test_summary_counts(self):
        source = (
            "Untagged.\n\n"
            "{depth=1}\nA.\n\n"
            "{depth=1}\nB.\n\n"
            "{depth=2}\nC."
        )
        doc = parse(source)
        summary = depth_summary(doc)
        assert summary[1] == 2
        assert summary[2] == 1
        assert summary[None] >= 1
