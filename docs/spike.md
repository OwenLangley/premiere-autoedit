# Phase 0 spike checklist

Everything here needs a Mac with Premiere Pro 2025/2026. None of it can be done
on a machine without Premiere installed, which is why it is a discrete phase
rather than something to discover mid-build.

Budget: half a day. The point is to convert three assumptions into facts before
any more code depends on them.

## 0. Audit versions first

`@adobe/premierepro` is versioned to match the host (currently 26.3.0), and the
UXP scripting API matured across 25.x → 26.x. Record the exact Premiere version
on every editor's machine before committing to a minimum.

The panel manifest currently declares `minVersion: 25.1.0`. Raise it to whatever
the team actually runs — declaring support for a version nobody has only creates
bug reports from a code path never tested.

## 1. Does the panel load?

```
UXP Developer Tool → Add Plugin → panel/manifest.json → Load
```

Then Premiere → Window → UXP Plugins → AutoEdit.

If it fails, the usual causes are a host `minVersion` above the installed build,
or a manifest schema mismatch. Both are one-line fixes.

## 2. Which clip-placement strategy does this build need? **(the important one)**

This is the single assumption that could invalidate the assembly logic.

`apply.js` defaults to `STRATEGY.IN_OUT`: set in/out on the master ProjectItem,
then overwrite onto the timeline, repeated per clip inside one CompoundAction.
That relies on Premiere evaluating each action **as it is added**. If it instead
evaluates at commit time, all 200 clips inherit the *last* in/out — and the
failure is quiet. The timeline looks populated; the ranges are just wrong.

Run the empirical check rather than reasoning about it:

```js
const { verifyStrategy } = require("./src/apply");
const ppro = require("premierepro");
const project = await ppro.Project.getActiveProject();
const root = await project.getRootItem();
const item = (await root.getItems())[0];      // any clip longer than 8s
console.log(await verifyStrategy(item));
```

It places three known ranges (1s, 2s, 3s) and reports what actually landed.

- Durations come back `[1, 2, 3]` → `IN_OUT` is safe. Keep the default.
- Durations all match the last range → switch to `STRATEGY.SUBCLIP`.

Record the answer in this file. It is cheap to measure and expensive to assume.

## 3. Does the folder handoff work?

Generate a plan on the same machine:

```bash
./.venv/bin/autoedit plan --job SPIKE --recipe podcast-2cam --media <a real clip> --media-root <its folder> --out <jobs folder>/SPIKE.editplan.json
```

In the panel, set the media root and jobs folder, confirm the plan lists, loads,
and shows a sensible clip count. This exercises the persistent-token flow, which
is the part most likely to behave differently from expectations.

## 4. Confirm undo granularity

Build a sequence, then press ⌘Z **once**. The whole assembly should disappear,
not the last clip. If it undoes one edit at a time, the CompoundAction is not
grouping and that needs solving before editors see it — an editor who has to
press ⌘Z 200 times will not use the tool again.

## 5. Spot-check the cut against the audio

Take one real episode. Listen to three or four of the joins.

Specifically listen for:

- Clipped word heads → increase `lead_in` in the recipe
- Audible clicks at joins → `crossfade_frames` is not being applied
- Any surviving "um" → check whether a handle is reaching across the filler
  boundary (there are regression tests for this, but real transcripts are messier
  than fixtures)
- Choppy pacing → raise `min_silence` and `min_clip_length`

The acceptance bar is not technical. It is whether an editor takes the output and
finishes from it rather than starting over.

## Results

_Fill in during the spike:_

| Check | Result | Notes |
|---|---|---|
| Premiere versions in use | | |
| Panel loads | | |
| Clip strategy | | `IN_OUT` or `SUBCLIP` |
| Folder handoff | | |
| Single-⌘Z undo | | |
| Audio spot-check | | |
