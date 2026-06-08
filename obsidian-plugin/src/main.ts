/**
 * 3DMarkdown Obsidian Plugin
 *
 * Depth tag syntax supported:
 *   Block:      standalone line  {depth=N}  or  {dN}
 *   Span start: standalone line  {start depth=N}  or  {sdN}
 *   Span end:   standalone line  {end}  or  {e}
 *   Inline:     text{depth=N}  or  text{dN}
 *
 * Reading mode  — post-processor uses getSectionInfo() to look up the source
 *                 and determine each block's effective depth independently,
 *                 avoiding the cross-call state problem.
 * Live preview  — CM6 ViewPlugin applies line decorations for block-level depth
 *                 and mark decorations for inline spans.
 */

import {
  App,
  MarkdownPostProcessorContext,
  MarkdownView,
  MarkdownRenderer,
  Modal,
  Notice,
  Plugin,
  PluginSettingTab,
  Setting,
  WorkspaceLeaf,
} from "obsidian";
import { Decoration, DecorationSet, EditorView, ViewPlugin, ViewUpdate } from "@codemirror/view";
import { RangeSetBuilder, StateEffect } from "@codemirror/state";

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------

interface ThreeDMDSettings {
  defaultDepth: number;
  showDepthInStatusBar: boolean;
}

const DEFAULT_SETTINGS: ThreeDMDSettings = {
  defaultDepth: 99,
  showDepthInStatusBar: true,
};

// ---------------------------------------------------------------------------
// Regex
// ---------------------------------------------------------------------------

const BLOCK_TAG_RE  = /^\s*\{d(?:epth=)?(\d+)\}\s*$/;
const SPAN_START_RE = /^\s*\{(?:start\s+depth=|sd)(\d+)\}\s*$/;
const SPAN_END_RE   = /^\s*\{(?:end|e)\}\s*$/;
const INLINE_TAG_RE = /\{d(?:epth=)?(\d+)\}/g;

function parseDepthTag(line: string): number | null {
  const m = line.match(BLOCK_TAG_RE);
  return m ? parseInt(m[1], 10) : null;
}

function parseSpanStart(line: string): number | null {
  const m = line.match(SPAN_START_RE);
  return m ? parseInt(m[1], 10) : null;
}

function isSpanEnd(line: string): boolean {
  return SPAN_END_RE.test(line);
}

// ---------------------------------------------------------------------------
// CM6: StateEffect used to push a depth change into active editor views
// ---------------------------------------------------------------------------

const threedmdDepthEffect = StateEffect.define<number>();

// ---------------------------------------------------------------------------
// CM6 ViewPlugin — live preview decoration (blocks + inline spans)
// ---------------------------------------------------------------------------

