"""
threedmd — depth-tagged Markdown library.

Public API::

    from threedmd import parse, render

    doc = parse(source_text)
    output = render(doc, max_depth=2)
"""

from threedmd.parser import Document, parse
from threedmd.renderer import render

__all__ = ["Document", "parse", "render"]
__version__ = "0.1.0"
