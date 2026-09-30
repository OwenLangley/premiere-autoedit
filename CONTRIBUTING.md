# Contributing

This is a working tool before it is a project, so the bar here is not ceremony —
it is evidence. Two rules cover most of it:

1. **A change to behaviour comes with a test that fails without it.**
2. **Claims about Premiere are measured, not reasoned.** Adobe's docs and Adobe's
   own samples contradict reality in several places this project has hit; see
   [docs/premiere-uxp-findings.md](docs/premiere-uxp-findings.md). If you find
   something new there, add it with the numbers that showed it.

## Running the tests

```bash
./setup.sh --check                 # what this machine is missing
.venv/bin/python -m pytest engine/tests -q
cd panel && npm install && npm test && npm run typecheck
```

Both suites run on any Mac with the venv built. Nothing in them needs Premiere
open — the parts that do are in the panel's own **Self-test**, which runs inside
Premiere against the real API and writes `/tmp/autoedit-selftest/report.json`.
Run that when you change `panel/src/apply.js` or move to a new Premiere version.

## Where things live

| | |
|---|---|
| `engine/` | Python. All the editorially risky logic, because it is testable off-Premiere. |
| `panel/` | The UXP plugin, the only code that touches Premiere. Plain `require()`, no bundler. |
| `helper/` | The background watcher. The panel cannot start a process, so this closes the loop. |
| `engine/recipes/*.yaml` | Editorial policy as data — thresholds, pacing, what each kind of edit means. Tune these before you touch code. |
| `schema/` | The EditPlan contract, plus vectors both languages assert against. |

**Premiere caches the panel for the life of the process.** After any change to
`panel/`, run `./update.sh` (or `panel/install.sh`) and then quit and reopen
Premiere. Reinstalling without restarting changes nothing you can see, and has
cost more than one afternoon.

## Things that will fail review

- **A Japanese string with no English key, or an English key with no Japanese.**
  `panel/test/i18n.test.js` enforces both directions, and a key whose Japanese
  equals its English is treated as untranslated.
- **A new user-visible string that only exists in one language.** Same test.
- **A recipe with no `keywords`.** The panel has no recipe control; the words in
  the description are the only way to reach one.
- **Widening a threshold to make a test pass.** If the fixture is wrong, fix the
  fixture and say why in the comment.

## Japanese

The Japanese throughout was written by the author, who is not a native speaker.
**Corrections are the single most welcome contribution to this project.**
`panel/src/i18n.js` is the whole catalogue; `engine/autoedit/detect.py` and
`story.py` carry the word lists that decide what gets cut. Terms like 尺 and
テロップ are exactly where a translation comes out fluent and wrong.

## Translations

`README.ja.md` and `docs/cutting-guide.ja.md` sit beside their English sources
rather than under a `docs/ja/`, so a stale translation is visible in the
directory listing next to the file it has fallen behind.

`panel/test/docs.test.js` holds every pair to three things: the tagged code
fences must be identical in both directions, every version and measurement in
the English must survive into the translation, and the two must link to each
other. Prose is deliberately not compared -- wording drifts, and a test that
fires on a reworded sentence gets switched off. A stale `brew install` line does
not.

Two rules when writing one:

- **The glossary is `panel/src/i18n.js`, not your own translation.** If the docs
  tell someone to press a button, they must use the string that is on it.
  素材フォルダ and 映像フォルダ are both reasonable Japanese; only one of them is
  in the panel.
- **Do not hard-wrap CJK prose.** A soft line break renders as a space, which is
  invisible in English and a gap in the middle of a Japanese sentence. One
  paragraph per line. Tables and list items are unaffected.

A new language is a row in `PAIRS` at the top of that test and nothing else.
Translations of languages the author does not read are welcome as
community-maintained, and are held to the same three assertions.

## Reporting a bug

From the panel: **Report a problem** (bottom right) → **Collect a report to
send**. It writes a zip to your Desktop with the build's git sha, the machine and
its versions, both logs, and the last eight jobs. `./report.sh` does the same
from a terminal if the panel will not open — which is the case you need it most.

It contains media *filenames*, folder paths, whatever you typed in the
description box and any subtitle text. No video, no audio, no credentials. Check
it before attaching it to a public issue if any of that is client work.