function buildLivePreviewPlugin(getDepth: () => number) {
  return ViewPlugin.fromClass(
    class {
      decorations: DecorationSet;

      constructor(view: EditorView) {
        this.decorations = this.build(view);
      }

      update(update: ViewUpdate) {
        if (
          update.docChanged ||
          update.viewportChanged ||
          update.transactions.some(t => t.effects.some(e => e.is(threedmdDepthEffect)))
        ) {
          this.decorations = this.build(update.view);
        }
      }

      build(view: EditorView): DecorationSet {
        const maxDepth = getDepth();
        const doc = view.state.doc;
        const builder = new RangeSetBuilder<Decoration>();

        let pendingDepth: number | null = null;
        let spanDepth: number | null = null;
        let pendingHasContent = false;
        let inFence = false;
        let fenceChar = "";
        const inlineSpanStack: number[] = [];

        for (let n = 1; n <= doc.lines; n++) {
          const line = doc.line(n);
          const raw  = line.text;
          const text = raw.trim();

          // ── Fence tracking: skip content inside code blocks ───────────────
          if (!inFence) {
            const fm = text.match(/^(`{3,}|~{3,})/);
            if (fm) { inFence = true; fenceChar = fm[1][0]; continue; }
          } else {
            const fm = text.match(/^(`{3,}|~{3,})$/);
            if (fm && fm[1][0] === fenceChar) inFence = false;
            continue;
          }

          // ── Blank line ─────────────────────────────────────────────────────
          if (text === "") {
            if (pendingHasContent) { pendingDepth = null; pendingHasContent = false; }
            continue;
          }

          // ── Span start/end ─────────────────────────────────────────────────
          const ss = parseSpanStart(text);
          if (ss !== null) {
            spanDepth = ss;
            builder.add(line.from, line.from, Decoration.line({ class: "threedmd-cm-tag" }));
            continue;
          }
          if (isSpanEnd(text)) {
            spanDepth = null;
            builder.add(line.from, line.from, Decoration.line({ class: "threedmd-cm-tag" }));
            continue;
          }

          // ── Single-block depth tag ─────────────────────────────────────────
          const bd = parseDepthTag(text);
          if (bd !== null) {
            pendingDepth = bd;
            pendingHasContent = false;
            builder.add(line.from, line.from, Decoration.line({ class: "threedmd-cm-tag" }));
            continue;
          }

          // ── Content line ───────────────────────────────────────────────────
          const eff = pendingDepth ?? spanDepth;
          if (eff !== null) pendingHasContent = true;

          // A line is also hidden when it falls entirely within a deep inline span
          // that was opened on a previous line (entrySpanDepth > maxDepth).
          const entrySpanDepth =
            inlineSpanStack.length > 0 ? inlineSpanStack[inlineSpanStack.length - 1] : null;
          const blockHidden  = eff !== null && eff > maxDepth;
          const inlineHidden = eff === null && entrySpanDepth !== null && entrySpanDepth > maxDepth;

          if (blockHidden || inlineHidden) {
            // Dim the whole line; still scan for span markers to keep stack in sync.
            scanInlineStack(raw, inlineSpanStack);
            builder.add(line.from, line.from, Decoration.line({ class: "threedmd-cm-hidden" }));
          } else {
            // Line is visible — decorate inline markers and update stack.
            addInlineMarks(raw, line.from, maxDepth, inlineSpanStack, builder);
          }
        }

        return builder.finish();
      }
    },
    { decorations: v => v.decorations },
  );
}

// Combined regex for all inline markers (trailing depth tag, span open, span close).
// Named groups: `open` for span-open depth, `trail` for trailing-tag depth.
// Span-close has neither group.
// {dN} won't accidentally match {sdN} because {sdN} starts with "{s", not "{d".
const CM6_INLINE_RE =
  /\{(?:start\s+depth=|sd)(?<open>\d+)\}|\{(?:end|e)\}|\{d(?:epth=)?(?<trail>\d+)\}/g;

/**
 * Add CM6 mark decorations for all inline depth markers within a line.
 * Also updates `inlineSpanStack` (mutated in place) for cross-line state.
 *
 * Marker semantics:
 *   {dN} / {depth=N}         — trailing tag: preceding text gets depth N
 *   {sdN} / {start depth=N}  — inline span open: push N; text before gets current span depth
 *   {e} / {end}              — inline span close: text before gets current span depth; pop
 *
 * Text at depth > maxDepth gets threedmd-cm-inline-hidden.
 * All markers get threedmd-cm-tag-inline (faded metadata style).
 * Text after the last marker is decorated if inside an open deep span.
 */
