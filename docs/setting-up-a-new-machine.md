# Setting up a new machine

One command, then restart Premiere:

```bash
./setup.sh ~/Desktop/AutoEdit-jobs ~/Footage ~/Music/Library
```

To see what a machine is missing without changing anything:

```bash
./setup.sh --check
```

## The four parts, and why each matters

| | What breaks without it |
|---|---|
| **ffmpeg** | Everything. Every probe, audio extract and proxy goes through it. |
| **Engine venv** | No analysis. Holds the Python code and Whisper. |
| **Panel** | Nothing appears under Window → UXP Plugins. |
| **Helper** | The panel loads, the form works, and **Create Edit does nothing at all.** |

That last one is the trap. UXP cannot start a subprocess, so the panel writes a
request file and a background watcher runs the engine. When the watcher is not
running there is no error anywhere -- the request just sits on disk. `--check`
looks for it specifically.

## What the first run costs

- **~3 GB Whisper model download.** Once per machine, then cached in
  `~/.cache/huggingface`.
- **Proxy building** for footage Premiere cannot play smoothly. Runs in the
  background as media is indexed and is cached by content hash. Budget a few
  minutes per 4K clip and roughly 5 MB per second of footage.

Neither blocks the first plan; both make the first session slower than every one
after it.

## Things worth deciding before you roll it out

**Where the jobs folder lives.** Each editor can have their own on the Desktop,
or you can point everyone at one folder on shared storage. Shared means plans and
receipts are visible to whoever is helping; local means no network dependency.
The helper takes it as an argument, and the panel can change it without a
restart.

**Whether the media root is shared.** Plans store paths *relative to the media
root*, so moving footage to a NAS later is a settings change and nothing else.
That only holds if everyone's root points at the same tree.

**The panel language.** It ships in English and Japanese, switchable in Setup and
remembered per machine.

## Updating an already-installed machine

```bash
git pull && ./setup.sh --check          # then restart Premiere
```

`panel/install.sh` alone is enough if only the panel changed. Only the *first*
install needs an administrator password; after that the plugin folder belongs to
the installing user.

**Premiere must be restarted after any panel change.** `require()` caches modules
for the life of the process, so closing and reopening the panel is not enough --
you get the old code with no sign that anything is stale.
