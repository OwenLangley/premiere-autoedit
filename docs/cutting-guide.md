# Steering the cut

The [README](../README.md) covers the three things that decide most edits: what
you type, a reference video, and the settings behind them. This is the rest —
what each control actually does, what the engine does with silent footage, how
music is chosen, and how to change the editorial policy itself.

It also carries the command-line path. Everything the panel does is the engine
underneath it, and the engine is usable on its own.

## From the command line

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
lists every audio-only file it can see, from two places:

- the **music folder**, set once in panel Setup. It lives wherever you keep your
  library and has nothing to do with where the footage is.
- anything audio-only sitting **with the footage**, labelled as such so the two
  are never confused.

```
~/Music Library/            <- the music folder
  Upbeat/drive.mp3          <- offered as "Upbeat/drive - 2:12"
  Cues/sting.wav

/Volumes/Footage/EP001/     <- the media root, somewhere else entirely
  C1367.MP4  C1371.MP4
  scratch-track.wav         <- "scratch-track - 0:30 (with the footage)"
```

Choosing a folder in the panel writes it to `config.json` in the jobs folder; the
helper re-reads that while running, so it takes effect without a restart. From the
terminal, `helper/install.sh <jobs> <media> [music]` or `--music` on the watcher
does the same thing.

Tracks from the library are marked `library:` in the request, so the helper knows
which root to resolve against rather than trying one then the other -- guessing
wrong there means a promo scored to the wrong track, which nothing catches until
playback. Plans record the track as a `relPath` plus `root: "music"`, never an
absolute path, so the NAS move stays a settings change.

Audio-only is decided by the index's probe, not by extension, so a track exported
as `.mp4` is offered as music rather than turning up in the clip list. The scan is
capped at three folders deep and 400 files; hitting either is reported in the panel
rather than quietly shortening the list.

**Auditioning, and choosing which part of the track to use.** A trend is a moment
in a song -- the drop, the hook, the eight bars everyone knows -- and it is almost
never the opening. Press **Audition** and the track opens in Premiere's own Source
Monitor and plays; find the drop by ear, then press **Use playhead as start**.

UXP cannot play audio at all -- no `<audio>`, no Web Audio -- so this is not a
workaround, it is the only honest way to do it, and it gives the editor Premiere's
waveform and scrubbing rather than something worse rebuilt in a panel. Auditioning
does not import the track: choosing between five would otherwise leave four in the
bin.

```bash
./.venv/bin/autoedit plan --job PROMO01 --recipe promo-silent --visual --media ~/Footage/*.MP4 --media-root ~/Footage --music ~/Music/drive.mp3 --music-start 30 --music-length 20 --out ~/AutoEdit-jobs/PROMO01.editplan.json
```

```
music: 92.0 BPM, 92 beats, confidence 0.53
music: start moved 0.20s to the nearest beat, at 30.20s
music: the music runs 12.1s past the last frame of picture -- extend the edit or shorten the chunk
```

**The start snaps to the nearest beat.** Not a nicety: shot lengths are already
whole multiples of the beat interval, so a bed that begins exactly on a beat
phase-aligns the entire cut grid to what is audible. The move is at most half a
beat, and is only reported when it is big enough to hear -- announcing a 3ms
correction as "moved 0.00s" is noise in a list the editor has to read.
`--no-music-snap` turns it off.

**Length is the one place music beats picture.** Leave it empty and the bed
follows the edit, so it never hangs past the last frame. Set it and that much
music is laid even if it outruns the picture, with a warning saying by how much --
because choosing a chunk is choosing a span of music, and quietly shortening it
would defeat the point of having chosen.

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

## Cut it like this one

**New edit > Cut it like.** Pick a reference video, or paste a link to one, and
the edit takes its shot count, its shot lengths and their order. The reference is
measured and discarded -- no frame of it reaches the timeline.

Verified against a reference built to 0.50 / 3.00 / 0.50 / 2.00 seconds: the edit
came out 0.50 / 2.99 / 0.50 / 2.00, six seconds against six.

Two things are copied and one is not:

- **Rhythm**, always. This is the reliable half.
- **What each shot shows**, when the footage can serve it. Measured on this
  repo's own library: a reference from the same shoot scored 0.842-0.918 against
  the footage and three unrelated videos scored 0.547-0.649, so the floor sits at
  0.75. The band actually seen rides on every job as a warning, because an
  absolute floor is the weak part of this and a wrong one should be visible
  rather than silent.
