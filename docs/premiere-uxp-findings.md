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

## 13. What works

- Panel loads, renders, and is interactive
- Media resolution by path, importing what is missing
- Sequence creation, clip placement at frame-exact positions
- **One ⌘Z removes an entire assembly** — transaction grouping is correct
- Post-build verification reads the timeline back and reports gaps or drift
