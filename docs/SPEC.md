# 3DMarkdown Specification v0.1

> Canonical reference for the 3DMarkdown depth-tag format.

---

## 1. Motivation

Scientific and technical documents exist simultaneously at many levels of detail:
a one-line slide bullet, a PI update, a methods section, a full protocol, and a
raw lab notebook. Today these representations are maintained as separate files that
diverge over time.

3DMarkdown introduces a **depth axis** as a first-class property of a document.
The author writes at maximum granularity and derives all shallower representations
for free via a single render call.

---

## 2. File format

### 2.1 Extensions

| Extension | Meaning |
|-----------|---------|
| `.md`     | Standard Markdown — depth tags are valid but optional |
| `.3dmd`   | Explicitly depth-tagged Markdown |

Both extensions are treated identically by all conforming tooling. The `.3dmd`
extension signals authorial intent and may be used to trigger editor integrations.

### 2.2 Encoding

UTF-8 plain text. Line endings: LF or CRLF (normalised to LF before processing).

---

## 3. Syntax

### 3.1 Block-level depth tag

A **block depth tag** is a line that, after stripping leading and trailing
whitespace, contains only a depth tag where N is a positive decimal integer.

| Form  | Example     |
|-------|-------------|
| Long  | `{depth=N}` |
| Short | `{dN}`      |

The tag applies to the **immediately following block** (paragraph, list, table,
fenced code block, or any other Markdown block element). The tag line itself
is never included in rendered output.

**Example:**

```markdown
{depth=2}
Periods ranged from 1.0–5.7 d across all three confirmed variable star candidates.

{d2}
Equivalent shortform for the same depth.
```

### 3.2 Span depth context (multi-block)

A **span depth context** marks a run of consecutive blocks at the same depth,
avoiding the need to place a tag before every block.

| Form  | Open marker       | Close marker |
|-------|-------------------|--------------|
| Long  | `{start depth=N}` | `{end}`      |
| Short | `{sdN}`           | `{e}`        |

Both the open and close markers must appear on lines by themselves (same
whitespace rules as block tags). The open marker applies depth N to every
block between it and the matching close marker. A missing close marker is
treated as "span extends to end of document".

Single-block depth tags inside a span override the span depth for that one
block only.

**Example:**

```markdown
{start depth=2}
All these paragraphs belong to depth 2.

No need to repeat the tag.

Even this one.
{end}

{sd3}
Shortform span at depth 3.

Also depth 3.
{e}
```

### 3.3 Inline span depth context

An **inline span** opens a depth context at any point within text flow — mid-sentence,
across paragraphs, or anywhere a trailing tag would be too coarse.

| Form  | Open marker        | Close marker |
|-------|--------------------|--------------|
| Long  | `{start depth=N}`  | `{end}`      |
| Short | `{sdN}`            | `{e}`        |

The open marker may appear anywhere in text — not just on a standalone line.
All content from that point onwards has depth N until the matching close marker is
encountered.  The span may cross paragraph boundaries; blocks falling entirely within
the span are treated as if they carry an inline depth annotation at level N.  Both
markers are consumed (not rendered).

**Context determines block vs. inline:** when `{sdN}` / `{start depth=N}` appears
on a standalone line it is a block span start (§3.2); when embedded in text it
opens an inline span.  `{e}` / `{end}` on a standalone line closes the active
block span (or, if none is open, the innermost inline span); within text it
always closes the innermost inline span.

Inline spans may be nested: `{sd2}outer {sd3}inner{e} back to 2{e}`.

**Example — within a single paragraph:**

```markdown
The survey covers 4,200 targets. {sd2}Of these, three passed the FAP threshold.{e}
```

At `max_depth=1` renders as: `The survey covers 4,200 targets.`

**Example — crossing paragraph boundaries:**

```markdown
The survey covers 4,200 targets. {sd2}Of these, three passed the FAP
threshold.

LST-07 had period 3.4 d; LST-11 had period 1.9 d.{e} Further follow-up planned.
```

At `max_depth=1` renders as:
`The survey covers 4,200 targets.  Further follow-up planned.`

### 3.4 Inline depth tag

An **inline depth tag** is the suffix `{depth=N}` (or shortform `{dN}`)
appended immediately after a text run within a paragraph. It applies to the
text **preceding** it, back to the previous inline tag or the start of the
paragraph, whichever comes first.

**Example:**

