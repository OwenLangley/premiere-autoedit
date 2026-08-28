# Premiere Pro UXP: measured behaviour

Everything here was measured against **Premiere Pro 26.3.2** on macOS using the
panel's built-in self-test, not inferred from documentation. Adobe's docs and
even Adobe's own samples contradict several of these.

Re-run `Run self-test` in the panel when moving to a new Premiere version. The
report lands at `/tmp/autoedit-selftest/report.json`.

## 1. `manifest.host` must be an object, not an array

```json
"host": { "app": "premierepro", "minVersion": "25.1.0" }
```

The array form fails to load with:

```
Plugin com.company.autoedit : Expected the host attribute to be an object for the 3P Plugin
Failed to parse the manifest.json file.
```

Adobe's own `metadata-handler` sample uses the array form. Their `premiere-api`
sample uses the object form. Only the object form works.

**Diagnosing plugin load failures:** `~/Library/Logs/Adobe/Adobe Premiere Pro 2026/UXPLogs_*.log`
names the plugin and the exact reason. `Number of plugins added from user's fallback: 1`
confirms a successful load.

## 2. The entry script must sit at the plugin root

```html
<script src="index.js"></script>   <!-- works -->
<script src="src/main.js"></script> <!-- panel renders, nothing executes -->
```

With the subdirectory form the panel draws correctly and every control is inert,
with nothing in the UXP log. `index.js` at the root then `require("./src/main.js")`
is the structure Adobe's samples use.

> Honest caveat: the working change also converted `element.onclick = fn` to
> `addEventListener`. The two were not isolated from each other, so the script
> path is the likely cause but `onclick` has not been independently cleared.

## 3. Mutations require `lockedAccess` — this is the big one

Building **or** committing an Action outside `project.lockedAccess()` throws
`The script object is no longer valid.` Reads are unaffected.

Measured (`lifetime/*` in the self-test):

| Pattern | Result |
|---|---|
| Fetch item, read `getMediaFilePath()` | works |
| Fetch item, `await` something unrelated, read | works |
| Fetch item, `createSequence`, then read | works |
| Build an Action (no transaction) | **throws** |
| `executeTransaction` alone | **throws** |
| `lockedAccess` wrapping `executeTransaction` | **works** |

Note the third row: creating a sequence does **not** invalidate references. The
error message strongly implies a lifetime problem, and it is really a locking one.

```js
function transact(project, build, undoLabel) {
  let committed = false;
  project.lockedAccess(() => {
    committed = project.executeTransaction(build, undoLabel);
  });
  return committed;
}
```

`lockedAccess` takes a **synchronous** callback, so fetch every reference before
calling it and do only action-building inside.

## 4. Per-clip in/out inside one transaction does not work

Setting in/out on a master clip and overwriting, repeated per clip in a single
CompoundAction, does **not** give each clip its own range. Probe asking for
1s / 2s / 3s returned **1.001s / 1.001s / 1.001s** — every clip inherited one range.

The timeline looks fully populated and only the ranges are wrong, so this fails
silently. **Use `createSubClipAction` per range instead** (`STRATEGY.SUBCLIP`,
now the default).

## 5. Subclip durations come back one frame short -- *withdrawn*

This said `createSubClipAction` returned one frame less than requested (25/50/25
asked, 24/49/24 placed) and that adding one source frame to the out point fixed
it. **The measurement was real; the conclusion was wrong.**

It was taken when out points were built with `createWithSeconds`, and a float
frame boundary sits a tick below the true one about 3% of the time (see 5b), so
Premiere aligned down and the frame vanished. The nudge was compensating for the
float, not for anything Premiere does.

The tell was the residual this section used to record: with the nudge, the
*final* clip of a plan came back one frame **long** while every earlier clip was
exact. Earlier clips were long too -- the next clip's overwrite trimmed them, so
only the last one had nowhere to hide.

Rebuilt with exact ticks and no nudge: 29 clips, zero gaps, zero position or
duration mismatches. `NUDGE_FRAMES` is 0 in `panel/src/apply.js` and the reasoning
is recorded there.

