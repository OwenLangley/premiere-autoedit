# Story sections

Letting an editor say *exterior → cooking → serving → customer eating →
exterior* and having the engine build that, inside the length, shape and beat
constraints the form already sets.

This specifies **v1: the editor tags the shots.** No machine vision. The whole
point of doing it this way first is that the structural half — ordering,
dividing the runtime, landing on beats, reporting what could not be filled — is
the half that has to be right, and it can be built and verified without a model
being correct about anything. v2 swaps the tagging for CLIP behind the same
interface; see [What v2 changes](#what-v2-changes) at the end.

## The word "beat" is already taken

The engine uses *beat* for a musical beat throughout `music.py`, `plan.py` and
`options.py`. Story units must not share the name, or `beats_per_shot` and
"beats in the story" will collide in every function that touches both.

**Story units are called `sections`.** That name is not new to the codebase:
`sectionId` already exists in the EditPlan schema
([edit-plan.schema.json:313](../schema/edit-plan.schema.json)), the plan builder
takes a `section_id` argument ([plan.py:164](../engine/autoedit/plan.py)), and
the panel already groups timeline entries by it so an editor can toggle a whole
section off before applying ([plan.js:118](../panel/src/plan.js)). It is
currently never populated on the visual path. This feature fills it in.

## What already works, and what has to be built

The ordering machinery is largely present. The content understanding is entirely
absent.

| Capability | State today |
|---|---|
| Clips appear in the order the editor listed them | **Works.** `append_cuts` is called once per media file in `collected` order and lays that file's spans end-to-end from the playhead ([cli.py:632](../engine/autoedit/cli.py)) |
| Runtime is divided across sources so all appear | **Works.** `_spread_across_sources` ([options.py:301](../engine/autoedit/options.py)) |
| Timeline entries carry a section, panel groups by it | **Works, unused.** Schema, builder and panel all support it; nothing sets it |
| Cuts land on musical beats | **Works.** `cut_points()` and `_fit_to_beats` |
| Knowing a shot shows food rather than a shopfront | **Does not exist.** A shot is four numbers: `score = 0.5*sharpness + 0.3*motion_fit + 0.2*exposure_fit` ([visual.py:385](../engine/autoedit/visual.py)). `MediaInfo` carries codec, resolution, rotation, keyframe interval — nothing about content |

So v1 is mostly wiring plus one new allocation rule, and the editor supplies the
one thing the engine cannot work out.

## Data model

### The template lives in the recipe

The value here is repetition. "Restaurant promo" is a house format, not a
one-off, so the section list belongs somewhere reusable. New top-level
`story:` block in the recipe, parsed alongside the existing `music:` block and
added to the known-keys set in `recipe.py:137`:

```yaml
story:
  sections:
    - id: exterior      # stable key: what the request and the plan refer to
      label: Outside     # what the panel shows; translated via i18n, not this file
      weight: 1
    - id: cooking
      label: Cooking
      weight: 2          # twice the runtime of a weight-1 section
    - id: serving
      label: Serving
      weight: 1
    - id: eating
      label: Eating
      weight: 2
    - id: exterior_out
      label: Outside again
      weight: 1
```

Order in the list **is** order on the timeline. That is the whole structure.

`weight` is relative, not seconds — the runtime target comes from the form and
can change per job, so absolute seconds here would be wrong the moment someone
picks a different length. Default `1`.

### The assignment lives in the job request

Which clip belongs to which section is a per-job decision, so it goes in the
request. New `options.story`:

```json
"options": {
  "story": {
    "template": "restaurant-promo",
    "assign": {
      "shoot/C1367.MP4": "exterior",
      "shoot/C1371.MP4": "cooking",
      "shoot/C1374.MP4": "cooking",
      "shoot/C1376.MP4": "serving",
      "shoot/C1377.MP4": "eating"
    }
  }
}
```

**A map keyed by `relPath`, not an array parallel to `media`.** The existing
`--role` flag is a parallel array ([cli.py:849](../engine/autoedit/cli.py)) and
that is fine for a flag a human types once, but a request is written by the
panel, stored, listed and potentially re-submitted. If anything ever reorders
`media` — a re-render of the clip list, a dedup, a user removing one clip — a
parallel array silently reassigns every shot after the change. The failure is
invisible and produces a confidently wrong edit, which is the exact class of bug
this project keeps getting bitten by. A map cannot misalign.

`template` names a recipe-provided section list. Omitting it while providing
`sections` inline is also allowed, for a one-off story that is not worth a
recipe.

Schema work in `job-request.schema.json`: the file is `additionalProperties:
false` throughout, deliberately, so both `story` and its inner keys must be
declared or every request carrying one is rejected.

### The CLI

The helper maps request options onto engine flags ([`request_to_argv`, watch.py:313](../helper/watch.py)),
so the engine needs flags for this:

```
--section <id> [<id> ...]     one per --media, in the same order (like --role)
--story-section <id>:<label>:<weight>   repeatable; overrides the recipe's list
```

Parallel arrays are acceptable *here* because the helper builds `--media` and
`--section` from the same map in one pass and can guarantee alignment, and a
human running the CLI by hand is typing both in one line where they can see them.

### The output

Every timeline entry produced from a section carries `sectionId` — already in
the schema, already rendered by the panel. Nothing downstream changes shape.

The plan's `warnings` gain the new codes below. `notes.py` and both catalogues in
`panel/src/i18n.js` must be updated together; the cross-catalogue test in
`panel/test/i18n.test.js` enforces it.

## Allocation

### When a story is active

If `options.story` is absent, or present but **no** clip is assigned, behaviour
is exactly as today. Backwards compatible by construction.

If any clip is assigned, the story is active and the allocation unit becomes the
section rather than the source file.

### Dividing the runtime

Requires a duration target. `duration.mode: none` means there is no runtime to
divide, so weights are meaningless — in that case sections still control
**order**, and each is filled from whatever its clips yield. Warn
(`story.noTargetLength`) so the editor knows their weights did nothing.

With a target:

```
share(section) = target * weight(section) / sum(weights of sections that have clips)
```

Sections with no assigned clips are excluded from the denominator, not given
zero — otherwise an unfilled section silently shortens the whole edit rather
than redistributing its time.

Within a section, `_spread_across_sources` runs unchanged over that section's
clips with `share` as the target. That reuse is the point: "every source in this
section appears" is the right sub-policy, and it is already written, tested, and
knows about slicing long spans at a fast cut rate.

### Where this collides with today's behaviour

Today's rule is *every source appears*. A story says *this section gets 20% of
the runtime from whichever clips are in it*. Those genuinely conflict — five
clips in one section and one in another means the lone clip gets far more screen
time per clip than the others.

**The story wins.** That is what the editor asked for. But it must be said out
loud rather than discovered: when the per-clip share inside a section falls below
`min_clip_length`, emit `story.sectionCrowded` naming the section and the count,
because at that point the honest answer is "you put too many clips in Cooking for
a 15-second edit" and only the editor can decide whether to cut the count or
lengthen the film.

### Beats

No new beat logic. Section boundaries fall at clip boundaries, every clip
boundary is already placed on a musical beat by `_fit_to_beats`, so a section
transition lands on a beat for free.

A stronger version — forcing section boundaries onto every 4th or 8th beat so a
story transition coincides with a bar — is deliberately **not** in v1. It reads
as more intentional and it is a real improvement, but it needs a downbeat
estimate the detector does not currently produce, and it constrains section
lengths in a way that interacts with `weight` in ways worth seeing evidence about
first.

## Ordering rules

1. Sections in the order the template declares them.
2. Within a section, clips in the order the editor selected them.
3. Within a clip, shots chronological — unchanged.

A clip may be assigned to only one section in v1. Reusing one shot in both the
opening and closing exterior is a real editorial want, and it is a v1.1 item
(`assign` becomes `relPath -> [sectionId]`), left out here so the first version
does not have to answer "which half of the clip goes where".

## Failure modes, which are the actual design

This project's recurring failure is not being wrong. It is being wrong
*silently* — a sequence of stills that passed verification, a helper with no
ffmpeg that accepted every job. A story edit fails the same way: it produces
something plausible that tells the wrong story, and nobody can see the difference
between "the system understood" and "the system filled the gap".

So the rule is: **an unfillable section is never quietly filled.**

| Situation | Behaviour |
|---|---|
| Section has no assigned clips | Skip it. Redistribute its share across the rest. Warn `story.sectionUnfilled` naming it. It appears in the panel's section list as empty, not absent |
| Some clips unassigned | Those clips are **not used**. Warn `story.clipsUnassigned` with the count and names |
| All clips unassigned | Story inactive; behave exactly as today. No warning — this is the ordinary no-story job |
| Section's share < `min_clip_length` | Warn `story.sectionTooShort`; give it one minimum-length clip and take the difference from the largest section |
| Too many clips for the share | Warn `story.sectionCrowded`; `_spread_across_sources` already drops the weakest |
| `template` names a section list the recipe does not have | Hard error before any work. A typo must not fall back to "no story" — that produces a complete, plausible, wrongly-ordered edit |
| Assignment names an unknown section id | Hard error, same reasoning |

The two hard errors are deliberate. Every other unknown key in this project's
config is already rejected rather than defaulted — `job-request.schema.json` is
`additionalProperties: false` for exactly this reason, and the reason is written
into its own description.

## The panel

The clip list already exists and already has per-clip rows (`media-list`,
[main.js:452](../panel/src/main.js)). Story assignment attaches there rather
than becoming a separate screen.

- Recipe carries a story template → a **Story** dropdown appears next to each
  selected clip, listing the section labels plus an unset option.
- A running summary under the list: `Outside 1 · Cooking 2 · Serving 1 · Eating
  1 · unassigned 2`. The unassigned count is the important number, because those
  clips are about to be silently excluded and this is the moment to notice.
- **Create Edit stays enabled with clips unassigned.** Blocking it would be
  wrong — a half-tagged job is a legitimate thing to run. The warning after the
  fact plus the count before it are enough.
- Section labels come from `i18n.js`, keyed `story.section.<id>`, with the
  recipe's `label` as the English fallback. A Japanese editor should not get a
  form that is Japanese except for five English words.

Known panel bug that this makes worse and should be fixed alongside: scrolling
over a `<select>` cycles its value. One dropdown per clip in a scrolling list
turns that from an annoyance into a way to silently reassign several shots while
scrolling past them.

## Verification

The structural half is fully testable without any judgment about content, which
is the reason for building it first.

1. **Allocation, unit level.** Weights divide a target correctly; an unfilled
   section redistributes rather than shortening the edit; a section below
   `min_clip_length` is handled by the stated rule and not by luck.
2. **Order, unit level.** Given assignments, timeline `sectionId`s appear in
   template order, and clips within a section in selection order. Assert on the
   plan, not on a rendering of it.
3. **Both hard errors.** An unknown template and an unknown section id each fail
   before any media is probed. Cheap to assert and the whole point.
4. **The silent-fill test.** Assign nothing to `eating`, build, and assert no
   timeline entry carries `sectionId: "eating"` **and** that the warning is
   present. This is the test that would catch the failure mode this design
   exists to prevent, so it is the one worth writing first.
5. **End to end, on real footage.** A five-section restaurant job at 15s with
   music, checking cuts still land on beats and section boundaries fall where
   the weights say. The existing beat verification applies unchanged.
6. **Regression.** 310 Python / 104 JS stay green; a request with no `story` key
   produces a byte-identical plan to today's.

## What v2 changes

Only the source of `assign`. Everything above — the template, the allocation,
the ordering, the warnings, the section rendering — stays.

v2 samples frames per shot, embeds them with CLIP alongside the section labels,
and proposes an assignment. The editor sees it pre-filled in the same dropdowns
and corrects it. That is a strictly better product than either half alone: the
model does the tedious part, the editor keeps the veto, and when the model is
wrong it is wrong somewhere visible.

Two things to expect when it arrives:

- **The middle sections are where it fails.** *Serving* and *eating* both show a
  table, food and people. The distinctions in the original request are
  narrative, not visual, and CLIP is weak on relational ones. The bookend
  exteriors will work well and mislead you about the rest.
- **Confidence must reach the panel.** A proposed assignment the model is
  unsure about has to look different from one it is sure about, or the editor
  reviews all of them or none of them.

## Risks

- **This is a different product.** Today's system is "assemble a rough cut from
  these clips". This is "tell this story". Worth confirming with the editors who
  will use it that the second one is what they want, before either version gets
  built.
- **Tagging is work.** Six clips is fine. Sixty is a chore, and the chore is
  exactly what v2 removes — so v1 may feel worse than no feature at all on a big
  shoot, and should probably be tried on a small one.
- **`weight` is a number, and editors think in shots.** "Cooking should be about
  twice as long" is natural; `weight: 2` is a translation of it. Watch whether
  people reach for it or leave everything at 1.
- **The Japanese strings are still mine, not a native speaker's** — and this adds
  a screenful more of them.
