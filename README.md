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

See **Running it with real footage** below.

## Running it with real footage

### One-time setup

```bash
python3 -m venv .venv && ./.venv/bin/pip install -e engine faster-whisper pytest jsonschema PyYAML
```

```bash
./panel/install.sh
```

Then restart Premiere, enable **Settings → Plugins → Enable developer mode** if the
panel does not appear, and open **Window → UXP Plugins → AutoEdit**. In the panel,
set **Media root** (where your footage lives) and **Jobs folder** (where plans land).
Both persist.

### Per job

Point the engine at your rushes. The first run transcribes; everything after is cached.

```bash
./.venv/bin/autoedit plan --job EP001 --recipe podcast-2cam --media ~/Footage/EP001/*.mov --media-root ~/Footage --model large-v3 --out ~/AutoEdit-jobs/EP001.editplan.json
```

Then in the panel: pick the plan, check the summary and warnings, press **Build sequence**.
It always builds a *new* sequence, and one ⌘Z undoes the whole thing.

### Multi-camera

Roles map sources onto tracks, as defined in the recipe:

```bash
./.venv/bin/autoedit plan --job EP001 --recipe podcast-2cam --media camA.mov camB.mov --role cam-a cam-b --media-root ~/Footage --out ~/AutoEdit-jobs/EP001.editplan.json
```

### Silent footage: promos and b-roll

Footage with no usable speech is cut from the pictures instead. Shot detection
finds the boundaries, quality scoring throws away the black, frozen, soft and
shaky ones, and a music bed puts the cuts on the beat.

```bash
./.venv/bin/autoedit plan --job PROMO01 --recipe promo-silent --media ~/Footage/broll/*.mp4 --music ~/Footage/track.wav --media-root ~/Footage --out ~/AutoEdit-jobs/PROMO01.editplan.json
```

**The music bed is found automatically.** Drop a single audio-only file next to
the footage and `promo-silent` uses it -- no flag needed:

```bash
./.venv/bin/autoedit plan --job PROMO01 --recipe promo-silent --visual --media ~/Footage/*.MP4 --media-root ~/Footage --out ~/AutoEdit-jobs/PROMO01.editplan.json
```

```
music: found million dollar baby.mp4 in /Users/you/Footage
music: 92.0 BPM, 92 beats, confidence 0.53
```

It only fires when there is **exactly one** candidate -- with several it lists them
and asks, because scoring a promo to the wrong track is worse than a question.
Extensions are not trusted: a track exported as `.mp4` with no video stream still
counts. `--music` overrides, `--no-music` opts out.

This is per-recipe (`auto_music: true`), and only `promo-silent` sets it. Podcast
recipes declare a `music` role for a bed the editor adds by hand, and a rough cut
that silently gained a soundtrack would be a nasty surprise.

**Or choose the track in the panel.** Automatic covers one loose file beside the
rushes; a real library does not look like that. The panel's **Music** dropdown
lists every audio-only file under the media root, so keeping tracks in a `Music`
folder works:

```
Footage/
  C1367.MP4  C1371.MP4  C1376.MP4
  Music/
    million dollar baby.mp4      <- offered as "Music/million dollar baby - 1:00"
    Cues/sting.mp4               <- and nested folders too, three deep
```

Audio-only is decided by the index's probe, not by extension, so a track exported
as `.mp4` is offered as music rather than turning up in the clip list. The scan is
capped at three folders deep and 400 files; hitting either is reported in the panel
rather than quietly shortening the list.

Picking a track is what makes pacing bite on silent footage -- the same two clips
at `punchy`, up to 10s:

| Music | Shots | Shot length |
|---|---|---|
| None or unfound | 4 | 34 frames, uniform |
| `Music/Cues/sting.mp4` | 5 | 39-40 frames, on the 92 BPM grid |

Add `--visual` to cut from the pictures even when the footage *does* have audio.

The engine reports what it threw away and why, per shot:

```
C1367.MP4: 3 shots, 3 usable, 3 clips, kept 4.8s of 13.5s (removed 64%)
promo_reel.mp4: 5 shots, 3 usable  --  2 rejected: black frames, frozen frame
```

If everything gets rejected, the thresholds are wrong for your footage rather
than the footage being unusable. Lower `min_sharpness` and `min_brightness` in
the recipe's `visual:` block first.

**Speed.** Analysis decodes the source three times, which on 4K HEVC is the whole
cost — about 55s for 19s of footage even with VideoToolbox and downscaled
analysis. Cutting from proxies is dramatically faster and the measurements are
the same.

**Beat detection is honest about ambiguity.** Half-versus-double tempo is a real
musical question at the extremes of the range, so the grid flags it rather than
silently picking. If a cut feels twice or half as fast as the track, halve or
double `beats_per_shot`.

### Tuning the cut

Transcripts are cached on content hash, so re-running after a recipe change is
instant — no re-transcription. Edit `engine/recipes/*.yaml`, re-run the same
command, rebuild in the panel. Comparing all three recipes on one 14s clip:

| Recipe | Removed | Result |
|---|---|---|
| `client-promo` | 10% | leaves pacing to the editor |
| `podcast-2cam` | 12% | drops `um` and false starts, keeps voice |
| `social-short` | 29% | also drops `So,` and `you know` |
| `promo-silent` | n/a | no speech: cuts from shots, quality and beats |

Add `--no-cache` to force re-transcription.

### Two things that will surprise you

**Model choice changes how much gets cut.** Cuts are gated on transcript
confidence (`min_confidence`, default 0.55) — the engine refuses to cut on speech
it cannot read, and says so in the warnings. A smaller model means lower
confidence means fewer cuts. Use `--model large-v3` for real work; `small` is for
iterating.

**Nothing leaves your machine.** `whisper-local` runs entirely offline. The first
run downloads the model (~460MB for `small`, ~3GB for `large-v3`), then never again.

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