**The general lesson:** a correction that makes the symptom go away is not the
same as a diagnosis. This one survived because it was measured honestly and the
number it produced was right -- but it was fixing the wrong layer, and it left a
residual that nobody could explain for weeks. An unexplained residual is a
standing signal that the model is wrong.

## 5b. Time values built from seconds land a tick short

`TickTime.createWithSeconds()` takes a double, and a frame boundary expressed as
a double is often a hair below the real value: 187/30 is 6.233333333333333, not
6.2333... exactly. Measured over 20,000 frames, the conversion falls a single
tick short on roughly 3% of them at 30, 59.94 and 29.97. One tick below a frame
boundary is a whole frame below it once Premiere aligns, which shows on screen as
black between two shots.

`createWithTicks(string)` takes exact integers, and the tick rate -- 254016000000,
readable at runtime from `TickTime.TIME_ONE_SECOND.ticks` -- is chosen so that 24,
25, 30, 50, 60 and their 1001-based NTSC cousins all divide it exactly. So every
frame boundary at every broadcast rate has an exact integer tick count, and there
is no reason to go through a float at all. `panel/src/timebase.js` does the
arithmetic in BigInt and hands over the string.

## 5c. The sequence rate has to match the footage, not the recipe

This one is not a Premiere quirk, it is a design mistake that was in this repo: a
recipe named a sequence rate (`social-short` says 30) and the engine used it
whatever the footage was. Feeding 59.94 rushes into a 30.000 sequence means a
clip chosen as a whole number of source frames is a *fractional* number of
sequence frames -- the ratio is 1001/2000 -- so the plan rounds one way, Premiere
rounds the other, and clips land a frame out. A build of 29 clips came back with
five one-frame gaps and twelve position mismatches.

`choose_timebase()` now keeps the recipe's rate only when every source lands on it
exactly, and otherwise takes the rate the footage is actually in, warning that it
did. `holds_exactly()` is the test: the ratio between the two rates has to be a
whole number.

## 5d. A freeze frame is usually a decoder, not an edit

Reported as a bug in the assembly: *"when they don't have enough length for the
slot designated for them it's just showing a freeze frame of the final frame."*
The plan was correct, the verifier was clean, and no clip's slot exceeded its
material. What was happening is that Premiere holds the last frame it managed to
decode -- and at a cut, that frame is the outgoing clip's final one, which is
exactly what the report describes.

Measured on the footage in question (Sony 4K 59.94p 10-bit 4:2:2 HEVC, ~97 Mbps),
decode only, nothing else running:

| | Speed | vs 59.94p real time |
|---|---|---|
| Software | 52 fps | 0.86x |
| VideoToolbox | 80 fps | 1.34x |
| 1080p ProRes Proxy | -- | **33.5x** |

(Whole file, idle machine. An earlier set of numbers here was taken while the
proxy transcode was still running and was quietly depressed by it -- worth
checking what else is on the CPU before trusting a benchmark.)

Two things worth keeping:

**Hardware decode was available and was still not enough.** The first guess was
that an M1 Pro cannot hardware-decode 4:2:2 HEVC. VideoToolbox accepted the file,
so that was wrong -- but 1.34x leaves no headroom once Premiere also has to seek,
scale, composite and paint. "Is it accelerated?" was the wrong question; "how
much headroom is there?" was the right one.

**Keyframe spacing matters as much as bitrate.** The source has one keyframe per
second (measured: 6 in 360 frames), so displaying a frame 40 frames into a GOP
means decoding all 40. Every cut point that is not a keyframe pays this. ProRes
Proxy is all-intra -- 120 keyframes in 120 frames -- so seeking costs nothing,
which matters far more for an assembly of thirty cuts than the raw decode rate
does.

`needs_proxy()` in `engine/autoedit/probe.py` tests all three conditions rather
than any one of them: long-GOP codec, heavy chroma or bit depth, and a large
frame. Proxies at ~37 Mbps cost roughly 5 MB per second of footage.

**Note the trap for the next person:** attaching a proxy via `attachProxy()`
changes nothing on screen until the editor turns on Toggle Proxies in the program
monitor, and there is no API for that toggle. Attaching silently and saying
nothing looks identical to the proxy not working.

