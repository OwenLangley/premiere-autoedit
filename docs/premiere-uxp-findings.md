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

## 5. Subclip durations come back one frame short

Passing a range verbatim to `createSubClipAction` yields one frame less than
requested: asking for 25 / 50 / 25 frames produced 24 / 49 / 24, positions exact.
Adding one frame of the **source** timebase to the end corrects it.

**Residual, uncharacterised:** with that correction the *final* clip of a plan came
back one frame **long** (44 requested, 45 placed) while every earlier clip was
exact. No gap and no overlap results, since it is last. The post-build verifier
reports it rather than hiding it.

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