- **Not the reference's music.** Its cutting rhythm already encodes its tempo,
  and applying that tempo to a different track would be matching the wrong
  thing.

When none of the reference's shots are found in the footage -- which is the
ordinary case for a reference of a different subject -- the rhythm is used on its
own, the shots are filled by quality, and the panel says so. **Rhythm only** asks
for that outright.

A reference with no cuts in it is refused, because there is no pattern to copy.
Both causes are named: a genuine single take, or transitions too soft to detect.

### Reference folders and links

**Setup > Reference folder** nominates where reference videos live, the same way
the music folder does. There is no file picker: UXP has never given this panel
one, and `getEntryWithUrl` on an arbitrary path is documented as refusable.

A pasted link is downloaded by the helper -- not the panel, which has no network
permission at all -- cached under the jobs folder by URL, and fetched at 720p
because the file is measured and thrown away.

**Downloading from YouTube, TikTok or Instagram is generally against their terms
of service**, and `yt-dlp`'s extractors break whenever those sites change, so
this part needs occasional updating. The output is your own footage either way;
whether to use the link field is your call.

### The reference's own music

When Music is left on **Automatic** and a reference is given, the tool listens to
the reference and looks for that track in your music library. If it is there, the
edit is cut to it. An editor cutting to a reference usually wants the reference's
music and already owns the file; finding it again in a hundred-item dropdown is a
lookup a machine can do.

It matches by landmark fingerprinting -- pairs of spectrogram peaks, scored by
how many agree on the *same* time offset. That last part is what survives a
voiceover: noise creates spurious matches too, but they scatter across the track
instead of piling up on one offset. Measured against the real 103-track library
here, with a 30-second excerpt re-encoded as AAC:

| what is over the music | score |
|---|---|
| nothing | 2917 |
| speech at the music's level | 2787 |
| speech at four times it | 2566 |
| heavy pink noise | 2643 |
| only ten seconds of music, clean | 975 |

and, holding out ten tracks and matching each against a library that did **not**
contain it, the highest false match anywhere was **8**. The floor is 100, which
sits in the gap rather than near either edge. A track that is not in the library
produces no match and no music, which is the right answer.

The library is fingerprinted by the helper in the background -- about three
minutes for a hundred tracks, once, cached by content hash.

**That only happens on machines that use references.** Matching needs something
to match against, so indexing for anyone else is pure cost. It starts the moment
a reference is chosen in the panel -- which is minutes before Create is pressed,
while clips are still being picked -- so the library is usually ready by the
time the first job runs. A job that asks for a reference also queues it, as a
backstop for a panel that never sent the message.

Indexing stands aside while a job is being built. It is decode-bound and so is
an edit, and two ffmpeg passes on the same cores make the one somebody is
waiting for slower.

A library that has not been heard yet produces no match, and the plan says so --
"the music library is still being listened to (0 of 103 tracks)". A part-indexed
library can fail to find a match but can never find a wrong one.

The cost is about forty seconds for a hundred tracks, once, and 12MB on disk.
Fingerprints are read back one at a time while matching rather than all at once,
so the memory a match needs does not grow with the library.

## Footage in more than one place

**Setup > Add another footage folder.** Rushes on the desktop and last month's
on an external drive is the ordinary case, and one media root was always a
simplification of it.

Each folder is indexed under its own name -- `media`, `media2`, `media3` -- and
a clip is recorded as that name plus a path inside it. So two cards both holding
`C0001.MP4` stay two different clips: they differ by root, and the clip picker
says which folder each came from. Plans stay free of absolute paths, so the
"move the library and change one setting" promise survives.

**An unplugged drive is not an error.** Its folder is dropped from the scan, the
rest of the library keeps working, and the panel names what is missing rather
than showing fewer clips for no stated reason. Plug it in and the clips come
back.

UXP grants folder access one folder at a time, so each one is its own pick --
there is no widening the first grant to cover a drive.

## Frame rate

**Setup > Show settings > Timing > Frame rate.** "Match the footage" is the
default and does what it always did: the recipe states a rate, and footage that
cannot land on it exactly displaces it, with a warning saying so.