## 5e. A subclip reports the same media path as its master

`getMediaFilePath()` on a subclip returns the path of the file the master points
at. So an index built as `index.set(normalizePath(path), item)` while walking the
project keeps whichever item it happened to walk LAST -- and in a project that
has been built into a few times, that is a subclip.

The consequence was severe and completely silent. `createSubClipAction` was then
called on a subclip, and its in/out points are relative to *that subclip's* start
rather than to the media. Ranges compounded on every build, marched past the end
of the file, and Premiere played the media's last frame, held, for the clip's
entire duration. One clip in the reported job survived: the only one whose in
point was 0.000, because zero compounds to zero.

**Nothing in the pipeline could see it.** The plan was internally consistent, the
durations were right, the positions were right, and the post-build verifier
passed clean -- it checks where clips are and how long they are, and this defect
changes neither. The first evidence came from exporting the sequence and running
`freezedetect` over the file, then matching the frozen frame against the source
numerically.

There is no `isSubclip()` on `ClipProjectItem`, and `getContentType()` only
separates MEDIA from SEQUENCE. `findItemsMatchingMediaPath(path, ignoreSubclips)`
exists but is an instance method on `ClipProjectItem`, which is awkward when the
thing you are trying to find IS the item. The available discriminator is the
name: an item imported from disk carries its filename; a subclip carries whatever
it was christened. `isMasterFor()` in `panel/src/plan.js` uses that, and
`createSubclips` now refuses to run against a non-master rather than producing a
frozen picture.

**The lesson for the verifier:** it checked geometry and called that "verified".
Geometry was never wrong. Any check that cannot see content will pass a sequence
made entirely of stills.

## 5f. Cameras write a rotation flag instead of rotating pixels

Every clip in the reported job was vertical, and every one of them was stored
landscape: 3840x2160 with `rotation=90` in a Display Matrix side-data entry.
ffprobe's `width`/`height` are the raster on disk, not the picture anyone sees,
and reading them directly meant:

- "Match source" produced a **landscape** sequence for vertical rushes;
- scale-to-fill computed `max(1080/3840, 1920/2160)` = 88.9% and centre-cropped,
  zooming into a picture that was already exactly the right shape (the correct
  answer is `max(1080/2160, 1920/3840)` = 50%, an exact fit with no crop);
- proxies came out **608x1080** rather than 1080x1920, because `scale=-2:1080`
  scales the height, and ffmpeg had already applied the rotation on decode.

`MediaInfo.display_width` / `display_height` swap the axes when the flag is a
quarter turn, and everything downstream reasons about those. The stored values
stay available, because the difference is exactly what you want to see when
something looks wrong.

Note the two places rotation hides: modern files use the Display Matrix side data
(`rotation: 90`), older ones a `rotate` tag. Both turn up in real rushes.

**A cache lesson too.** The first fix did not take, because proxy filenames were
keyed on the source path, size and mtime -- none of which change when the *rule
for building the proxy* changes. The old 608x1080 files were happily reused. The
transcode recipe is now part of the key. Any cache of derived artefacts needs the
deriving code's identity in its key, or a fix silently fails to apply.

## 5g. Premiere 26 only looks for plugins system-wide

The panel disappeared from Window > UXP Plugins with no error, no entry in the
menu, and every file still present and byte-identical to source. Nothing in the
UXP log mentioned the plugin at all, which was the clue: Premiere was not
rejecting it, it was never finding it.

`upic 2.6.0` logs exactly where it looks, and both places are SYSTEM paths:

    upic::Failed to read plugin info file:
        /Library/Application Support/Adobe/UXP/PluginsInfo/v1/premierepro.json
    upic::Loading plugins from system fallback plugins folder:
        /Library/Application Support/Adobe/UXP/Plugins/External
    upic::Number of plugins added from system's fallback: 0

Note the absence of `~`. Earlier versions read the per-user folder, which is
where `install.sh` had always installed, and it worked for months. When Premiere
updated, everything kept working right up until it silently did not.