function addInlineMarks(
  lineText: string,
  lineFrom: number,
  maxDepth: number,
  inlineSpanStack: number[],
  builder: RangeSetBuilder<Decoration>,
): void {
  CM6_INLINE_RE.lastIndex = 0;
  let match: RegExpExecArray | null;
  let lastPos = 0;

  const currentSpanDepth = (): number | null =>
    inlineSpanStack.length > 0 ? inlineSpanStack[inlineSpanStack.length - 1] : null;

  while ((match = CM6_INLINE_RE.exec(lineText)) !== null) {
    const matchStart = match.index;
    const matchEnd   = matchStart + match[0].length;

    if (match.groups?.open !== undefined) {
      // Inline span open: text from lastPos to matchStart at current span depth
      const spanD = currentSpanDepth();
      if (spanD !== null && spanD > maxDepth && lastPos < matchStart) {
        builder.add(
          lineFrom + lastPos, lineFrom + matchStart,
          Decoration.mark({ class: "threedmd-cm-inline-hidden" }),
        );
      }
      builder.add(lineFrom + matchStart, lineFrom + matchEnd,
        Decoration.mark({ class: "threedmd-cm-tag-inline" }));
      inlineSpanStack.push(parseInt(match.groups.open, 10));

    } else if (match.groups?.trail !== undefined) {
      // Trailing depth tag: preceding text gets the tag's explicit depth
      const tagDepth = parseInt(match.groups.trail, 10);
      if (tagDepth > maxDepth && lastPos < matchStart) {
        builder.add(
          lineFrom + lastPos, lineFrom + matchStart,
          Decoration.mark({ class: "threedmd-cm-inline-hidden" }),
        );
      }
      builder.add(lineFrom + matchStart, lineFrom + matchEnd,
        Decoration.mark({ class: "threedmd-cm-tag-inline" }));

    } else {
      // Inline span close: preceding text at current span depth
      const spanD = currentSpanDepth();
      if (spanD !== null && spanD > maxDepth && lastPos < matchStart) {
        builder.add(
          lineFrom + lastPos, lineFrom + matchStart,
          Decoration.mark({ class: "threedmd-cm-inline-hidden" }),
        );
      }
      builder.add(lineFrom + matchStart, lineFrom + matchEnd,
        Decoration.mark({ class: "threedmd-cm-tag-inline" }));
      if (inlineSpanStack.length > 0) inlineSpanStack.pop();
    }

    lastPos = matchEnd;
  }

  // Remaining text at end of line: at current span depth
  if (lastPos < lineText.length) {
    const spanD = currentSpanDepth();
    if (spanD !== null && spanD > maxDepth) {
      builder.add(
        lineFrom + lastPos, lineFrom + lineText.length,
        Decoration.mark({ class: "threedmd-cm-inline-hidden" }),
      );
    }
  }
}

function filterInlineForExport(line: string, maxDepth: number): string {
  INLINE_MARKER_RE.lastIndex = 0;
  if (!INLINE_MARKER_RE.test(line)) return line;
  INLINE_MARKER_RE.lastIndex = 0;

  const stack: number[] = [];
  const top = (): number | null => (stack.length ? stack[stack.length - 1] : null);
  let out = "", last = 0;
  let m: RegExpExecArray | null;
  while ((m = INLINE_MARKER_RE.exec(line)) !== null) {
    const before = line.slice(last, m.index);
    const openD  = m[1] !== undefined ? parseInt(m[1], 10) : null;
    const trailD = m[2] !== undefined ? parseInt(m[2], 10) : null;
    if (trailD !== null) {
      if (trailD <= maxDepth) out += before;            // drop text behind an out-of-depth {dN}
    } else {
      const d = top();
      if (!(d !== null && d > maxDepth)) out += before;
      if (openD !== null) stack.push(openD);
      else if (stack.length) stack.pop();
    }
    last = m.index + m[0].length;
  }
  const d = top();
  if (!(d !== null && d > maxDepth)) out += line.slice(last);
  return out;
}