```markdown
We found three transit candidates{depth=1}, all with periods under 10 d{depth=2}, of which
LST-07 showed a secondary eclipse depth requiring follow-up{depth=3}.

We found three transit candidates{d1}, all with periods under 10 d{d2}, of which
LST-07 showed a secondary eclipse depth requiring follow-up{d3}.
```

At `max_depth=1`, only `"We found three transit candidates"` is rendered.

### 3.5 Untagged content

Content without any depth tag is **always rendered** regardless of the depth
threshold. Use this for headings, figure captions, table headers, and any
structural content that must appear at every abstraction level.

### 3.6 Depth values

- N is a positive decimal integer ≥ 1.
- There is no upper bound. Authors may use as many layers as needed.
- The highest N in a document represents the maximum-detail source layer.

**Suggested conventions** (not enforced by the format):

| Depth | Typical use |
|-------|-------------|
| 1 | One-liner / slide bullet / external-facing summary |
| 2 | Short summary / PI update / abstract paragraph |
| 3 | Methods overview / short report |
| 4 | Full paper section / detailed protocol |
| 5+ | Raw notebook / decision log / edge-case notes |

---

## 4. Rendering semantics

`render(doc, max_depth=N)` **includes**:

- All content with no depth tag (untagged).
- All block-tagged content with `depth ≤ N`.
- Within an included block, all inline-tagged spans with `depth ≤ N`. Spans
  with `depth > N` are dropped; the surrounding text is joined without them.

`render(doc, max_depth=N)` **excludes**:

- Block-tagged content with `depth > N` (the block is omitted entirely).
- Inline-tagged spans with `depth > N`.

Blocks that become empty after inline filtering are omitted from the output.

The result of a render is **valid standard Markdown** with no depth tags present.

---

## 5. Coherence

The format does not enforce coherence. Responsibility for producing coherent
rendered output lies with the author or with post-processing tooling.

Recommended strategies:

1. **Author explicit transitions at each depth level.** The lowest-depth content
   should be self-contained. Each deeper layer adds detail without requiring
   shallower layers to be restructured.

2. **LLM-assisted coherence repair.** After rendering, a language model can
   smooth transitions in the filtered output. See `threedmd repair`.

3. **Dependency management via links.** Since output is standard Markdown/GFM,
   figure references and wikilinks in lower-depth blocks naturally resolve
   without special handling.

---

## 6. Relation to prior art

| System | Multi-output | Depth tagging | Inline granularity | Coherence repair |
|--------|-------------|---------------|--------------------|------------------|
| DITA | ✅ | ✅ (audience attr) | ⚠️ block-only | ❌ |
| Pandoc/Quarto | ✅ (format) | ⚠️ profile-based | ❌ | ❌ |
| TreeWriter | ⚠️ | ✅ (AI-assisted) | ❌ | ⚠️ |
| **3DMarkdown** | ✅ | ✅ | ✅ | LLM-assisted ✅ |

Key differences from DITA: plaintext-first, Markdown-native, no XML toolchain,
inline granularity, LLM-friendly.

Key differences from Pandoc conditional blocks: depth is an ordered integer axis
rather than a named profile; a single threshold controls the entire render.

---

## 7. Tooling requirements

Conforming implementations MUST:

- Recognise `{depth=N}` and shortform `{dN}` as block tags when either is the
  sole content of a line.
- Recognise `{depth=N}` and shortform `{dN}` as inline tags when either appears
  inside a paragraph.
- Recognise `{start depth=N}` / `{sdN}` and `{end}` / `{e}` as span context
  markers when they are the sole content of a line; apply the span depth to all
  blocks between them.
- Recognise `{start depth=N}` / `{sdN}` as an inline span open marker wherever
  it appears in text (not just on a standalone line); recognise `{e}` / `{end}`
  within text as the corresponding inline span close.
  An unclosed inline span extends to end of document.
- Treat untagged content as always-visible.
- Produce output that is valid Markdown (no depth tags in output).
- Not modify content inside fenced code blocks (verbatim regions are exempt
  from depth-tag processing).

Conforming implementations SHOULD:

- Accept both `.md` and `.3dmd` file extensions without distinction.
- Provide an `inspect` mode that lists depth levels present in a document.
- Preserve blank lines and block structure in rendered output.

---

## 8. Versioning

This document describes version 0.1 of the 3DMarkdown specification.
The version is surfaced in the `threedmd --version` CLI output and in the
`__version__` attribute of the Python library.
