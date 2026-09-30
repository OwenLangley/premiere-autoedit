# premiere-autoedit

**English · [日本語](README.ja.md)**

**A tool to automate rough cuts from plaintext descriptions or reference videos.**

Describe the video in a sentence. The tool watches every clip you give it, finds
the shots worth using, cuts them to the speech or to the beat, and builds a real
sequence in Premiere — with a reason attached to every clip, so you can disagree
with any of it.

```
"a 15 second promo of the kitchen, fast cuts"
        │
        ├─ reads 40 minutes of rushes          shots, faces, motion, sharpness
        ├─ throws away the unusable            black, frozen, soft, shaky
        ├─ picks and orders what is left       to your words, or to a beat
        └─ builds a sequence in Premiere       new timeline, nothing overwritten
```

It is not trying to replace editorial judgment. It does the mechanical part — the
part that costs an afternoon and no thought — and it explains every decision so
you can overrule it. Every clip on the timeline carries why it was chosen and how
confident the tool was; every shot has alternates you can swap in with a click.

**It does not:** colour grade, mix audio, write your script, or touch a sequence
you already have. It always builds a new one.

**You will need to finish the edit.** A rough assembly that is 80% right is worth
an afternoon. It is not worth publishing.

---

## What you need

| | |
|---|---|
| **macOS** | Apple Silicon or Intel. Nothing here has run on Windows. |
| **Premiere Pro 26.3 or later** | 26.0 and 26.2 are missing calls the panel makes. Check under About Premiere Pro. |
| **~4 GB free** | for the speech and shot-recognition models, downloaded once on first use. |
| **Homebrew** | for ffmpeg and Python. [brew.sh](https://brew.sh) |

**Your footage never leaves your machine.** Transcription and shot recognition
both run locally. The only things that touch the network are the one-off model
download and, if you paste a link to a reference video, fetching that video.

## Install

```bash
brew install ffmpeg python@3.11
git clone https://github.com/OwenLangley/premiere-autoedit.git
cd premiere-autoedit
./setup.sh ~/Footage
```

`~/Footage` is the top of your rushes tree, not one shoot — it is the only path
you have to decide, and you can change it later in the panel without re-running
anything. Setup installs three things: the Python engine, the Premiere panel, and
a background helper. It asks for your password once, because Premiere 26 only
loads plugins from `/Library`.

Then **quit Premiere and reopen it**, and open **Window → UXP Plugins → AutoEdit**.

`./setup.sh --check` says what a machine is missing without changing anything.

> **The background helper is not optional.** A Premiere panel cannot start a
> process, so the panel writes a request and the helper runs the engine. Without
> it the panel looks perfectly healthy and **Create edit does nothing at all**,
> with no error anywhere. `--check` looks for it specifically.

## Set it up once

In the panel, under **Setup**:

1. **Jobs folder** — pick the one setup printed (`~/Desktop/AutoEdit-jobs`). This
   click is the only step that cannot be scripted: a plugin gets folder access
   only through a picker you drive yourself.
2. **Media root** — your footage. Already set if you passed it to `setup.sh`.
3. **Music folder** *(optional)* — wherever your library lives. Nothing to do
   with where the footage is.
4. **Reference folder** *(optional)* — videos you might want to cut like.

Setup collapses to one line once it is done and stops taking up the panel. The
panel is also in Japanese: **Setup → Panel language**.

## Make a cut

1. **Describe it.** One sentence, in plain English or Japanese:
   *"a 15 second promo of the kitchen, fast cuts"*. Leave the name blank and the
   edit is named by the date and time.
2. **Pick your clips** under **Clips**. Select all is fine — the tool discards
   what it cannot use and tells you what it discarded.
3. **Press Create edit.** A progress bar tracks it: reading the footage,
   recognising the shots, matching, writing. On a few minutes of 4K expect around
   five minutes the first time, and seconds on anything it has already seen.
4. **Read the plan.** It appears under **Plan** with a one-line summary, the
   warnings, and a strip of every clip. Click a block to see the other shots that
   could have taken its place, and swap it if you disagree.
5. **Press Build sequence.** A new sequence appears in Premiere. Each stage is
   one undo step, so ⌘Z is never "the last of 200 edits".

Nothing you have open is touched, at any point.

## Telling it what you want

Three ways, and they stack. The description is the one to reach for first.

### In words

The sentence you type is read for what it says about the edit:

| You write | It does |
|---|---|
| "15 seconds", "1分30秒" | sets the length |
| "tiktok", "vertical", "縦動画" | sets the shape |
| "fast", "punchy", "slow" | sets the pace |
| "promo", "podcast", "reel", "プロモ" | picks the kind of edit, which sets the thresholds behind it |
| "with subtitles", "字幕" | writes an .srt alongside |
| "opens with the storefront, then the chef" | becomes the running order, shot by shot |

Anything it recognised is echoed back in the line beside **Show settings**, so
you can see what landed before you press anything.

### Like this video

**Cut it like** takes a reference video — pick one from your reference folder or
paste a link — and copies its shot count, shot lengths and order. The reference
is measured and thrown away; no frame of it reaches your timeline.

It matches what each shot *shows* as well, when your footage can serve it, and
says so when it cannot. **Rhythm only** asks for the timing alone. If the
reference's music is in your music library, the edit is cut to that track.

### With music

Under **Music**, *Automatic* finds a single audio file sitting with your rushes;
otherwise pick a track from your music library. Cuts land on its beat.

A trend is usually a moment in a song rather than its opening, so **Audition**
opens the track in Premiere's own Source Monitor and **Use playhead as start**
takes the drop you found by ear. Leave the length empty and the bed follows the
edit, so it never hangs past the last frame.

### By hand

**Show settings** opens everything the description would have set — shape,
length, pacing, cut rate, frame rate, spoken language — plus subtitles, keeping
whole sentences, and cutting silence. The line above it always reads back what is
actually set.

The editorial policy underneath — how much silence is a pause, when a filler word
gets cut, how long a shot may run — lives in `engine/recipes/*.yaml` as data you
can edit, not in code. See the [cutting guide](docs/cutting-guide.md).

## When something goes wrong

**Report a problem**, bottom right, then **Collect a report to send**. It writes a
zip to your Desktop with both logs, this build's version and the last eight jobs.
`./report.sh` does the same from a terminal if the panel will not open — which is
the case you need it most. It contains media *filenames*, folder paths, what you
typed and any subtitle text; no video, no audio, no credentials.

That drawer is also where the log lives, and it counts anything that went wrong
while it was closed.

**Check for updates** is top right. It says where you stand rather than what it
does — *Up to date*, or *Update available* — and checking installs nothing, so
pressing it to find out costs nothing. Installing ends in **Restart Premiere**,
which is the only step it cannot do for you: UXP loads the panel once at startup.

Three failures worth naming:

- **Create edit does nothing.** The helper is not running. `./setup.sh --check`.
- **Every shot was rejected.** The quality thresholds are wrong for your footage,
  not the other way around. Lower `min_sharpness` and `min_brightness` in the
  recipe's `visual:` block.
- **A drive is unplugged.** Not an error — that folder drops out of the scan, the
  rest keeps working, and the panel names what is missing.

## Going deeper

| | |
|---|---|
| [docs/cutting-guide.md](docs/cutting-guide.md) | Everything about steering the cut: silent footage, music and beat detection, references, recipes, frame rate, multiple drives, the command line, and working in Japanese. |
| [docs/how-it-works.md](docs/how-it-works.md) | The architecture, the EditPlan contract, the editorial rules the engine enforces, what is tested, how long a job takes, and what is known to be broken. |
| [docs/premiere-uxp-findings.md](docs/premiere-uxp-findings.md) | Everything measured about Premiere's UXP API, several points of which contradict Adobe's own documentation. Read this before touching `panel/src/apply.js`. |
| [docs/setting-up-a-new-machine.md](docs/setting-up-a-new-machine.md) | What the first run costs, and what to decide before putting this on someone else's Mac. |
| [CONTRIBUTING.md](CONTRIBUTING.md) | How to run both test suites, and what will fail review. |

## Licence

MIT — see [LICENSE](LICENSE). Use it, change it, ship it in something you sell;
keep the copyright line.

It is offered with no warranty and no support promise, and that is worth reading
literally. This is one person's working tool, developed against one pair of eyes
and one machine: **macOS, Premiere Pro 26.3+**, developed and measured on Apple
Silicon. Nothing here has run on Windows, and the panel's findings are measured
against Premiere 26.3.2
specifically. Plenty in
[docs/premiere-uxp-findings.md](docs/premiere-uxp-findings.md) will be wrong on a
future build — that file exists to be re-measured, not trusted.

The [known defects](docs/how-it-works.md#known-defects) are honest and not exhaustive.

### What it uses, and what those are licensed under

Nothing here is redistributed; all of it is installed or downloaded on the
machine that runs it.

| | |
|---|---|
| **ffmpeg** | every probe, decode, audio extract and proxy. Installed by you via Homebrew, under its own licence (LGPL or GPL depending on how your build was configured). This project only ever invokes the binary. |
| **faster-whisper**, **PyYAML**, **jsonschema**, **numpy**, **onnxruntime** | MIT or BSD |
| **huggingface_hub** | Apache-2.0 |
| **yt-dlp** | Unlicense. Used only to fetch a reference video you point it at. |
| **CLIP ViT-B/32** (`Xenova/clip-vit-base-patch32`) and the multilingual text encoder (`sentence-transformers/clip-ViT-B-32-multilingual-v1`) | downloaded from Hugging Face on first use, ~90 MB, under their own model licences. |
| **`@adobe/premierepro`** | TypeScript definitions, a dev dependency for `npm run typecheck`. Not shipped in the panel. |

**A reference video is yours to be entitled to.** The tool will download one from
a link and measure its cutting; what you are allowed to download, and what you
then do with an edit shaped by it, is between you and whoever owns that video.

### Not affiliated with Adobe

Adobe, Premiere Pro and UXP are trademarks of Adobe Inc. This is an independent
project, not endorsed by or connected to Adobe in any way. It is an unsigned
third-party plugin that you install yourself, and it writes to a Premiere project
you have open — read [what the panel does to a project](docs/how-it-works.md#panel-behaviour) before pointing it at
work that matters, and keep backups you would be happy to fall back on.