function filterMarkdownByDepth(source: string, maxDepth: number): string {
  const lines = source.split("\n");
  const out: string[] = [];
  let pendingDepth: number | null = null;
  let spanDepth: number | null = null;
  let pendingHasContent = false;
  let inFence = false, fenceChar = "", dropFence = false;

  for (const raw of lines) {
    const text = raw.trim();

    if (inFence) {
      const fm = text.match(/^(`{3,}|~{3,})$/);
      if (!dropFence) out.push(raw);
      if (fm && fm[1][0] === fenceChar) { inFence = false; dropFence = false; }
      continue;
    }
    const open = text.match(/^(`{3,}|~{3,})/);
    if (open) {
      const depth = pendingDepth ?? spanDepth;
      if (pendingDepth !== null) pendingHasContent = true;
      inFence = true; fenceChar = open[1][0];
      dropFence = depth !== null && depth > maxDepth;
      if (!dropFence) out.push(raw);
      continue;
    }
    if (text === "") {
      if (pendingHasContent) { pendingDepth = null; pendingHasContent = false; }
      out.push(raw);
      continue;
    }
    const ss = parseSpanStart(text); if (ss !== null) { spanDepth = ss; continue; }
    if (isSpanEnd(text))            { spanDepth = null; continue; }
    const bd = parseDepthTag(text); if (bd !== null) { pendingDepth = bd; pendingHasContent = false; continue; }

    const depth = pendingDepth ?? spanDepth;
    if (pendingDepth !== null) pendingHasContent = true;
    if (depth !== null && depth > maxDepth) continue;     // drop out-of-depth line
    out.push(filterInlineForExport(raw, maxDepth));
  }
  return out.join("\n").replace(/\n{3,}/g, "\n\n");
}

/**
 * Scan a line's text for inline span markers ({sdN}/{e}) and update
 * `inlineSpanStack` accordingly.  Used for lines that are already fully
 * hidden by block depth so we don't add redundant decorations, but must
 * still maintain correct cross-line inline span state.
 */
function scanInlineStack(lineText: string, inlineSpanStack: number[]): void {
  CM6_INLINE_RE.lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = CM6_INLINE_RE.exec(lineText)) !== null) {
    if (match.groups?.open !== undefined) {
      inlineSpanStack.push(parseInt(match.groups.open, 10));
    } else if (match.groups?.trail === undefined) {
      // Close marker (neither open nor trail group)
      if (inlineSpanStack.length > 0) inlineSpanStack.pop();
    }
    // Trailing tags don't affect the span stack
  }
}

// ---------------------------------------------------------------------------
// Reading mode helpers
// ---------------------------------------------------------------------------

/**
 * Establish depth state entering a section by replaying source up to lineStart,
 * then scan the section [lineStart, lineEnd] to find:
 *   hide  — true when the section is markers/blank only (no rendered content)
 *   depth — shallowest effective depth among content lines
 *           (null = at least one always-visible line, so the block must show).
 * Inline spans deeper than this block depth are handled by processInlineMarkers.
 */