`/Library/Application Support/Adobe/UXP` is `root:wheel`, so installing there
needs sudo -- which is exactly why the per-user path was chosen originally. It is
not a good enough reason for a plugin nobody can find.

`panel/install.sh` now installs to the system folder, asking for sudo when it
needs to, and mirrors into the per-user folder as well so an older Premiere still
finds it.

**The diagnostic worth remembering:** when a plugin vanishes, check whether the
host is *rejecting* it or *not seeing* it. A rejection leaves an error; not
seeing it leaves silence, and silence sends you looking at your own code. The
`upic::` lines say which, and where it looked.

    grep 'upic::' ~/Library/Logs/Adobe/Adobe*Premiere*/UXPLogs_*.log | tail -8

## 5h. A song is not a metronome

Cut placement used to be arithmetic: `round(k * beat_interval * fps)` from beat
zero. That is exact, and exactly wrong, because a song's spacing drifts. Measured
on a click track accelerating from 100 to 130 BPM over twenty seconds, an even
grid sits a median 70ms from the real beats and a worst 546ms -- more than a beat
adrift by the end.

`track_beats()` now uses the fitted grid only as a starting guess. Each predicted
beat is pulled to the nearest real onset when one is within 28% of the current
interval, the interval is nudged toward the spacing actually observed, and the
next prediction runs from where the music WAS rather than from where arithmetic
said it should be. Errors stop compounding because nothing is computed from beat
zero. Same track: median 11ms, worst 65ms, and the right number of beats.

Two things worth keeping:

**`beat_interval` is now measured, not derived.** With tracked beats the spacing
genuinely varies, so `60/bpm` is the wrong number to compute with. `bpm` stays
as the headline for reporting.

**Subdividing uses the midpoint of two ACTUAL beats.** "Twice per beat" adds a
cut halfway between each pair, which stays correct as the tempo moves -- a fixed
half-interval offset would not.

The verification worth copying: measure cuts against the WAVEFORM, never against
the grid the engine detected. Every earlier check here was self-referential and
a wrong grid passed all of them. End to end on a real track, the planner lands a
median 4.4ms from a tracked beat -- a quarter of a video frame.

## 6. Several APIs are synchronous despite the async house style

`getTrackItems()`, `getComponentCount()`, `getComponentAtIndex()` return values
directly, not Promises. `await` on them is harmless but misleading.

## 7. Methods that are not where you would expect

Both were caught by type-checking against `@adobe/premierepro`, before ever
running the code:

- `findItemsMatchingMediaPath` is an **instance method on `ClipProjectItem`**, not a
  static on `ProjectItem` — so it cannot answer "is this file already in the
  project?". Walk the bin tree with `FolderItem.cast` / `ClipProjectItem.cast` and
  compare `getMediaFilePath()` instead.
- `createSetInOutPointsAction` is on **`ClipProjectItem`**, not `ProjectItem`. Cast first.

## 8. Code changes need a full Premiere restart

Closing and reopening the panel does **not** reload changed JavaScript — `require`
caches modules for the life of the process. Every code change costs a restart
(~90s). The UXP Developer Tool's "Load & Watch" is worth installing for
iteration; it was not needed to get this working.

## 9. Transcript import format is undocumented and unresolved

`Transcript.importFromJSON` rejects `{ language, segments: [{start, end, text}] }`
with `Failed to parse input string into JSON`. The expected schema is not
documented.

**Next step:** transcribe any clip in Premiere (Window → Text → Transcribe), then
run the self-test. The `transcript/schema-discovered` check calls
`Transcript.exportToJSON` on it and dumps the real shape into the report.

**Worked around for subtitles.** The engine writes a standard `.srt` beside the
plan instead, which every platform and client already reads. It does not depend
on this API being solved.

**Why solving it still matters:** it is the only remaining route to captions on
the timeline. See 9b.

## 9b. There is no API that puts an item on a caption track

Read the shipped definitions rather than guessing, and the answer is flat.
`@adobe/premierepro` 26.3 declares:

```ts
export declare type CaptionTrack = {
  createSetNameAction(name: string): object;
  setMute(mute: boolean): Promise<boolean>;
  getMediaType(): Promise<Guid>;
  getIndex(): Promise<number>;
  isMuted(): Promise<boolean>;
  getTrackItems(trackItemType: number, includeEmptyTrackItems: boolean): [];
  readonly name: string;
};
```

Read, rename, mute. Nothing that adds anything. And every editing action that
places media takes video and audio indices only:

```ts
createInsertProjectItemAction(projectItem, time, videoTrackIndex, audioTrackIndex, limitShift)
createOverwriteItemAction(projectItem, time, videoTrackIndex, audioTrackIndex)
```

`Constants.MediaType` has `DATA`, and it appears in exactly one editing call --
`createRemoveItemsAction`. **Captions can be removed programmatically and not
created.** Importing the `.srt` puts it in the project; the drag to the track is
the editor's, and no amount of API archaeology changes that.

Two routes remain, and both are blocked on something outside the code:

1. **Premiere's own caption generation**, which needs a transcript inside
   Premiere — so it needs finding 9 solved. `exportToJSON` will hand over the
   real schema from any clip Premiere has transcribed itself, and the build now
   probes for that automatically rather than waiting for a self-test run.
2. **Burned-in text**, which the panel could already do: `insertMogrtFromPath`
   plus `setMogrtFields` is proven and in use for brand graphics. It needs a
   subtitle `.mogrt` template, and there is none in the brandkit. That is an
   asset someone has to author in After Effects; it cannot be written here.

## 10. An empty `<input type="number">` renders the literal string `nan`

Not empty, not the placeholder -- the four characters `nan`, in the field, looking
like a value the editor typed. The Length field for a music chunk starts empty on
purpose, because empty means "follow the edit", so it showed `nan` on every load.

Number inputs that always hold a value are fine; it is specifically the empty
state. Use `type="text"` and parse for any field that may legitimately be blank:

```js
function parseSeconds(text) {
  const value = Number(String(text ?? "").trim());
  return Number.isFinite(value) && value > 0 ? value : 0;   // "nan" -> 0
}
```

`dataset` is also unreliable on UXP elements -- keep per-field flags in your own
state object rather than on the DOM node.

## 10b. An `<img>` must be in the document, and visible, before `src` is set

A thumbnail grid showed empty boxes through four wrong diagnoses. Every layer
was measured working on the same machine: the JPEGs were good pictures, the
storage API read them out of the work directory, a 27KB data URI came back
intact, `replaceWith` and every other DOM method probed was present, and the
self-test's own probe rendered a 33KB data URI at `naturalWidth: 320`.

The difference between the probe that worked and the panel that did not was
statement order.

```js
// selftest.js renderProbe -- works
document.body.appendChild(img);
img.src = src;

// the panel -- silently blank
img.style.display = "none";
card.appendChild(img);
// ...later
img.src = uri;
img.style.display = "block";
```

An `<img>` that is not laid out when `src` is assigned never decodes, and
unhiding it afterwards does not start it. An earlier version failed the same way
for the mirror reason: it set `src` before the element was in the tree at all.

There is **no error** in either case. No exception, no `error` event, no console
line. The element simply stays blank, which is indistinguishable from a missing
file, a permissions refusal or a bad encoding -- and those were the first three
diagnoses.

**Append first, then assign `src`.** If a placeholder is wanted, keep it as a
separate element and hide it from the image's own `load` handler.

**The general lesson, and it is the same one as 5d:** every layer passing in
isolation is not evidence that the composition works. Four fixes went out against
layers that were already fine, because each was tested the way it was reasoned
about -- alone. The bug lived in the order two working things were combined in,
which no single-layer test can see. What broke the deadlock was writing the
panel's actual runtime state to a file and reading it, rather than reasoning
about what it must contain.

## 10c. `atFrame` is not a slot: a music bed starts at frame 0 too

Swapping one shot replaced the entire timeline with a twelve-second clip.

Shot swaps were held in a `Map` keyed on the timeline entry's `atFrame`. That is
unique **on a track**, which is what the comment justifying it said -- and the
map was global. A music bed sits at frame 0 on the audio track, and the first
picture sits at frame 0 on V1, so one swap matched both.

