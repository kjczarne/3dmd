"""
threedmd.cli
~~~~~~~~~~~~

Command-line interface.

Usage examples::

    threedmd render --depth 2 experiment.3dmd
    threedmd render --depth 2 experiment.3dmd | pandoc -o summary.pdf
    threedmd inspect experiment.3dmd
    threedmd autotag experiment.md -o experiment.3dmd
    threedmd repair --depth 2 experiment.3dmd
    threedmd generate --target-depth 1 experiment.3dmd
"""

from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from threedmd.parser import parse
from threedmd.renderer import depth_summary, render, render_annotated

console = Console(stderr=True)


def _read_source(path: str) -> str:
    p = Path(path)
    if not p.exists():
        console.print(f"[red]Error:[/red] file not found: {path}")
        sys.exit(1)
    return p.read_text(encoding="utf-8")


@click.group()
@click.version_option(package_name="threedmd")
def main() -> None:
    """3DMarkdown — depth-tagged Markdown toolkit."""


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------


@main.command()
@click.argument("source", type=click.Path(exists=True))
@click.option("--depth", "-d", required=True, type=int, help="Maximum depth level to include.")
@click.option(
    "--annotate",
    is_flag=True,
    default=False,
    help="Keep hidden blocks as HTML comments (useful for editor previews).",
)
@click.option("--output", "-o", default="-", help="Output file path (default: stdout).")
def render_cmd(source: str, depth: int, annotate: bool, output: str) -> None:
    """Render SOURCE at a given depth level.

    Untagged content is always included. Blocks tagged {depth=N} are included
    only when N <= DEPTH.

    \b
    Examples:
        threedmd render --depth 2 experiment.3dmd
        threedmd render --depth 2 experiment.3dmd | pandoc -o summary.pdf
        threedmd render --depth 2 -o rendered.md experiment.3dmd
    """
    text = _read_source(source)
    doc = parse(text)

    if annotate:
        result = render_annotated(doc, max_depth=depth)
    else:
        result = render(doc, max_depth=depth)

    if output == "-":
        click.echo(result)
    else:
        Path(output).write_text(result, encoding="utf-8")
        console.print(f"[green]Written:[/green] {output}")


# ---------------------------------------------------------------------------
# inspect
# ---------------------------------------------------------------------------


@main.command()
@click.argument("source", type=click.Path(exists=True))
def inspect(source: str) -> None:
    """Show depth level statistics for SOURCE."""
    text = _read_source(source)
    doc = parse(text)

    levels = doc.depth_levels()
    summary = depth_summary(doc)

    table = Table(title=f"Depth summary: {source}")
    table.add_column("Depth", style="cyan", justify="right")
    table.add_column("Blocks", justify="right")
    table.add_column("Notes")

    for depth_val, count in sorted(summary.items(), key=lambda kv: (kv[0] is None, kv[0])):
        label = str(depth_val) if depth_val is not None else "—"
        note = "always visible (untagged)" if depth_val is None else ""
        table.add_row(label, str(count), note)

    console.print(table)
    console.print(f"\nDepth levels present: {levels}")


# ---------------------------------------------------------------------------
# autotag  (requires threedmd[llm])
# ---------------------------------------------------------------------------


@main.command()
@click.argument("source", type=click.Path(exists=True))
@click.option("--output", "-o", default="-", help="Output file (default: stdout).")
def autotag(source: str, output: str) -> None:
    """Auto-tag a plain Markdown file with depth annotations using an LLM.

    Requires ANTHROPIC_API_KEY and `pip install threedmd[llm]`.
    """
    try:
        from threedmd.coherence import autotag as _autotag
    except ImportError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)

    text = _read_source(source)
    console.print("[dim]Calling LLM for auto-tagging…[/dim]")
    result = _autotag(text)

    if output == "-":
        click.echo(result)
    else:
        Path(output).write_text(result, encoding="utf-8")
        console.print(f"[green]Written:[/green] {output}")


# ---------------------------------------------------------------------------
# repair  (requires threedmd[llm])
# ---------------------------------------------------------------------------


@main.command()
@click.argument("source", type=click.Path(exists=True))
@click.option("--depth", "-d", required=True, type=int, help="Depth level to render and repair.")
@click.option("--output", "-o", default="-", help="Output file (default: stdout).")
def repair(source: str, depth: int, output: str) -> None:
    """Render SOURCE at DEPTH, then run LLM coherence repair on the result.

    Requires ANTHROPIC_API_KEY and `pip install threedmd[llm]`.
    """
    try:
        from threedmd.coherence import repair as _repair
    except ImportError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)

    text = _read_source(source)
    doc = parse(text)
    rendered = render(doc, max_depth=depth)

    console.print(f"[dim]Running coherence repair at depth={depth}…[/dim]")
    result = _repair(rendered, context=text)

    if output == "-":
        click.echo(result)
    else:
        Path(output).write_text(result, encoding="utf-8")
        console.print(f"[green]Written:[/green] {output}")


# ---------------------------------------------------------------------------
# generate  (requires threedmd[llm])
# ---------------------------------------------------------------------------


@main.command()
@click.argument("source", type=click.Path(exists=True))
@click.option(
    "--target-depth",
    "-t",
    required=True,
    type=int,
    help="Depth level to generate content for.",
)
@click.option("--output", "-o", default="-", help="Output file (default: stdout).")
def generate(source: str, target_depth: int, output: str) -> None:
    """Generate missing summary blocks at TARGET_DEPTH.

    Reads SOURCE, finds sections lacking a shallower representation, and
    generates them using an LLM.

    Requires ANTHROPIC_API_KEY and `pip install threedmd[llm]`.
    """
    try:
        from threedmd.coherence import generate_layer
    except ImportError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)

    text = _read_source(source)
    doc = parse(text)

    console.print(f"[dim]Generating depth={target_depth} content…[/dim]")
    result = generate_layer(doc, target_depth=target_depth)

    if output == "-":
        click.echo(result)
    else:
        Path(output).write_text(result, encoding="utf-8")
        console.print(f"[green]Written:[/green] {output}")