function effectiveSectionDepth(
  lines: string[],
  lineStart: number,
  lineEnd: number,
): { hide: boolean; depth: number | null; spanDepthAfter: number | null; pendingDepthAfter: number | null } {
  let pendingDepth: number | null = null;
  let spanDepth: number | null = null;
  let pendingHasContent = false;
  let inFence = false;
  let fenceChar = "";

  const advance = (text: string): { content: boolean; depth: number | null } => {
    if (inFence) {
      const fm = text.match(/^(`{3,}|~{3,})$/);
      const depth = pendingDepth ?? spanDepth;
      if (fm && fm[1][0] === fenceChar) inFence = false;
      return { content: true, depth };
    }
    const open = text.match(/^(`{3,}|~{3,})/);
    if (open) {
      const depth = pendingDepth ?? spanDepth;
      if (pendingDepth !== null) pendingHasContent = true;
      inFence = true; fenceChar = open[1][0];
      return { content: true, depth };
    }
    if (text === "") {
      if (pendingHasContent) { pendingDepth = null; pendingHasContent = false; }
      return { content: false, depth: null };
    }
    const ss = parseSpanStart(text);
    if (ss !== null) { spanDepth = ss; return { content: false, depth: null }; }
    if (isSpanEnd(text)) { spanDepth = null; return { content: false, depth: null }; }
    const bd = parseDepthTag(text);
    if (bd !== null) { pendingDepth = bd; pendingHasContent = false; return { content: false, depth: null }; }
    const depth = pendingDepth ?? spanDepth;
    if (pendingDepth !== null) pendingHasContent = true;
    return { content: true, depth };
  };

  for (let i = 0; i < lineStart && i < lines.length; i++) advance(lines[i].trim());

  let hasContent = false, sawVisible = false;
  let minDepth: number | null = null;
  for (let i = lineStart; i <= lineEnd && i < lines.length; i++) {
    const { content, depth } = advance(lines[i].trim());
    if (!content) continue;
    hasContent = true;
    if (depth === null) sawVisible = true;
    else minDepth = minDepth === null ? depth : Math.min(minDepth, depth);
  }

  // spanDepth and pendingDepth now reflect state immediately after lineEnd —
  // returned so the caller can keep sequential fallback state in sync.
  const spanDepthAfter    = spanDepth;
  const pendingDepthAfter = pendingHasContent ? pendingDepth : null;

  if (!hasContent) return { hide: true, depth: null, spanDepthAfter, pendingDepthAfter };
  return { hide: false, depth: sawVisible ? null : minDepth, spanDepthAfter, pendingDepthAfter };
}

const INLINE_MARKER_RE =
  /\{(?:start\s+depth=|sd)(\d+)\}|\{(?:end|e)\}|\{d(?:epth=)?(\d+)\}/g;

function processInlineMarkers(el: HTMLElement, maxDepth: number): void {
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  const targets: Text[] = [];
  let node: Text | null;
  while ((node = walker.nextNode() as Text | null)) {
    INLINE_MARKER_RE.lastIndex = 0;
    if (INLINE_MARKER_RE.test(node.textContent ?? "")) targets.push(node);
  }

  const stack: number[] = [];
  const top = (): number | null => (stack.length ? stack[stack.length - 1] : null);

  const emit = (frag: DocumentFragment, txt: string, depth: number | null): void => {
    if (!txt) return;
    if (depth === null) { frag.appendChild(document.createTextNode(txt)); return; }
    const span = document.createElement("span");
    span.textContent = txt;
    span.dataset.threedmdDepth = String(depth);
    if (depth > maxDepth) span.classList.add("threedmd-inline-hidden");
    frag.appendChild(span);
  };

  for (const t of targets) {
    const text = t.textContent ?? "";
    const parent = t.parentNode;
    if (!parent) continue;
    const frag = document.createDocumentFragment();
    let last = 0;
    INLINE_MARKER_RE.lastIndex = 0;
    let m: RegExpExecArray | null;
    while ((m = INLINE_MARKER_RE.exec(text)) !== null) {
      const before = text.slice(last, m.index);
      const openD  = m[1] !== undefined ? parseInt(m[1], 10) : null;  // {sdN}
      const trailD = m[2] !== undefined ? parseInt(m[2], 10) : null;  // {dN}
      if (trailD !== null) {
        emit(frag, before, trailD);
      } else {
        emit(frag, before, top());
        if (openD !== null) stack.push(openD);
        else if (stack.length) stack.pop();           // {e}
      }
      last = m.index + m[0].length;                    // token consumed
    }
    emit(frag, text.slice(last), top());
    parent.replaceChild(frag, t);
  }
}

// ---------------------------------------------------------------------------
// Set-depth modal
// ---------------------------------------------------------------------------

class SetDepthModal extends Modal {
  private plugin: ThreeDMDPlugin;

  constructor(app: App, plugin: ThreeDMDPlugin) {
    super(app);
    this.plugin = plugin;
  }

  onOpen(): void {
    const { contentEl } = this;
    contentEl.empty();
    contentEl.createEl("h3", { text: "Set depth level" });

    // Quick-pick buttons 1–5 + all
    const quickPick = contentEl.createDiv({ cls: "threedmd-quick-pick" });
    for (let d = 1; d <= 5; d++) {
      const btn = quickPick.createEl("button", { text: String(d) });
      if (d === this.plugin.currentDepth) btn.addClass("mod-cta");
      btn.addEventListener("click", () => { this.plugin.setDepth(d); this.close(); });
    }
    const allBtn = quickPick.createEl("button", { text: "all" });
    if (this.plugin.currentDepth >= 99) allBtn.addClass("mod-cta");
    allBtn.addEventListener("click", () => { this.plugin.setDepth(99); this.close(); });

    // Free-text number input
    const inputRow = contentEl.createDiv({ cls: "threedmd-input-row" });
    inputRow.createEl("label", { text: "Or enter a value: " });
    const input = inputRow.createEl("input", {
      attr: { type: "number", min: "1", placeholder: "depth…" },
    }) as HTMLInputElement;
    if (this.plugin.currentDepth < 99) input.value = String(this.plugin.currentDepth);

    const apply = (): void => {
      const val = parseInt(input.value, 10);
      this.plugin.setDepth(isNaN(val) || val < 1 ? 99 : val);
      this.close();
    };
    input.addEventListener("keydown", (e: KeyboardEvent) => { if (e.key === "Enter") apply(); });

    const footer = contentEl.createDiv({ cls: "threedmd-modal-footer" });
    footer.createEl("button", { text: "Set", cls: "mod-cta" }).addEventListener("click", apply);

    setTimeout(() => input.focus(), 0);
  }

  onClose(): void {
    this.contentEl.empty();
  }
}

// ---------------------------------------------------------------------------
// Plugin
// ---------------------------------------------------------------------------

export default class ThreeDMDPlugin extends Plugin {
  settings!: ThreeDMDSettings;
  currentDepth!: number;
  statusBarEl: HTMLElement | null = null;

  // Sequential fallback state for elements where getSectionInfo() returns null
  // (block math, embeds, and other special Obsidian elements).  Updated every
  // time a section *with* valid info is processed, so the state stays in sync
  // with document order and is available for the next null-returning element.
  private seqSpan    = new Map<string, number | null>();
  private seqPending = new Map<string, number | null>();


  async onload(): Promise<void> {
    await this.loadSettings();
    this.currentDepth = this.settings.defaultDepth;

    if (this.settings.showDepthInStatusBar) {
      this.statusBarEl = this.addStatusBarItem();
      this.updateStatusBar();
    }

    this.addCommand({
      id: "depth-increase",
      name: "Increase depth level",
      hotkeys: [{ modifiers: ["Ctrl", "Shift"], key: "ArrowRight" }],
      callback: () => this.setDepth(this.currentDepth + 1),
    });

    this.addCommand({
      id: "depth-decrease",
      name: "Decrease depth level",
      hotkeys: [{ modifiers: ["Ctrl", "Shift"], key: "ArrowLeft" }],
      callback: () => { if (this.currentDepth > 1) this.setDepth(this.currentDepth - 1); },
    });

    this.addCommand({
      id: "depth-set",
      name: "Set depth level…",
      callback: () => new SetDepthModal(this.app, this).open(),
    });

    this.addCommand({
      id: "depth-reset",
      name: "Reset depth (show all)",
      callback: () => this.setDepth(99),
    });

    // Currently not functional, requires more work:
    // this.addCommand({
    //   id: "export-pdf",
    //   name: "Export to PDF at current depth",
    //   callback: () => this.exportToPdf(),
    // });

    // Live preview
    this.registerEditorExtension(buildLivePreviewPlugin(() => this.currentDepth));

    // Reading mode
    this.registerMarkdownPostProcessor(this.postProcess.bind(this));

    this.addSettingTab(new ThreeDMDSettingTab(this.app, this));
    console.log("3DMarkdown plugin loaded.");
  }

  onunload(): void {
    console.log("3DMarkdown plugin unloaded.");
  }

  private postProcess(el: HTMLElement, ctx: MarkdownPostProcessorContext): void {
    const id   = ctx.docId;
    const info = ctx.getSectionInfo(el);

    if (info) {
      // ── Primary path: section info available ─────────────────────────────
      const lines = info.text.split("\n");
      const { hide, depth, spanDepthAfter, pendingDepthAfter } =
        effectiveSectionDepth(lines, info.lineStart, info.lineEnd);

      // Keep sequential state in sync so the fallback path stays accurate
      // for any null-returning elements that follow this section in the DOM.
      this.seqSpan.set(id, spanDepthAfter);
      this.seqPending.set(id, pendingDepthAfter);

      if (hide) {
        el.classList.add("threedmd-marker");
        el.style.display = "none";
        return;
      }

      if (depth !== null) {
        el.dataset.threedmdDepth = String(depth);
        el.classList.toggle("threedmd-hidden",  depth > this.currentDepth);
        el.classList.toggle("threedmd-visible", depth <= this.currentDepth);
      } else {
        delete el.dataset.threedmdDepth;
        el.classList.remove("threedmd-hidden", "threedmd-visible");
      }

      processInlineMarkers(el, this.currentDepth);

    } else {
      // ── Fallback path: getSectionInfo() returned null ─────────────────────
      // This happens for block math (MathJax/KaTeX), file embeds, and other
      // special elements that Obsidian doesn't map back to source lines.
      // Use the sequential state left by the most recent section-info-aware call.
      const text = el.textContent?.trim() ?? "";

      // Depth-tag and span-marker lines still produce recognisable textContent
      // even for special elements, so check them defensively.
      if (isSpanEnd(text)) {
        this.seqSpan.set(id, null);
        this.seqPending.set(id, null);
        el.style.display = "none";
        return;
      }
      const ss = parseSpanStart(text);
      if (ss !== null) {
        this.seqSpan.set(id, ss);
        this.seqPending.set(id, null);
        el.style.display = "none";
        return;
      }
      const bd = parseDepthTag(text);
      if (bd !== null) {
        this.seqPending.set(id, bd);
        el.style.display = "none";
        return;
      }

      const pending = this.seqPending.get(id) ?? null;
      const span    = this.seqSpan.get(id) ?? null;
      const depth   = pending ?? span;
      this.seqPending.set(id, null);   // single-block pending is consumed after one block

      if (depth !== null) {
        el.dataset.threedmdDepth = String(depth);
        el.classList.toggle("threedmd-hidden",  depth > this.currentDepth);
        el.classList.toggle("threedmd-visible", depth <= this.currentDepth);
      } else {
        delete el.dataset.threedmdDepth;
        el.classList.remove("threedmd-hidden", "threedmd-visible");
      }

      processInlineMarkers(el, this.currentDepth);
    }
  }
  
  setDepth(d: number): void {
    this.currentDepth = d;
    this.updateStatusBar();
    this.app.workspace.iterateAllLeaves((leaf: WorkspaceLeaf) => {
      if (!(leaf.view instanceof MarkdownView)) return;
      const view = leaf.view;
      // Live preview — push depth change into the CM6 state machine
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (view.editor as any).cm?.dispatch({ effects: threedmdDepthEffect.of(d) });
      // Reading mode — directly update elements that postProcess already tagged;
      // we do NOT rely on previewMode.rerender() because it does not reliably
      // re-invoke post-processors in all Obsidian versions.
      this.applyDepthToReadingView(view.containerEl, d);
    });
  }

  /**
   * Walk all block elements and inline spans that postProcess/processInlineDepth
   * stamped with data-threedmd-depth and toggle threedmd-hidden / threedmd-inline-hidden
   * to match the new depth threshold.  Called directly on every depth change.
   */
  private applyDepthToReadingView(container: HTMLElement, maxDepth: number): void {
    // Block-level elements (p, h1-h6, ul, table, …) tagged by postProcess
    container.querySelectorAll<HTMLElement>("[data-threedmd-depth]:not(span)").forEach(el => {
      const d = parseInt(el.dataset.threedmdDepth ?? "99", 10);
      if (d > maxDepth) {
        el.classList.add("threedmd-hidden");
        el.classList.remove("threedmd-visible");
      } else {
        el.classList.remove("threedmd-hidden");
        el.classList.add("threedmd-visible");
      }
    });
    // Inline spans created by processInlineDepth
    container.querySelectorAll<HTMLElement>("span[data-threedmd-depth]").forEach(el => {
      const d = parseInt(el.dataset.threedmdDepth ?? "99", 10);
      if (d > maxDepth) {
        el.classList.add("threedmd-inline-hidden");
      } else {
        el.classList.remove("threedmd-inline-hidden");
      }
    });
  }

  async exportToPdf(): Promise<void> {
    const view = this.app.workspace.getActiveViewOfType(MarkdownView);
    if (!view || !view.file) { new Notice("Open a note before exporting to PDF."); return; }

    const raw  = view.editor ? view.editor.getValue() : await this.app.vault.read(view.file);
    const fm   = raw.match(/^---\n[\s\S]*?\n---\n?/);            // skip YAML frontmatter
    const body = fm ? raw.slice(fm[0].length) : raw;
    const filtered = filterMarkdownByDepth(body, this.currentDepth);

    const printRoot = document.body.createDiv({
      cls: "threedmd-print-root markdown-preview-view markdown-rendered",
    });

    try {
      await MarkdownRenderer.render(this.app, filtered, printRoot, view.file.path, this);
      await new Promise<void>(r => setTimeout(r, 300));   // let math/embeds settle

      const cleanup = () => { printRoot.remove(); window.removeEventListener("afterprint", cleanup); };
      window.addEventListener("afterprint", cleanup);
      window.print();                                     // user picks "Save as PDF"
      setTimeout(cleanup, 60_000);                        // safety net if afterprint never fires
    } catch (e) {
      printRoot.remove();
      new Notice("3DMarkdown: PDF export failed — see console.");
      console.error(e);
    }
  }

  updateStatusBar(): void {
    if (this.statusBarEl) {
      this.statusBarEl.setText(
        this.currentDepth >= 99 ? "depth: all" : `depth: ${this.currentDepth}`,
      );
    }
  }

  async loadSettings(): Promise<void> {
    this.settings = Object.assign({}, DEFAULT_SETTINGS, await this.loadData());
  }

  async saveSettings(): Promise<void> {
    await this.saveData(this.settings);
  }
}

// ---------------------------------------------------------------------------
// Settings tab
// ---------------------------------------------------------------------------

class ThreeDMDSettingTab extends PluginSettingTab {
  plugin: ThreeDMDPlugin;

  constructor(app: App, plugin: ThreeDMDPlugin) {
    super(app, plugin);
    this.plugin = plugin;
  }

  display(): void {
    const { containerEl } = this;
    containerEl.empty();
    containerEl.createEl("h2", { text: "3DMarkdown settings" });

    new Setting(containerEl)
      .setName("Default depth")
      .setDesc("Depth level shown when a note is opened. Set to 99 to show all content.")
      .addSlider(slider =>
        slider
          .setLimits(1, 10, 1)
          .setValue(this.plugin.settings.defaultDepth >= 99 ? 10 : this.plugin.settings.defaultDepth)
          .setDynamicTooltip()
          .onChange(async value => {
            this.plugin.settings.defaultDepth = value >= 10 ? 99 : value;
            await this.plugin.saveSettings();
          }),
      );

    new Setting(containerEl)
      .setName("Show depth in status bar")
      .addToggle(toggle =>
        toggle.setValue(this.plugin.settings.showDepthInStatusBar).onChange(async value => {
          this.plugin.settings.showDepthInStatusBar = value;
          await this.plugin.saveSettings();
        }),
      );
  }
}