The bed was 719 frames (11.995s). Handed a video source and clamped so the range
would fit inside it -- `min(66.9257, 78.08 - 11.995)` -- it became 66.085 to
78.080, the tail of the file, and overwriting that at frame 0 buried all 25
clips behind it.

Every number in the failure was reachable from the receipt, which is the only
reason it was found: the placed item's name encoded a range appearing in neither
the plan nor the library, `78.08 - 11.995 = 66.085` matched the clamp exactly,
and the plan held exactly one 719-frame entry -- the bed.

Two fixes, both needed. Slots are keyed by frame **and** track. And the strip
lists picture slots only: a music bed has no frames to choose between, and
"swap this shot" can never mean it.

**The general lesson:** an identifier that is unique *within* a scope is not an
identifier. I wrote "unique on a track" in the comment defending the choice, and
then used it across all tracks -- the reasoning was correct and the code did not
follow it. The verifier could not catch it either, because it checked position
and duration and never which footage landed where; that is now checked too, and
it is what named the culprit.

## 10d. CLIP similarity is not a threshold, and CoreML is not free

Two measurements from wiring a vision model to the shot library, both of which
contradicted the obvious design.

**An absolute similarity floor does not work.** Against the real library,
phrases describing what the footage contains scored 0.268-0.298 and phrases
describing what it does not scored 0.195-0.215. The bands separate, but both
move with the footage and with the wording, so there is no number to write in a
constant: set it at 0.25 and a differently-worded prompt matches nothing, set it
at 0.20 and everything matches.

What works is competition. Each shot is assigned by softmax over the beats *and*
a handful of generic distractor phrases -- "a photograph", "people in a room" --
and a beat that cannot beat those on its own best shot is unmatched. It
calibrates itself per library and per prompt.

Measured both ways on 29 shots of a futsal court: three beats describing a
restaurant matched **zero** shots, and three describing the footage that is
actually there matched 11, 16 and 1 at p=0.98, 0.91 and 0.64.

**CoreML claims the model and then fails.** `CoreMLExecutionProvider` reports
support for 1324 of the graph's 2358 nodes and happily embeds images through the
partitioned result -- then fails outright on the text tower the moment more than
one phrase is embedded: *"Unable to compute the prediction using a neural network
model"*. Half the model working is the trap, because the failure arrives later
and on the other half. CPU runs the whole thing at 0.19s a still, which is
*faster* than the partitioned CoreML path measured, and correct.

**The general lesson**, which is 5d and 10b again from a third direction: the
number that looks like a threshold usually is not one, and a provider that
advertises support has not promised correctness. Both were caught by running the
thing against real footage rather than reasoning about it.

## 11. `SourceMonitor` is the way to play audio, because UXP cannot

There is no `<audio>` element and no Web Audio API, so a panel cannot play a
sound. `ppro.SourceMonitor` gives you Premiere's own monitor instead, and it is
better than anything a panel could rebuild:

```js
await ppro.SourceMonitor.openFilePath(absPath);   // does NOT import into the project
await ppro.SourceMonitor.play(1);
const position = await ppro.SourceMonitor.getPosition();   // TickTime -> .seconds
```

All three work as documented on 26.3.2 -- a pleasant surprise given the rest of
this file. `openFilePath` not importing is the useful part: auditioning five
tracks to choose one does not leave four of them in the bin.

`getPosition()` returns the playhead, not the in/out points, and there is no API
for reading those -- so "park the playhead and read it back" is the interaction
the API supports, not merely the one we chose.

## 12. Japanese needs NFC normalisation everywhere a name is compared

macOS hands back composed forms from some APIs and decomposed from others. With
ASCII names the two are byte-identical and nothing notices; with `ダンス.MP4` a
`===` between them fails, and an imported clip looks missing — so it is imported
again on every build, or its subclip is never found.

`.normalize("NFC")` on both sides of every filename and path comparison, and on
`relPath` where the helper writes the index. The panel's font stack also needs
CJK fallbacks: `"Adobe Clean"` has no Japanese coverage, so every label is tofu.

