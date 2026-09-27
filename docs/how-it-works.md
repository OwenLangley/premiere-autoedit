# How it works

The parts of this that are engineering rather than editing: why the tool is
shaped the way it is, what crosses between its halves, what is tested, and what
is known to be wrong. Start at the [README](../README.md) if you are trying to
make a cut rather than change one.

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
- 310 Python tests, 104 JS tests, clean type check
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

**[premiere-uxp-findings.md](premiere-uxp-findings.md) records everything
measured about Premiere's UXP behaviour** — several points contradict Adobe's docs
and their own samples. Read it before touching `apply.js`. The headline: every
action must be built inside `project.lockedAccess()`, and per-clip in/out inside a
single transaction silently does not work, so clips are placed as subclips.

## How long a job takes

Profiled cold on six 4K HEVC clips -- about three minutes of footage -- with a
reference:

| | before | after |
|---|---|---|
| whole job | 681s | **293s** |
| decoding | 628s (92%) | 237s |
| thumbnails | 32s | 34s |
| loading the shot recogniser | 7s | 8s |

Every measurement starts by decoding the same frames and scaling them to 640px,
and that used to happen separately for the shot detection, the brightness
sampling and the edge sampling -- four times over when a centre crop was wanted
too. They now read from one decode. The plan either way is the same plan:
identical timeline, identical candidates, identical warnings.

Decoding is still four fifths of a job, and there is no further saving in it
without changing what gets measured.

### Why the proxies are not analysed

The obvious next move is to measure the ProRes proxy rather than the 4K original,
since every measurement is downscaled to 640px anyway. Measured on one clip:

| | |
|---|---|
| Speed | **5.22x** -- 19.9s becomes 3.8s |
| Shot boundaries | identical |
| Shots passing the quality gate | 3 of 3, both ways |
| Brightness | 4.5% median difference |
| **Motion** | **26% median, 45% worst** |
| Sharpness | 14% median, 18.5% worst |

So it is not implemented, and not because 5x is unwelcome. A proxy is a
re-encode, and motion and sharpness are what rank the moments *inside* a shot --
which frame an editor is offered first. The boundaries and the pass/fail gate
happened to agree here, but that is one clip, and "the edit changed" is not
something to discover later in a commit labelled performance.

It is a real option, but it is a decision about what the tool measures rather
than a free speedup, and it needs a before/after on chosen cuts across several
clips before anyone takes it.

## Timing note

Adobe's ExtendScript support for Premiere ends around September 2026. UXP is the
only sensible target; this project does not contain any ExtendScript.

## How updating works

The [README](../README.md#when-something-goes-wrong) covers the buttons. This is
what they do, because three parts of it are load-bearing and none of them are
obvious.

`./update.sh` is the same thing from a shell. Both pull, **reinstall the panel**
and **restart the helper**. Those middle two matter more than they look: the
panel lives in `/Library` as a *copy*, so pulling alone changes nothing an editor
can see, and the helper holds the engine in memory, so pulling alone leaves it
running the old code. Both caught me while building this.

The first install is the only one that asks for a password. After it the plugin
folder belongs to the user, so every update after that is silent -- which is what
makes the button possible at all.

- **The updater detaches itself.** The last thing it does is restart the helper,
  and a child of the helper dies with it -- launchd stops the whole process
  group. It runs in its own session so it survives the restart it causes.
- **It never waits for a password.** `GIT_TERMINAL_PROMPT=0` and SSH batch mode,
  so a machine that cannot authenticate fails in seconds with something an editor
  can act on instead of hanging the helper forever.
- **It refuses to run over local changes** rather than clobbering them, and names
  the files.
- **It knows a rewritten history from a broken network.** Both make git fail
  without naming a file; only one of them is fixable by the person reading it.

Every plan records the sha that produced it, under `generator.commit`, with
`+dirty` when the checkout had uncommitted changes. Before that, every build this
project shipped reported version `0.1.0`, so a bug report could not say which
code ran.

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