Choosing a rate makes it a delivery spec instead. It is honoured even when the
footage does not divide into it -- "25 for broadcast" is a requirement, not a
preference -- and the mixed-rate warning still names the files that will be
resampled.

Nothing above 60 is offered or accepted. Premiere will not create a sequence
from a preset faster than that; see findings 12f.

## Recipes

Editorial policy lives in `engine/recipes/*.yaml`, not in code, so a producer can
tune it. Five ship:

| Recipe | min_silence | Fillers | Intent |
|---|---|---|---|
| `podcast-2cam` | 0.50s | conservative | Stay conversational. An over-cut podcast reads as artificial. |
| `social-short` | 0.25s | **aggressive** | The one format where cutting `like` / `you know` is correct. |
| `client-promo` | 0.70s | conservative | Music-led. Deliberately does less; rhythm stays with the editor. |
| `long-form` | 0.80s | conservative | YouTube and documentary. People pause when they think. |
| `promo-silent` | 0.50s | conservative | A montage: cuts from the pictures to the beat, so these two rarely come into it. |

A typo in a recipe is rejected rather than silently ignored — a setting that
quietly does nothing is worse than a crash.

**The panel has no recipe control.** The description picks it, from the
`keywords` each recipe's `format` block declares: "a b-roll montage of the
kitchen" is `promo-silent`, "a two camera podcast episode" is `podcast-2cam`.
When one is recognised its name appears in the line beside **Show settings**, so
an editor can see the word landed. There were three ways to say this at one
point — cards, a dropdown and the description — and two have gone, in that
order. The dropdown was also the only one that spoke in thresholds and track
counts rather than in deliverables, which is nobody's way of choosing an edit.

A description that names no kind of edit gets the recipe declaring
`default: true` — `client-promo`, the one that cuts least, because a default
applies exactly when nobody said anything. Only one recipe may claim it, and a
recipe with no keywords is now unreachable; both are tested, since both used to
be settled by whichever filename sorted first. Whichever recipe ran is recorded
on the plan and shown in its details, so a cut can be traced back to the
settings that made it.

## Japanese

The panel runs in Japanese, and Japanese footage cuts properly. Pick the language
in **Setup → パネルの言語**; it is remembered per machine, so a mixed team shares
one install.

**The spoken language is detected, not configured.** Recipes ship as
`language: auto`, Whisper identifies it from the audio, and the panel shows what
it found with a dropdown to correct it. A wrong guess produces a fluent,
confident and meaningless transcript, so low confidence is reported rather than
acted on quietly.

**Filler removal works, and getting there required measuring.** Whisper does not
tokenise Japanese into words:

| Spoken | Tokens returned |
|---|---|
| `えーと` | `えー` + `と、` |
| `うーん` | `う` + `ーん、` |
| `あの、` | `あの、` |

The commonest hesitation sounds are never a single token, so matching one at a
time cannot find them. Fillers are matched across a *run* of consecutive tokens,
joined with nothing for languages written without spaces and with a space
otherwise — one mechanism that covers English "you know" as well.

Conservative removes hesitation sounds only. Aggressive also removes あの, その,
まあ, なんか — real words used as filler, the same judgement that keeps English
"like" out of the conservative set. `うん` and `ええ` are deliberately in neither:
they mean *yes*, and cutting someone's agreement out of an interview is a
different kind of mistake.

Warnings translate too. The engine emits a key and the numbers, not a sentence:

```jsonc
{ "code": "music", "messageKey": "music.stopsEarly", "params": { "shortfall": 0.1 },
  "message": "the music stops 0.1s before the picture does" }
```

so the panel can render 「音楽が映像より 0.1 秒早く終わります」, and a warning with
no translation yet falls back to the English rather than vanishing. An editor who
cannot read a warning is worse off than one who sees none, because they assume
it is fine.

**Not translated, deliberately:** per-clip `reason` strings ("shot 3 of 14,
quality 0.71"). They live in the plan JSON rather than the panel, and translating
them triples the catalogue for no editor-facing gain.

**The Japanese was written by the tool's author, not a native speaker.** Terms
like 尺 and テロップ are where a translation comes out fluent and wrong, which is
harder to spot than obviously broken — worth a colleague reading
`panel/src/i18n.js` before it goes out.