## 12b. CLIP reads Japanese without complaining, and understands none of it

CLIP's tokenizer is byte-level BPE, so it accepts Japanese, round-trips it
cleanly, and returns a vector. The vector means nothing. Four phrases with quite
different meanings, measured against the same 29 stills:

| | サッカーをしている子どもたち | ゴールキーパー | 料理の皿 | 建物の外観 |
|---|---|---|---|---|
| similarity | 0.219 | 0.218 | 0.218 | 0.222 |

A spread of **0.0016**, against **0.0370** for the same four ideas in English.
That is not a weak signal, it is no signal — and because nothing errors, a
Japanese prompt produced a confident edit built from arbitrary shots. "A plate
of food" won two shots of a futsal court.

The fix is `sentence-transformers/clip-ViT-B-32-multilingual-v1`, a text encoder
**distilled onto CLIP ViT-B/32's own text space**. That property is what makes
it affordable: the image tower is untouched, so every cached `.vec.npy` stays
valid and no library is re-embedded. 50 languages, Apache 2.0, 135MB quantized.
It lifts the Japanese spread to 0.0319.

Two things to know about it:

- **It is shipped as safetensors plus an ONNX transformer, not one file.** The
  ONNX is the encoder only; the 768→512 projection that lands it in CLIP's space
  is a separate 1.6MB matrix. Mean-pool over the attention mask, then project.
  Pooling over the padding too drags every short phrase toward the same vector —
  which is the exact failure being fixed, reintroduced by hand.
- **The quantized builds are per-architecture.** `model_qint8_arm64.onnx` is not
  portable to Intel; pick by `platform.machine()`.

It closes most of the gap but not all of it. The encoder preserves ranking
across languages and compresses the *distances* unevenly, so a strong beat
sweeps a pool that its English equivalent would have shared. Same footage, same
intent: English beats split 4/17/7, Japanese went 0/28/0. Japanese prompts find
the right footage; they discriminate between beats less finely.

## 12c. A Japanese opener trails the shot it introduces

English openers lead their clause — "opens with the shop front" — so everything
before one is preamble and gets dropped. Japanese puts the marker last:
「店の外観から始まり」 is the same sentence, and the shot is the part *before*
から始まり.

Parsing both the same way threw the opening shot away every time, silently. The
editor named 店の外観 and it was simply not in the edit. Only the clause the
marker trails is a shot; what precedes *that* is the preamble, and the last
connective before the marker is where it ends.

## 12d. A protected cut has to survive the frame snap that follows it

Cutting a montage of people talking put boundaries inside words -- three in one
plan, one of them inside a single character. Moving the boundary to the edge of
the word is the obvious fix and it does not work: `append_cuts` snaps every
source point to the source frame grid afterwards, **rounding to nearest**, so a
boundary sitting exactly on a word's edge rounds straight back in. Measured: the
cut was moved to 1.1600, the word began at 1.1600, snapping at 59.94fps produced
1.1678. Eleven cuts were reported protected and ten were still mid-word.

The boundary has to land a clear margin outside -- 50ms covers one frame at
every rate this tool supports, and it is spent on silence either way.

That fixed three of eleven. The rest needed a second idea entirely: **continuous
speech does not divide into sentences a clip can fit inside.** A coach shouting
instructions produced utterances of 4.7 and 5.9 seconds against takes of 1.6, so
"keep the whole sentence" had nowhere to put the boundary and gave up. Cutting
between two words inside a long sentence is ordinary editing; cutting through
the middle of one never is. Whole sentence first, nearest inter-word gap second.

**Eleven of twenty down to two.** The last two are not a bug: Whisper sometimes
reports a run of words with no silence between them at all -- one ends exactly
where the next begins -- and then there is no moment in that stretch that is not
inside a word. That count is reported rather than hidden, because a protection
pass that quietly fails is worse than one that says what it could not do.

## 13. What works

- Panel loads, renders, and is interactive
- Media resolution by path, importing what is missing
- Sequence creation, clip placement at frame-exact positions
- **One ⌘Z removes an entire assembly** — transaction grouping is correct
- Post-build verification reads the timeline back and reports gaps or drift
