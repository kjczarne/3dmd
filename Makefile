.PHONY: install install-llm test lint format \
        plugin plugin-dev plugin-typecheck \
        demo inspect pdf pdf-lua clean

# ── Python ────────────────────────────────────────────────────────────────────

install:
	uv pip install -e ".[dev]"

install-llm:
	uv pip install -e ".[dev,llm]"

test:
	uv run pytest

lint:
	uv run ruff check src/ tests/
	uv run mypy src/

format:
	uv run ruff format src/ tests/

# ── Obsidian plugin ───────────────────────────────────────────────────────────

# Install npm deps (only needed once, or after package.json changes)
obsidian-plugin/node_modules: obsidian-plugin/package.json
	cd obsidian-plugin && npm install

# Build main.js from main.ts
plugin: obsidian-plugin/node_modules
	cd obsidian-plugin && npm run build

# Watch mode — rebuilds on every save
plugin-dev: obsidian-plugin/node_modules
	cd obsidian-plugin && npm run dev

# Type-check without emitting
plugin-typecheck: obsidian-plugin/node_modules
	cd obsidian-plugin && npm run typecheck

# ── Demo / docs ───────────────────────────────────────────────────────────────

# Render the stellar survey example at each depth level
demo:
	@echo "=== depth=1 (one-liner) ==="
	@uv run threedmd render --depth 1 examples/stellar_survey.3dmd
	@echo "\n=== depth=2 (summary) ==="
	@uv run threedmd render --depth 2 examples/stellar_survey.3dmd
	@echo "\n=== depth=3 (full report) ==="
	@uv run threedmd render --depth 3 examples/stellar_survey.3dmd

# Inspect depth levels present in an example
inspect:
	uv run threedmd inspect examples/stellar_survey.3dmd

# Pandoc PDF at depth=2 (requires pandoc)
pdf:
	uv run threedmd render --depth 2 examples/stellar_survey.3dmd | \
		pandoc -f markdown -o /tmp/stellar_summary.pdf && \
		echo "Written: /tmp/stellar_summary.pdf"

# Pandoc via Lua filter directly
pdf-lua:
	pandoc --lua-filter pandoc-filter/threedmd.lua \
		-M depth=2 \
		examples/stellar_survey.3dmd \
		-o /tmp/stellar_summary_lua.pdf && \
		echo "Written: /tmp/stellar_summary_lua.pdf"

# ── Clean ─────────────────────────────────────────────────────────────────────

clean:
	rm -rf .pytest_cache __pycache__ src/threedmd/__pycache__ \
		src/*.egg-info .mypy_cache .ruff_cache
	rm -f obsidian-plugin/main.js
