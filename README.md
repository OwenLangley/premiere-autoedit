# premiere-autoedit

Automated rough-cut assembly, ingest and brand application for Premiere Pro.

The system turns rushes into a first-pass timeline an editor refines. It does not
try to replace editorial judgment — it does the mechanical 80% and explains every
decision it made so the editor can disagree with it.

## Why it is split in two

```
┌─ engine/ (Python, off-Premiere) ───────────────────┐
│  ffprobe → audio extract → word-level transcript   │
│  → silence/filler/stutter detection → EditPlan     │
└──────────────────┬─────────────────────────────────┘
                   │  EditPlan JSON  (schema/edit-plan.schema.json)
┌──────────────────▼─────────────────────────────────┐
│  panel/ (UXP) — the only code that touches Premiere│
│  read plan → executeTransaction() → sequence       │
└────────────────────────────────────────────────────┘
```

Two constraints force this shape:

- **UXP has no Node.js runtime.** ffmpeg and Whisper cannot run inside a Premiere
  panel, so analysis has to live in a separate process regardless.
- **The Premiere API is async and hard to test.** Keeping all the editorially
  risky logic on the Python side means it is testable on any machine, and the
  panel stays small enough to be reliable.

Everything crosses the boundary as one versioned document, the **EditPlan**.

## The EditPlan contract

Two rules in the schema are load-bearing:

- **`relPath` + `hash`, never absolute paths.** Media resolves at apply time via
  a configurable root. Today that root is a local drive; when it becomes a NAS,
  one setting changes and existing plans keep working.
- **Every clip carries `reason` and `confidence`.** The panel shows them.
  Editors do not adopt a tool that makes cuts they cannot interrogate.

Timeline positions are **integer frames**, source in/out are seconds. Nothing
adds two floats to decide where a clip lands, so a 200-cut assembly cannot drift.

## Quick start

```bash
python3 -m venv .venv && ./.venv/bin/pip install -e engine pytest jsonschema PyYAML
```

```bash
./.venv/bin/autoedit recipes
```

```bash
./.venv/bin/autoedit plan --job EP042 --recipe podcast-2cam --media rushes/*.mov --media-root rushes --out jobs/EP042.editplan.json
```

Then in Premiere: load `panel/` via the UXP Developer Tool, point it at the media
root and the jobs folder, pick the plan, review, and press **Build sequence**.

## Recipes

Editorial policy lives in `engine/recipes/*.yaml`, not in code, so a producer can
tune it. Three ship by default:

| Recipe | min_silence | Fillers | Intent |
|---|---|---|---|
| `podcast-2cam` | 0.50s | conservative | Stay conversational. An over-cut podcast reads as artificial. |
| `social-short` | 0.25s | **aggressive** | The one format where cutting `like` / `you know` is correct. |
| `client-promo` | 0.70s | conservative | Music-led. Deliberately does less; rhythm stays with the editor. |

A typo in a recipe is rejected rather than silently ignored — a setting that
quietly does nothing is worse than a crash.

## Editorial rules the engine enforces

These are tested, not aspirational (`engine/tests/test_detect.py`):

- **Never discards speech.** When a constraint is violated the fix is always to
  cancel a cut, never to drop content.
- **Never cuts what it cannot read.** Low-confidence transcript regions veto
  cuts, including cuts adjacent to them, and say so in the warnings.
- **Never cuts mid-word.** Requires word-level timestamps; segment-level input
  is rejected rather than interpolated.
- **Handles apply to silence, not fillers.** Padding a 250ms "um" by 200ms of
  handles would leave most of the "um" in.
- **A handle never crosses a filler boundary.** Otherwise a filler adjacent to a
  pause leaks its tail into the next clip.
- **Every internal join gets a short crossfade**, or each cut clicks.

## Panel behaviour

- **Always builds a new sequence.** Never mutates what the editor has open.
- **One transaction per stage, each named**, so a single ⌘Z undoes "apply brand
  pass" rather than the last of 200 edits.
- **Fails before touching anything.** Plan validation, media resolution and
  brand-kit checks all run before the first object is created.

## Testing

```bash
./.venv/bin/python -m pytest engine/tests -q
```

```bash
cd panel && node --test test/*.test.js && ./node_modules/.bin/tsc --noEmit
```

The panel type-checks against Adobe's published `@adobe/premierepro` types. That
is not a substitute for running it, but it already caught one real API misuse
(`findItemsMatchingMediaPath` is an instance method on `ClipProjectItem`, not a
static on `ProjectItem`).

`schema/timebase-vectors.json` holds 224 conformance assertions that **both** the
Python and JavaScript timebases must reproduce. If those two implementations ever
drift, clips land on the wrong frame and nothing else would catch it.

## What is verified

Engine, on this machine:

- End to end on real media: probe → ffmpeg → transcript → cuts → validated plan
- 72 Python tests, 24 JS tests, clean type check
- 224 cross-language timebase conformance assertions

Panel, **against Premiere Pro 26.3.2**:

- Plugin loads; panel renders and is interactive
- Media root and jobs folder persist across restarts (persistent tokens)
- A plan written by the engine loads, validates, and summarises correctly
- `EP042_rough_v1` built from a real plan: **4 clips, frame-exact positions, no gaps**
- **One ⌘Z removes the whole assembly** — transaction grouping is correct
- Post-build verification reads the timeline back and reports any drift

Type-checking against `@adobe/premierepro` caught two real API errors before any
code ran: `findItemsMatchingMediaPath` and `createSetInOutPointsAction` both live
on `ClipProjectItem`, not `ProjectItem`.

**[docs/premiere-uxp-findings.md](docs/premiere-uxp-findings.md) records everything
measured about Premiere's UXP behaviour** — several points contradict Adobe's docs
and their own samples. Read it before touching `apply.js`. The headline: every
action must be built inside `project.lockedAccess()`, and per-clip in/out inside a
single transaction silently does not work, so clips are placed as subclips.

## Known defects

- **Transcript import does not work.** Premiere rejects our JSON shape and the
  expected schema is undocumented. Non-fatal: the build completes and warns. The
  self-test captures the real schema from any clip that already has a transcript.
- **Final clip lands one frame long** (44 requested, 45 placed) while every earlier
  clip is exact. No gap or overlap results. The verifier reports it.

## Known API gaps

- No multicam *creation* API (detection only). Better approach for automation
  anyway: let the engine choose the camera per segment and lay cuts on separate tracks.
- No scene-edit-detection API — do it in the engine.
- Time Remapping is inaccessible via both ExtendScript and UXP as of 26.3.
- MOGRT insertion is a direct call, not an Action, so each insert is its own undo
  step rather than joining the brand-pass transaction.

## Timing note

Adobe's ExtendScript support for Premiere ends around September 2026. UXP is the
only sensible target; this project does not contain any ExtendScript.
