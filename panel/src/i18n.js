"use strict";
/**
 * The panel in the editor's own language.
 *
 * Two catalogues in one file rather than two files, because the thing that goes
 * wrong with translations is drift: an English string changes, the Japanese one
 * does not, and nobody notices for a month. Side by side, a missing key is
 * visible while you are editing the line above it.
 *
 * Keys under `warn.` mirror `engine/autoedit/notes.py` exactly. Renaming one
 * there without renaming it here does not break anything -- the warning falls
 * back to the English the engine already rendered -- which is the point, but it
 * does mean a rename silently loses the translation.
 *
 * Pure, so `node --test` can check that the two catalogues line up.
 */

const EN = {
  // --- fixed choices the helper labels in English --------------------------
  "aspect.source": "Match source",
  "aspect.landscape": "Landscape 16:9",
  "aspect.vertical": "Vertical 9:16",
  "aspect.square": "Square 1:1",
  "aspect.portrait45": "Portrait 4:5",
  // The dial for how often the picture changes when the music leads. Musical
  // units rather than seconds: a bar means the same thing at any tempo, and
  // "0.95s" would not survive changing the track.
  "cutRate.auto": "Follow pacing",
  "cutRate.0.5": "Twice per beat",
  "cutRate.1": "Every beat",
  "cutRate.2": "Every 2 beats",
  "cutRate.4": "Every bar",
  "cutRate.8": "Every 2 bars",
  "pacing.relaxed": "Relaxed",
  "pacing.standard": "Standard",
  "pacing.punchy": "Punchy",
  "duration.none": "No limit",
  "duration.upTo": "Up to",
  "duration.exactly": "Exactly",
  "duration.about": "About",
  "look.none": "None",
  "lang.auto": "Detect automatically",
  "lang.en": "English",
  "lang.ja": "Japanese",

  // --- setup ---------------------------------------------------------------
  "setup.title": "Setup",
  "setup.mediaRoot": "Media root",
  "setup.jobsFolder": "Jobs folder",
  "setup.referenceFolder": "Reference folder",
  "edit.groupReference": "Cut it like",
  "edit.referenceNone": "No reference",
  "msg.referenceFolderUnreachable":
    "The reference folder cannot be reached -- it may have been moved, renamed, or be on a drive that is not plugged in. Set it again in Setup.",
  "edit.referenceUrl": "…or paste a link",
  "edit.referenceRhythm": "Rhythm only",
  "edit.referenceRhythmHint": "Copy the cutting pattern without trying to match what each shot shows.",
  "edit.referenceHint": "The edit takes its shot lengths and order from this video. It is measured and discarded — none of it reaches your timeline.",
  "msg.referenceFolderSet": "Reference folder: {path}",
  "setup.musicFolder": "Music folder",
  "setup.notSet": "not set",
  "setup.notSetOptional": "not set (optional)",
  "setup.hint":
    "Plans store paths relative to a root, so moving to a NAS later means changing " +
    "these settings and nothing else. The music folder is optional and lives " +
    "wherever you keep your library — it does not have to sit with the footage.",
  "setup.language": "Panel language",

  // --- new edit ------------------------------------------------------------
  "edit.describe": "Describe it (optional)",
  "edit.describePlaceholder": "opens with the storefront, then the chef cooking, then a happy customer",
  "edit.describeHint": "Each shot you describe becomes a section of the edit, in the order you write them. Anything the footage cannot serve is left out and said so.",
  "prompt.applied": "Set from your description: {settings}",
  "prompt.fromPictures": "cut from pictures",
  "prompt.subtitles": "subtitles",
  "edit.adjustShow": "Show settings",
  "edit.adjustHide": "Hide settings",
  "format.noLimit": "No length limit",
  "format.cutsToSpeech": "Cuts to the words",
  // The deliverables, by the name the description was recognised as. The recipe
  // yaml carries the same English, so a recipe added tomorrow still reads
  // sensibly before anyone translates it; these are what the panel shows.
  "format.social-short": "Reel / Short",
  "format.promo-silent": "Promo — cuts to music",
  "format.client-promo": "Client promo",
  "format.podcast-2cam": "Podcast cut",
  "format.long-form": "Long form — YouTube, documentary",
  "edit.title": "New edit",
  "edit.name": "Name (optional)",
  "edit.nameHint": "Leave it blank and the edit is named by the date and time.",
  "edit.clips": "Clips",
  "edit.clipsCount": "({selected} of {total})",
  "edit.selectAll": "Select all",
  "edit.clear": "Clear",
  "edit.reload": "Reload",
  "edit.recipe": "Recipe",
  "edit.shape": "Shape",
  "edit.length": "Length",
  "edit.seconds": "Seconds",
  "edit.pacing": "Pacing",
  "edit.cutRate": "Cut on",
  "edit.look": "Look",
  "edit.spokenLanguage": "Spoken language",
  "edit.create": "Create edit",
  "edit.createHint": "Runs in the background. The plan appears below when it is ready.",
  "edit.visual": "Cut from the pictures, ignoring speech",
  "edit.visualHint": "For promos and b-roll.",
  "edit.subtitles": "Write subtitles (.srt)",
  "edit.subtitlesHint": "Transcribes the speech and writes an .srt for the finished cut.",
  "edit.protectSpeech": "Keep whole sentences",
  "edit.protectSpeechHint": "Never cut mid-word. On automatically with subtitles.",
  "edit.removeSilence": "Cut out silence",
  "edit.removeSilenceHint": "Trims the quiet around speech and drops shots where nothing is said.",
  "edit.silenceAllowed": "Silence to leave",
  "edit.noMediaRoot": "Set a media root above.",
  "edit.scanning": "Scanning {where}",
  "edit.scanningNote": "Reading every clip once. A large folder takes a few minutes; it is cached after that.",
  "edit.noVideoFiles": "No video files found in {where}",
  "edit.mediaInICloud":
    "{count} file(s) in {where} are in iCloud and have not been downloaded, so nothing can read them. In Finder, select them and choose File > Download Now.",
  "edit.mediaUnreadable": "Could not read the media root: {message}",

  // --- music ---------------------------------------------------------------
  "music.label": "Music",
  "music.tracksFound": "({count} track{plural} found)",
  "music.noTracks": "(no tracks found)",
  "music.automatic": "Automatic",
  "music.none": "No music",
  "music.withFootage": "(with the footage)",
  "music.audition": "Audition",
  "music.usePlayhead": "Use playhead as start",
  "music.start": "Start in track",
  "music.length": "Length (seconds)",
  "music.lengthPlaceholder": "follows the edit",
  "music.hint":
    "Cuts snap to the beat of whichever track you pick. Tracks come from your music " +
    "folder, plus any audio-only file sitting with the footage. Automatic only fires " +
    "when there is exactly one of the latter.",
  "music.auditionHint":
    "Audition opens the track in the Source Monitor — replacing whatever is there — " +
    "so you can find the drop by ear, then press Use playhead as start. The start " +
    "moves to the nearest beat. Leave Length empty and the music follows the edit; " +
    "set it and that much music is laid even if it outruns the picture.",
  "music.auditioning":
    "Auditioning in the Source Monitor — park the playhead on the drop, then press " +
    "Use playhead as start.",
  "music.startSetTo": "Music starts at {time} — it will move to the nearest beat.",

  // --- plan and build ------------------------------------------------------
  "console.show": "What the engine did",
  "console.hide": "Hide details",
  "console.settings": "This edit",
  "console.checks": "What the engine reported",
  "console.lastBuild": "Last build",
  "console.timebase": "Frame rate",
  "console.length": "Length",
  "console.clips": "Clips",
  "console.warnings": "Warnings",
  "console.builtAt": "Built",
  "console.stages": "Stages",
  "console.problems": "Problems",
  "console.none": "none",
  "plan.title": "Plan",
  "plan.refresh": "Refresh",
  "plan.keep": "Keep this plan",
  "plan.keepHint": "Un-kept plans are cleared once five newer ones exist. Subtitles and build records are never removed.",
  "plan.kept": "“{name}” will be kept.",
  "plan.unkept": "“{name}” is no longer marked to keep.",
  "plan.sequence": "Sequence",
  "plan.recipe": "Recipe",
  "plan.clips": "Clips",
  "plan.duration": "Duration",
  "plan.sources": "Sources",
  "plan.confidence": "Mean confidence",
  "plan.build": "Build sequence",
  "plan.buildHint": "Always builds a new sequence. Each stage is one undo step.",
  "plan.none": "No jobs folder set",
  "sections.title": "Include",
  "sections.clips": "{count} clip(s)",
  "sections.graphics": "{count} graphic(s)",

  // --- reviewing and swapping shots ----------------------------------------
  "swap.title": "Shots",
  "swap.hint": "Each block is a clip, sized by how long it runs. Click one to see other shots that could take its place.",
  "swap.alternates": "Other shots that fit",
  "swap.pickPrompt": "Click a clip above.",
  "swap.current": "in use",
  "swap.sameSource": "same clip",
  "swap.preview": "Preview",
  "swap.use": "Use this",
  "swap.swapped": "swapped",
  "swap.revert": "Put the original back",
  "swap.revertAll": "Undo all swaps",
  "swap.count": "{count} swapped",
  "swap.none": "Nothing else is long enough to fill this slot.",
  "swap.noCandidates": "This plan carries no alternates. Re-run the edit to generate them.",
  "swap.discard": "Discard {count} swap(s) and open a different plan?",
  "swap.lost": "Swaps are held in the panel only — closing it or restarting Premiere loses them.",
  "swap.previewFailed": "Premiere would not open that clip in the Source Monitor.",
  "swap.library": "library",
  "swap.fromLibrary": "{count} from your library",
  "swap.libraryBuilding": "still indexing the library",
  "swap.noThumb": "no still yet",

  // --- what a shot appears to show -----------------------------------------
  // Ranked in English against the pictures, because that is the language the
  // image model was trained in and the ranking is what decides WHICH shot gets
  // which words. Only the label is translated, so the same shot reads the same
  // way in both languages instead of two encoders disagreeing about it.
  "shot.a-person-talking-to-camera": "a person talking to camera",
  "shot.a-close-up-of-a-person-s-face": "a close-up of a person's face",
  "shot.a-person-smiling": "a person smiling",
  "shot.two-people-talking": "two people talking",
  "shot.a-group-of-people-standing-together": "a group of people standing together",
  "shot.a-crowd-of-people": "a crowd of people",
  "shot.people-walking": "people walking",
  "shot.people-sitting-at-a-table": "people sitting at a table",
  "shot.a-person-working-at-a-desk": "a person working at a desk",
  "shot.a-handshake": "a handshake",
  "shot.a-person-pointing-at-something": "a person pointing at something",
  "shot.someone-giving-a-thumbs-up": "someone giving a thumbs up",
  "shot.people-laughing": "people laughing",
  "shot.a-child": "a child",
  "shot.a-family": "a family",
  "shot.children-playing-football": "children playing football",
  "shot.a-person-kicking-a-ball": "a person kicking a ball",
  "shot.a-ball-on-the-ground": "a ball on the ground",
  "shot.a-player-running": "a player running",
  "shot.a-goalkeeper": "a goalkeeper",
  "shot.a-goal-net": "a goal net",
  "shot.people-celebrating": "people celebrating",
  "shot.a-coach-giving-instructions": "a coach giving instructions",
  "shot.a-scoreboard": "a scoreboard",
  "shot.a-sports-hall": "a sports hall",
  "shot.a-pitch-or-court": "a pitch or court",
  "shot.a-chef-cooking": "a chef cooking",
  "shot.food-being-prepared": "food being prepared",
  "shot.a-plate-of-food": "a plate of food",
  "shot.a-person-eating": "a person eating",
  "shot.a-drink-being-poured": "a drink being poured",
  "shot.a-restaurant-interior": "a restaurant interior",
  "shot.a-bar": "a bar",
  "shot.a-kitchen": "a kitchen",
  "shot.a-waiter-serving-a-table": "a waiter serving a table",
  "shot.the-outside-of-a-building": "the outside of a building",
  "shot.a-shop-front": "a shop front",
  "shot.a-sign-or-banner": "a sign or banner",
  "shot.an-empty-room": "an empty room",
  "shot.a-corridor": "a corridor",
  "shot.a-street": "a street",
  "shot.a-car": "a car",
  "shot.a-car-park": "a car park",
  "shot.a-landscape": "a landscape",
  "shot.the-sky": "the sky",
  "shot.trees-or-plants": "trees or plants",
  "shot.water": "water",
  "shot.a-computer-screen": "a computer screen",
  "shot.a-product-on-a-surface": "a product on a surface",
  "shot.machinery-or-equipment": "machinery or equipment",
  "shot.a-wide-establishing-shot": "a wide establishing shot",
  "shot.a-close-up-of-an-object": "a close-up of an object",
  "shot.a-hand-doing-something": "a hand doing something",
  "shot.a-moving-camera-shot": "a moving camera shot",
  "shot.a-dark-or-low-light-scene": "a dark or low-light scene",
  "shot.an-empty-scene-with-no-people": "an empty scene with no people",

  "diag.title": "Diagnostics",
  // The bottom-right button. Named for the reason anyone opens it rather than
  // for what is inside: "Diagnostics" is what this drawer holds, not what an
  // editor is looking for when something has gone wrong.
  "diag.show": "Report a problem",
  "diag.showCount": "Report a problem ({count})",
  "diag.hide": "Close",
  "diag.selfTest": "Self-test",
  "diag.selfTestHint":
    "Exercises the Premiere API against this build and writes a report to " +
    "/tmp/autoedit-selftest/report.json. Run this first on a new machine.",
  "edit.groupFormat": "Format",
  "edit.groupTiming": "Timing",
  "edit.frameRate": "Frame rate",
  "edit.frameRateAuto": "Match the footage",
  "edit.groupSpeech": "Speech",
  "setup.addFolder": "Add another footage folder",
  "setup.removeFolder": "Remove",
  "msg.rootIndexing": "Scanning {path}...",
  "msg.rootSlow": "{path} is still being scanned — the clips will appear here on their own.",
  "msg.rootAdded": "Footage folder added: {path} — the helper will index it shortly.",
  "msg.rootAlreadyAdded": "{path} is already one of the footage folders.",
  "msg.rootsUnreachable": "Not reachable right now: {paths}. If that is an external drive, plug it in — its clips are hidden until you do.",
  "setup.change": "Change",
  // The top-right button, which is the whole update story in one label. See
  // `updateButton` for why checking and installing are two presses.
  "update.check": "Check for updates",
  "update.checking": "Checking...",
  "update.current": "Up to date",
  "update.available": "Update available",
  "update.updating": "Updating...",
  "update.restartNeeded": "Restart Premiere",
  "update.failed": "Update failed",
  "update.checkFailed": "Could not check",
  "update.offline": "Cannot check right now",
  "update.behindNote": "{count} change{plural} waiting, newest: {latest}",
  "update.behindNoteCount": "{count} change{plural} waiting.",
  "update.offlineNote": "Could not reach the repository: {detail}",
  "msg.updateChecking": "Checking for a newer version...",
  "msg.updateDone": "Updated ({detail}).",
  "msg.updateCurrent": "Already up to date ({detail}).",
  "msg.updateRestart": "  Quit Premiere and reopen it — the panel only loads at startup.",
  "msg.updateFailed": "Update did not complete: {detail}",
  "msg.updateStarted": "  The helper picked it up — updating...",
  "msg.updateRoot": "  in {root}",
  "msg.updateNotHeard":
    "  The helper has not read the request yet. It handles one thing at a time, so it is probably busy analysing footage — still waiting.",
  "msg.updateNeverHeard":
    "The background helper never read the request. It is either busy with a long job, or not running — run ./setup.sh --check in Terminal.",
  "msg.updateStillRunning":
    "The update started but has not finished yet. Give it a moment, then press Update again to see how it went.",
  "diag.report": "Collect a report to send",
  "diag.reportHint": "A report gathers the logs and your recent jobs into one zip on your Desktop — send it to whoever maintains this.",
  "msg.mediaRootSet": "Media root: {path} — the background helper will re-index it shortly.",
  "msg.mediaRootNotShared": "Could not tell the helper where the footage is ({message}). Pick the jobs folder first.",
  "msg.reportCollecting": "Collecting logs and recent jobs...",
  "msg.reportReady": "Report written to {path} — send that file.",
  "msg.reportContents": "  It has your media filenames, folder paths, prompts and any subtitle text. No video, no audio, no passwords.",
  "msg.reportNoHelper": "No answer from the background helper. It may not be running — run ./setup.sh --check in Terminal.",
  "msg.reportNoJobs": "Pick the jobs folder in Setup first.",
  "msg.reportFailed": "Could not collect the report: {detail}",
  "log.title": "Log",

  // --- how far through a job it is -----------------------------------------
  // Stage names, not sentences with the file name in them: the file name is
  // shown beside these and reads the same in either language, and a key with a
  // parameter renders the placeholder when the parameter is missing.
  "progress.queued": "Waiting for the background helper",
  "progress.fetching": "Downloading the reference video",
  "progress.reference": "Reading the reference video",
  // One label because it is now one decode: the cuts, the exposure and the
  // focus are all read from the same pass over the file.
  "progress.scanShots": "Looking through the footage",
  "progress.clip": "Reading the footage",
  "progress.thumbs": "Making stills",
  "progress.describe": "Recognising what each shot shows",
  "progress.music": "Listening for the reference's music",
  "progress.matching": "Matching your footage to the reference",
  "progress.writing": "Writing the edit",
  "progress.working": "Working",
  "progress.elapsed": "{elapsed} so far",
  // A fact, not a diagnosis. The panel cannot tell a dead helper from a slow
  // ffmpeg pass, and should not pretend otherwise.
  "progress.quiet": "no news for {quietFor}",

  // --- messages the panel itself produces ----------------------------------
  "msg.jobIdChanged":
    "The job will be called “{cleaned}” — characters a filename cannot hold were removed.",
  "msg.requested": "Requested “{name}” — {summary}",
  "msg.helperMissing": "Helper has not run yet — start it to enable new edits.",
  "msg.indexTruncated":
    "Media index stopped at {count} files — some clips or tracks are not listed.",
  "msg.musicFolderSet": "Music folder: {path} — indexing, press Reload in a moment.",
  "msg.musicFolderNoHelper": "Music folder set. Choose a jobs folder so the helper can index it.",
  "msg.auditionFailed": "Could not audition: {message}",
  "msg.playheadFailed": "Could not read the playhead: {message}",
  "msg.chooseTrackFirst": "Choose a track first.",
  "msg.built": "Done — {stages}",
  "msg.buildWarning": "warning: {message}",

  // --- form errors ---------------------------------------------------------
  "err.nameRequired": "Give the job a name",
  "err.nameDotOrSpace": "Job name cannot start with a dot or a space",
  "err.nameUnsafe": "Job name cannot contain / \\ : * ? \" < > |",
  // Not "choose one" any more: there is nothing to choose. The description
  // names the kind of edit and the recipes declare where silence lands, so an
  // empty recipe means the panel has not heard what this engine supports.
  "err.recipeRequired": "Waiting for the helper to say what this engine can make",
  "err.clipsRequired": "Select at least one clip",
  "err.lengthPositive": "Length must be more than zero seconds",
  "err.lengthTooLong": "Length must be under two hours",
  "err.musicType": "Music must be a track name, 'auto' or 'none'",
  "err.musicStart": "Music start must be a time like 0:42.4",
  "err.musicLength": "Music length must be more than zero seconds",
  "err.musicNeedsTrack": "Choose a track before setting where in it to start",

  // --- summary line --------------------------------------------------------
  "sum.clips": "{count} clip{plural}",
  "sum.upTo": "up to {seconds}s",
  "sum.exactly": "exactly {seconds}s",
  "sum.about": "about {seconds}s",
  "sum.look": "look: {name}",
  "sum.fromPictures": "from pictures",
  "sum.noMusic": "no music",
  "sum.music": "music: {name}",
  "sum.musicFrom": "music: {name} from {start}",
  "sum.musicFromFor": "music: {name} from {start} for {length}s",

  // --- engine warnings (keys mirror engine/autoedit/notes.py) --------------
  "warn.cut.lowConfidenceSkipped":
    "{count} cut(s) skipped over low-confidence speech (below {threshold}); review those sections by hand",
  "warn.cut.veryAggressive":
    "cut removed {percent}% of the source — unusually aggressive, check min_silence and filler_mode before trusting this assembly",
  "warn.visual.noSamples": "frame analysis produced no samples; quality gates skipped",
  "warn.visual.allShotsFailed":
    "every shot failed a quality gate — thresholds are probably wrong for this footage; loosen min_sharpness / min_brightness in the recipe",
  "warn.visual.shotsRejected": "{rejected} of {total} shots rejected on quality",
  "warn.visual.noBeats": "beat detection found no beats; falling back to fixed-length takes",
  "warn.visual.lowBeatConfidence":
    "beat confidence {confidence} is below {threshold}; cutting to a wrong grid is worse than not cutting to one, so fixed-length takes are used instead",
  "warn.visual.targetReachedEarly": "reached the {seconds}s target with {remaining} usable shot(s) unused",
  "warn.visual.nothingSurvived": "no clips survived visual cut planning",
  "warn.length.shortOfAbout":
    "came out at {total}s against a target of about {seconds}s — there was not enough usable material to reach it",
  "warn.length.shortOfTarget":
    "came out at {total}s against a {seconds}s target — there was not enough usable material to fill it",
  "warn.length.trimmedWorst":
    "trimmed to {running}s for the {seconds}s target ({dropped} clip(s) dropped, lowest quality first)",
  "warn.length.trimmedTail":
    "trimmed to {running}s for the {seconds}s target ({dropped} clip(s) dropped, from the end)",
  "warn.length.nothingFits":
    "nothing fits inside {seconds}s — the shortest available clip is longer than the target",
  "warn.reframe.cropRisk":
    "{count} clip(s) flagged: detail sits outside the centre crop, so check those before delivering",
  "warn.music.cappedToTrack":
    "the edit is capped at {seconds}s, the length of the music — set a length if you want it to run on past the track",
  "warn.music.startPastEnd":
    "start {start}s is past the end of the {duration}s track; starting from the beginning instead",
  "warn.music.snappedToBeat": "start moved {moved}s to the nearest beat, at {start}s",
  "warn.music.chunkClamped":
    "asked for {wanted}s from {start}s but the track only has {available}s left, so the bed is {actual}s",
  "warn.music.runsPastPicture":
    "the music runs {overhang}s past the last frame of picture — extend the edit or shorten the chunk",
  "warn.music.stopsEarly": "the music stops {shortfall}s before the picture does",
  "warn.music.fromReference":
    "the reference is playing {name}, which is in your music library, so the edit is cut to it",
  "warn.music.referenceAmbiguous":
    "the reference's music resembles more than one track in your library ({name} and {other}) — pass one explicitly if this is wrong",
  "warn.music.referenceNotIndexed":
    "the music library is still being listened to ({done} of {total} tracks), so the reference's music could not be looked up yet — it will work on the next run",
  "warn.language.uncertain":
    "{file}: only {confidence} sure this is {language} — set the language in the panel if that is wrong",
  "warn.plan.lowConfidence":
    "{count} clip(s) scored low confidence — either speech the transcript was unsure about, or shots that only just passed the quality gates. Review those before trusting the cut.",
  "warn.reframe.fitted":
    "{count} clip(s) resized to {width}x{height}; the shape already matched, so nothing is cropped",
  "warn.reframe.scaled":
    "{count} clip(s) scaled to fill {width}x{height}; anything at the edge of frame is now cropped out",
  "warn.transcript.notImported":
    "{mediaId}: transcript not handed to Text-Based Editing ({detail}) — the cut itself is unaffected",
  "warn.media.proxyAttached":
    "{count} clip(s) are too heavy to play back at full resolution, so proxies are used — turn on Toggle Proxies in the program monitor to see them",
  "warn.media.proxyBuilding":
    "{count} clip(s) will not play back smoothly and their proxies are still building; the cut is correct either way, and playback catches up once they finish",
  "warn.beat.snapped":
    "{snapped} cut(s) placed on the beat at {bpm} BPM{held_clause}{dropped_clause}",
  "warn.beat.gridUnavailable":
    "beat confidence {confidence} is below {threshold}, so the cuts follow the speech instead of the music — cutting to a wrong grid is worse than not cutting to one",
  "warn.timebase.followedFootage":
    "sequence set to {chosen}fps to match the footage; the recipe asks for {recipe}fps, which no whole number of source frames lands on exactly",
  "warn.visual.rateBelowMinimum":
    "your cut rate asks for {take}s shots, shorter than this recipe's {minimum}s minimum — the rate was used, since a shot cut to the beat is not a fragment",
  "warn.cut.rateNeedsPictures":
    "a cut rate only applies when cutting from pictures; this edit follows the words, so the rate was not used — pick a montage format or turn on “Cut from pictures”",
  "warn.reference.settingsTaken": "Taken from the reference video: {settings}",
  "warn.reference.matched":
    "{matched} of {shots} reference shots were found in your footage (similarity {worst}–{best}, floor {floor}).",
  "warn.reference.shotUnfilled":
    "Nothing in the footage matched {shot} of the reference, so that shot is not in the edit and its time went to the others.",
  "warn.reference.rhythmOnly":
    "None of the reference's shots were found in your footage, so it was used for its cutting rhythm alone and the shots were filled by quality.",
  "warn.reference.reused":
    "{count} shot(s) appear more than once: the reference has more cuts than your footage has usable moments.",
  "warn.reference.nothingMatched":
    "The reference could not be laid out over this footage, so the edit was assembled the ordinary way instead.",
  "warn.reference.noShots":
    "No shots were found in the reference video — if it is one continuous take there is no cutting pattern to copy.",
  "warn.reference.noVisualSpans":
    "Matching a reference needs pictures to match against; pick footage the analyser can read.",
  "warn.reference.noModel":
    "The vision model is not available, so the reference could not be used — the edit was assembled the ordinary way.",
  "warn.reference.describedInstead":
    "You gave both a description and a reference video; the description was used for the running order and the reference ignored.",
  "warn.reference.roles":
    "The reference holds on someone talking in {holds} shot(s); {talking} of your spans are someone talking, and those were preferred for them.",
  "warn.story.settingsApplied": "taken from your description: {settings}",
  "warn.subtitles.borrowedProvider":
    "the {recipe} recipe does not transcribe, so {provider} was used for the subtitles",
  "warn.subtitles.failed":
    "{file} could not be transcribed, so it has no subtitles ({detail}) — the edit itself is unaffected",
  "warn.build.oldPremiere":
    "This is Premiere {found}; the panel is built against {needed} and later. Some calls it makes do not exist here, so parts of the build fall back to less reliable methods. Updating Premiere is the fix.",
  "warn.clips.noSubclipApi":
    "This Premiere cannot make subclips, so clips were placed by setting in/out on the master instead. Check the clip lengths against the plan — that method is unreliable on some builds.",
  "warn.subtitles.placed":
    "{file}: {count} caption(s) placed on the caption track",
  "warn.subtitles.stale":
    "{file} was already in this project from an earlier build — remove the old one from the bin and re-import, or its captions will be from the previous edit",
  "warn.subtitles.wrongTrack":
    "{file} was placed as a clip, not a caption ({how}) — press Cmd-Z once to remove it, and drag the .srt to a caption track instead",
  "warn.subtitles.dragToTrack":
    "{file} is in the project — drag it to a caption track. Placing it automatically did not work here ({detail}).",
  "warn.subtitles.transcriptSchema":
    "Premiere transcript schema found — keys {keys}, sample {sample}",
  "warn.subtitles.imported":
    "{file} imported — drag it onto a caption track if it is not already there",
  "warn.subtitles.notImported":
    "{file} was written but Premiere would not import it ({detail}) — File > Import it by hand",
  "warn.speech.protected":
    "{count} cut(s) moved off the middle of a word so nobody is cut off mid-sentence",
  "warn.speech.paceTooFast":
    "more cuts fall inside a word than were saved: at this cut rate a shot is shorter than the speech in it, so slow the cutting down or turn off keeping whole sentences",
  "warn.speech.couldNotProtect":
    "{count} cut(s) still fall inside a word: the speech there runs with no gap to cut in",
  "warn.speech.beatsGaveWay":
    "some cuts no longer sit exactly on the beat, because keeping whole sentences and cutting on every beat cannot both be true",
  "warn.silence.removed":
    "{seconds}s of silence removed, leaving up to {allowed}s around what is said",
  "warn.silence.shotDropped":
    "nothing is said in this shot",
  "warn.silence.allDropped":
    "removing silence would have emptied the edit, so it was left alone — raise the allowance or turn it off",
  "warn.subtitles.written":
    "{count} subtitle(s) written to {file} — File > Import in Premiere puts them on a caption track",
  "warn.subtitles.none":
    "no subtitles were written: nothing in the finished edit has a transcript",

  "warn.story.noRunningOrder":
    "your description does not list shots in order, so it was used for the settings only — write “opens with X, then Y” to set a running order",
  "warn.story.beatUnfilled":
    "nothing in the footage matched “{beat}”, so that shot is not in the edit and its time went to the others",
  "warn.story.assembled":
    "{matched} of {total} described shots were found in the footage",
  "warn.story.nothingMatched":
    "none of the described shots were found in the footage, so the edit was assembled the ordinary way instead",
  "warn.story.noModel":
    "the vision model is not available, so the description could not be used — the edit was assembled the ordinary way",
  "warn.story.noVisualSpans":
    "a description needs pictures to match against; turn on “Cut from pictures”",
  "warn.timebase.chosen":
    "Sequence set to {chosen} fps because you asked for it.",
  "warn.timebase.mixedRates":
    "{count} clip(s) are not an exact fit for the {chosen}fps sequence, so those cuts can be a frame out — check the joins on {files}",
  "warn.swap.candidatesTruncated":
    "{file} has {found} usable shots; only the best {kept} are offered as alternates, so one you remember may not be listed",
  "warn.sequence.rateMismatch":
    "the sequence is {actual}fps but the plan was written for {wanted}fps — clip lengths will not be what the plan asked for",
};

const JA = {
  // --- fixed choices -------------------------------------------------------
  "aspect.source": "元の比率のまま",
  "aspect.landscape": "横位置 16:9",
  "aspect.vertical": "縦位置 9:16",
  "aspect.square": "正方形 1:1",
  "aspect.portrait45": "縦位置 4:5",
  "cutRate.auto": "テンポに合わせる",
  "cutRate.0.5": "1 拍に 2 回",
  "cutRate.1": "1 拍ごと",
  "cutRate.2": "2 拍ごと",
  "cutRate.4": "1 小節ごと",
  "cutRate.8": "2 小節ごと",
  "pacing.relaxed": "ゆっくり",
  "pacing.standard": "標準",
  "pacing.punchy": "速い",
  "duration.none": "指定なし",
  "duration.upTo": "最大",
  "duration.exactly": "ちょうど",
  "duration.about": "約",
  "look.none": "なし",
  "lang.auto": "自動判定",
  "lang.en": "英語",
  "lang.ja": "日本語",

  // --- setup ---------------------------------------------------------------
  "setup.title": "設定",
  "setup.mediaRoot": "素材フォルダ",
  "setup.jobsFolder": "ジョブフォルダ",
  "setup.referenceFolder": "参考動画フォルダ",
  "edit.groupReference": "この動画のように",
  "edit.referenceNone": "参考動画なし",
  "msg.referenceFolderUnreachable":
    "参考動画フォルダーにアクセスできません。移動または名前が変更されたか、ドライブが接続されていない可能性があります。設定でもう一度指定してください。",
  "edit.referenceUrl": "…またはリンクを貼り付け",
  "edit.referenceRhythm": "リズムのみ",
  "edit.referenceRhythmHint": "各ショットの内容は合わせず、カットの間隔だけを真似します。",
  "edit.referenceHint": "この動画からショットの長さと順序を取り込みます。解析のみに使用し、タイムラインには入りません。",
  "msg.referenceFolderSet": "参考動画フォルダ: {path}",
  "setup.musicFolder": "音楽フォルダ",
  "setup.notSet": "未設定",
  "setup.notSetOptional": "未設定（任意）",
  "setup.hint":
    "プランはルートからの相対パスで保存されるため、将来 NAS に移す場合もこの設定を" +
    "変えるだけで済みます。音楽フォルダは任意で、素材と同じ場所になくても構いません。",
  "setup.language": "パネルの言語",

  // --- new edit ------------------------------------------------------------
  "edit.describe": "内容を説明（任意）",
  "edit.describePlaceholder": "店の外観から始まり、シェフが調理する様子、最後に笑顔のお客様",
  "edit.describeHint": "説明した各ショットが、書いた順にセクションになります。映像内に見つからないものは除外し、その旨をお知らせします。",
  "prompt.applied": "説明文から設定しました: {settings}",
  "prompt.fromPictures": "映像からカット",
  "prompt.subtitles": "字幕",
  "edit.adjustShow": "設定を表示",
  "edit.adjustHide": "設定を隠す",
  "format.noLimit": "長さの指定なし",
  "format.cutsToSpeech": "話に合わせてカット",
  "format.social-short": "リール・ショート",
  "format.promo-silent": "プロモ（音楽に合わせてカット）",
  "format.client-promo": "クライアント向けプロモ",
  "format.podcast-2cam": "ポッドキャストの編集",
  "format.long-form": "長尺（YouTube・ドキュメンタリー）",
  "edit.title": "新規編集",
  "edit.name": "名前（任意）",
  "edit.nameHint": "空欄のままにすると、日付と時刻で名前が付きます。",
  "edit.clips": "クリップ",
  "edit.clipsCount": "（{total} 件中 {selected} 件）",
  "edit.selectAll": "すべて選択",
  "edit.clear": "選択解除",
  "edit.reload": "再読み込み",
  "edit.recipe": "レシピ",
  "edit.shape": "画面比率",
  "edit.length": "尺",
  "edit.seconds": "秒数",
  "edit.pacing": "テンポ",
  "edit.cutRate": "カットの位置",
  "edit.look": "カラー",
  "edit.spokenLanguage": "話されている言語",
  "edit.create": "編集を作成",
  "edit.createHint": "バックグラウンドで実行されます。完了するとプランが下に表示されます。",
  "edit.visual": "音声を無視して映像でカットする",
  "edit.visualHint": "プロモや B ロール向けです。オフにすると話した内容に合わせてカットします。",
  "edit.subtitles": "字幕を書き出す（.srt）",
  "edit.subtitlesHint": "音声を文字起こしし、完成した編集に合わせた字幕を書き出します。",
  "edit.protectSpeech": "文を途中で切らない",
  "edit.protectSpeechHint": "語の途中に来たカットをずらします。字幕を書き出す場合は自動でオンになります。",
  "edit.removeSilence": "無音を削除する",
  "edit.removeSilenceHint": "発話の前後の静かな部分を詰め、発話のないショットを外します。",
  "edit.silenceAllowed": "残す無音の長さ",
  "edit.noMediaRoot": "上で素材フォルダを設定してください。",
  "edit.scanning": "{where} を解析中",
  "edit.scanningNote": "すべてのクリップを一度読み込みます。大きなフォルダーでは数分かかりますが、以降はキャッシュされます。",
  "edit.noVideoFiles": "{where} に動画ファイルが見つかりません",
  "edit.mediaInICloud":
    "{where} 内の {count} 個のファイルは iCloud 上にありダウンロードされていないため読み込めません。Finder で選択し、ファイル > 今すぐダウンロード を実行してください。",
  "edit.mediaUnreadable": "素材フォルダを読み込めませんでした：{message}",

  // --- music ---------------------------------------------------------------
  "music.label": "音楽",
  "music.tracksFound": "（{count} 曲）",
  "music.noTracks": "（曲が見つかりません）",
  "music.automatic": "自動",
  "music.none": "音楽なし",
  "music.withFootage": "（素材と同じ場所）",
  "music.audition": "試聴",
  "music.usePlayhead": "再生ヘッドの位置を開始点にする",
  "music.start": "曲の開始位置",
  "music.length": "長さ（秒）",
  "music.lengthPlaceholder": "編集に合わせる",
  "music.hint":
    "選んだ曲のビートにカットが合わせられます。曲は音楽フォルダと、素材と同じ場所にある" +
    "音声のみのファイルから選べます。自動が働くのは後者がちょうど 1 つのときだけです。",
  "music.auditionHint":
    "「試聴」でソースモニターに曲が開きます（開いていたものは置き換わります）。" +
    "耳でサビを探して「再生ヘッドの位置を開始点にする」を押してください。開始位置は" +
    "最も近いビートに合わせられます。長さを空欄にすると編集に合わせ、指定すると映像より" +
    "長くなってもその分だけ音楽が置かれます。",
  "music.auditioning":
    "ソースモニターで試聴中です。サビの位置に再生ヘッドを合わせてから「再生ヘッドの位置を開始点にする」を押してください。",
  "music.startSetTo": "音楽の開始位置は {time} です。最も近いビートに合わせられます。",

  // --- plan and build ------------------------------------------------------
  "console.show": "処理の詳細を見る",
  "console.hide": "詳細を隠す",
  "console.settings": "この編集",
  "console.checks": "エンジンからの報告",
  "console.lastBuild": "前回の作成",
  "console.timebase": "フレームレート",
  "console.length": "長さ",
  "console.clips": "クリップ数",
  "console.warnings": "警告",
  "console.builtAt": "作成日時",
  "console.stages": "工程",
  "console.problems": "問題",
  "console.none": "なし",
  "plan.title": "プラン",
  "plan.refresh": "更新",
  "plan.keep": "このプランを残す",
  "plan.keepHint": "残す指定のないプランは、新しいものが5件たまると削除されます。字幕とビルド記録は削除されません。",
  "plan.kept": "「{name}」を残します。",
  "plan.unkept": "「{name}」の保持指定を解除しました。",
  "plan.sequence": "シーケンス",
  "plan.recipe": "レシピ",
  "plan.clips": "クリップ数",
  "plan.duration": "尺",
  "plan.sources": "素材数",
  "plan.confidence": "平均信頼度",
  "plan.build": "シーケンスを作成",
  "plan.buildHint": "毎回新しいシーケンスを作成します。各工程は取り消し 1 回分です。",
  "plan.none": "ジョブフォルダが未設定です",
  "sections.title": "含める範囲",
  "sections.clips": "クリップ {count} 個",
  "sections.graphics": "グラフィック {count} 個",

  // --- ショットの確認と差し替え --------------------------------------------
  "swap.title": "ショット",
  "swap.hint": "各ブロックが 1 つのクリップで、幅は長さを表します。クリックすると差し替え候補が表示されます。",
  "swap.alternates": "差し替えできるショット",
  "swap.pickPrompt": "上のクリップをクリックしてください。",
  "swap.current": "使用中",
  "swap.sameSource": "同じクリップ",
  "swap.preview": "プレビュー",
  "swap.use": "これに差し替え",
  "swap.swapped": "差し替え済み",
  "swap.revert": "元のショットに戻す",
  "swap.revertAll": "差し替えをすべて取り消す",
  "swap.count": "{count} 箇所を差し替え",
  "swap.none": "この長さを埋められるショットが他にありません。",
  "swap.noCandidates": "この編集案には差し替え候補がありません。編集を作り直すと生成されます。",
  "swap.discard": "{count} 箇所の差し替えを破棄して別の編集案を開きますか？",
  "swap.lost": "差し替えはパネル内にのみ保持されます。パネルを閉じたり Premiere を再起動すると失われます。",
  "swap.previewFailed": "そのクリップをソースモニターで開けませんでした。",
  "swap.library": "ライブラリ",
  "swap.fromLibrary": "ライブラリから {count} 件",
  "swap.libraryBuilding": "ライブラリを解析中",
  "swap.noThumb": "静止画なし",

  // --- what a shot appears to show -----------------------------------------
  // Ranked in English against the pictures, because that is the language the
  // image model was trained in and the ranking is what decides WHICH shot gets
  // which words. Only the label is translated, so the same shot reads the same
  // way in both languages instead of two encoders disagreeing about it.
  "shot.a-person-talking-to-camera": "カメラに向かって話す人",
  "shot.a-close-up-of-a-person-s-face": "顔のアップ",
  "shot.a-person-smiling": "笑っている人",
  "shot.two-people-talking": "会話する二人",
  "shot.a-group-of-people-standing-together": "集まって立つ人たち",
  "shot.a-crowd-of-people": "大勢の人",
  "shot.people-walking": "歩いている人たち",
  "shot.people-sitting-at-a-table": "テーブルに着く人たち",
  "shot.a-person-working-at-a-desk": "机で作業する人",
  "shot.a-handshake": "握手",
  "shot.a-person-pointing-at-something": "何かを指さす人",
  "shot.someone-giving-a-thumbs-up": "親指を立てる人",
  "shot.people-laughing": "笑い合う人たち",
  "shot.a-child": "子ども",
  "shot.a-family": "家族",
  "shot.children-playing-football": "サッカーをする子どもたち",
  "shot.a-person-kicking-a-ball": "ボールを蹴る人",
  "shot.a-ball-on-the-ground": "地面のボール",
  "shot.a-player-running": "走る選手",
  "shot.a-goalkeeper": "ゴールキーパー",
  "shot.a-goal-net": "ゴールネット",
  "shot.people-celebrating": "喜ぶ人たち",
  "shot.a-coach-giving-instructions": "指示を出すコーチ",
  "shot.a-scoreboard": "スコアボード",
  "shot.a-sports-hall": "体育館",
  "shot.a-pitch-or-court": "ピッチ・コート",
  "shot.a-chef-cooking": "調理するシェフ",
  "shot.food-being-prepared": "下ごしらえ",
  "shot.a-plate-of-food": "料理の皿",
  "shot.a-person-eating": "食事をする人",
  "shot.a-drink-being-poured": "注がれる飲み物",
  "shot.a-restaurant-interior": "店内",
  "shot.a-bar": "バーカウンター",
  "shot.a-kitchen": "厨房",
  "shot.a-waiter-serving-a-table": "料理を運ぶ店員",
  "shot.the-outside-of-a-building": "建物の外観",
  "shot.a-shop-front": "店の外観",
  "shot.a-sign-or-banner": "看板・のぼり",
  "shot.an-empty-room": "無人の部屋",
  "shot.a-corridor": "廊下",
  "shot.a-street": "街路",
  "shot.a-car": "車",
  "shot.a-car-park": "駐車場",
  "shot.a-landscape": "風景",
  "shot.the-sky": "空",
  "shot.trees-or-plants": "木や植物",
  "shot.water": "水",
  "shot.a-computer-screen": "パソコンの画面",
  "shot.a-product-on-a-surface": "置かれた商品",
  "shot.machinery-or-equipment": "機械・設備",
  "shot.a-wide-establishing-shot": "引きの画",
  "shot.a-close-up-of-an-object": "物のアップ",
  "shot.a-hand-doing-something": "手元のカット",
  "shot.a-moving-camera-shot": "カメラが動くカット",
  "shot.a-dark-or-low-light-scene": "暗いシーン",
  "shot.an-empty-scene-with-no-people": "人のいないカット",

  "diag.title": "診断",
  "diag.selfTest": "セルフテスト",
  "edit.groupFormat": "形式",
  "edit.groupTiming": "尺とテンポ",
  "edit.frameRate": "フレームレート",
  "edit.frameRateAuto": "素材に合わせる",
  "edit.groupSpeech": "音声",
  "setup.addFolder": "素材フォルダを追加",
  "setup.removeFolder": "削除",
  "msg.rootIndexing": "{path} を解析しています...",
  "msg.rootSlow": "{path} の解析が続いています。完了するとクリップが表示されます。",
  "msg.rootAdded": "素材フォルダを追加しました: {path}。まもなくヘルパーが解析します。",
  "msg.rootAlreadyAdded": "{path} はすでに素材フォルダに含まれています。",
  "msg.rootsUnreachable": "現在アクセスできません: {paths}。外付けドライブの場合は接続してください。接続するまでそのクリップは表示されません。",
  "setup.change": "変更",
  "diag.show": "不具合を報告",
  "diag.showCount": "不具合を報告（{count}）",
  "diag.hide": "閉じる",
  "diag.selfTestHint":
    "このバージョンの Premiere API を検証し、/tmp/autoedit-selftest/report.json に" +
    "結果を書き出します。新しい PC では最初にこれを実行してください。",
  "update.check": "更新を確認",
  "update.checking": "確認しています...",
  "update.current": "最新です",
  "update.available": "更新があります",
  "update.updating": "更新しています...",
  "update.restartNeeded": "Premiere を再起動",
  "update.failed": "更新に失敗しました",
  "update.checkFailed": "確認できませんでした",
  "update.offline": "現在は確認できません",
  "update.behindNote": "更新が {count} 件あります。最新: {latest}",
  "update.behindNoteCount": "更新が {count} 件あります。",
  "update.offlineNote": "リポジトリに接続できませんでした: {detail}",
  "msg.updateChecking": "新しいバージョンを確認しています...",
  "msg.updateDone": "更新しました（{detail}）。",
  "msg.updateCurrent": "すでに最新です（{detail}）。",
  "msg.updateRestart": "  Premiere を終了して再度開いてください。パネルは起動時にのみ読み込まれます。",
  "msg.updateFailed": "更新を完了できませんでした: {detail}",
  "msg.updateStarted": "  ヘルパーが受け付けました。更新しています...",
  "msg.updateRoot": "  対象: {root}",
  "msg.updateNotHeard":
    "  ヘルパーはまだ要求を読んでいません。一度に一つの処理しか行わないため、映像の解析中の可能性があります。このまま待機します。",
  "msg.updateNeverHeard":
    "バックグラウンドヘルパーが要求を読み取れませんでした。長い処理の実行中か、起動していない可能性があります。ターミナルで ./setup.sh --check を実行してください。",
  "msg.updateStillRunning":
    "更新は開始しましたが、まだ完了していません。しばらく待ってから、もう一度「更新」を押して結果を確認してください。",
  "diag.report": "送信用のレポートを作成",
  "diag.reportHint": "ログ・最近のジョブ・この PC の設定をまとめた zip をデスクトップに作成します。管理者に送ってください。",
  "msg.mediaRootSet": "素材フォルダ: {path}。バックグラウンドヘルパーがまもなく再スキャンします。",
  "msg.mediaRootNotShared": "素材フォルダの場所をヘルパーに伝えられませんでした（{message}）。先にジョブフォルダを選択してください。",
  "msg.reportCollecting": "ログと最近のジョブを収集しています...",
  "msg.reportReady": "レポートを {path} に書き出しました。このファイルを送ってください。",
  "msg.reportContents": "  含まれるのは素材のファイル名・フォルダーのパス・入力した説明文・字幕テキストです。映像・音声・パスワードは含まれません。",
  "msg.reportNoHelper": "バックグラウンドヘルパーから応答がありません。起動していない可能性があります。ターミナルで ./setup.sh --check を実行してください。",
  "msg.reportNoJobs": "先に設定でジョブフォルダーを選択してください。",
  "msg.reportFailed": "レポートを作成できませんでした: {detail}",
  "log.title": "ログ",
  "progress.queued": "バックグラウンドヘルパーの応答待ち",
  "progress.fetching": "参考動画をダウンロード中",
  "progress.reference": "参考動画を読み込み中",
  "progress.scanShots": "映像を解析中",
  "progress.clip": "素材を読み込み中",
  "progress.thumbs": "静止画を作成中",
  "progress.describe": "各ショットの内容を認識中",
  "progress.music": "参考動画の音楽を照合中",
  "progress.matching": "素材を参考動画に合わせています",
  "progress.writing": "編集データを書き出し中",
  "progress.working": "処理中",
  "progress.elapsed": "経過 {elapsed}",
  "progress.quiet": "{quietFor} 更新なし",

  // --- messages the panel itself produces ----------------------------------
  "msg.jobIdChanged":
    "ジョブ名は「{cleaned}」になります。ファイル名に使えない文字を取り除きました。",
  "msg.requested": "「{name}」を作成中 — {summary}",
  "msg.helperMissing": "ヘルパーがまだ起動していません。起動すると編集を作成できます。",
  "msg.indexTruncated":
    "素材の一覧が {count} 件で打ち切られました。表示されていないクリップや曲があります。",
  "msg.musicFolderSet": "音楽フォルダ：{path} — 読み込み中です。少し待ってから「再読み込み」を押してください。",
  "msg.musicFolderNoHelper": "音楽フォルダを設定しました。ヘルパーが読み込めるようジョブフォルダも選んでください。",
  "msg.auditionFailed": "試聴できませんでした：{message}",
  "msg.playheadFailed": "再生ヘッドの位置を取得できませんでした：{message}",
  "msg.chooseTrackFirst": "先に曲を選んでください。",
  "msg.built": "完了 — {stages}",
  "msg.buildWarning": "警告：{message}",

  // --- form errors ---------------------------------------------------------
  "err.nameRequired": "ジョブ名を入力してください",
  "err.nameDotOrSpace": "ジョブ名の先頭にピリオドや空白は使えません",
  "err.nameUnsafe": "ジョブ名に / \\ : * ? \" < > | は使えません",
  "err.recipeRequired": "このエンジンで作れるものが、ヘルパーからまだ届いていません",
  "err.clipsRequired": "クリップを 1 つ以上選んでください",
  "err.lengthPositive": "尺は 0 秒より長く指定してください",
  "err.lengthTooLong": "尺は 2 時間未満で指定してください",
  "err.musicType": "音楽には曲名、'auto'、'none' のいずれかを指定してください",
  "err.musicStart": "開始位置は 0:42.4 のような時間で入力してください",
  "err.musicLength": "音楽の長さは 0 秒より長く指定してください",
  "err.musicNeedsTrack": "開始位置を設定する前に曲を選んでください",

  // --- summary line --------------------------------------------------------
  "sum.clips": "クリップ {count} 件",
  "sum.upTo": "最大 {seconds} 秒",
  "sum.exactly": "ちょうど {seconds} 秒",
  "sum.about": "約 {seconds} 秒",
  "sum.look": "カラー：{name}",
  "sum.fromPictures": "映像からカット",
  "sum.noMusic": "音楽なし",
  "sum.music": "音楽：{name}",
  "sum.musicFrom": "音楽：{name}（{start} から）",
  "sum.musicFromFor": "音楽：{name}（{start} から {length} 秒）",

  // --- engine warnings -----------------------------------------------------
  "warn.cut.lowConfidenceSkipped":
    "音声の信頼度が低い箇所（{threshold} 未満）で {count} 箇所のカットを見送りました。該当部分は手で確認してください",
  "warn.cut.veryAggressive":
    "元素材の {percent}% を削除しました。かなり大きいので、この結果を信頼する前にレシピの min_silence と filler_mode を確認してください",
  "warn.visual.noSamples": "フレーム解析のサンプルが得られなかったため、画質判定を省略しました",
  "warn.visual.allShotsFailed":
    "すべてのショットが画質判定に落ちました。この素材にはしきい値が合っていない可能性が高いので、レシピの min_sharpness と min_brightness を下げてください",
  "warn.visual.shotsRejected": "{total} ショット中 {rejected} ショットを画質により除外しました",
  "warn.visual.noBeats": "ビートを検出できなかったため、固定長のカットに切り替えました",
  "warn.visual.lowBeatConfidence":
    "ビートの信頼度 {confidence} がしきい値 {threshold} を下回っています。誤ったビートに合わせるより良いため、固定長のカットを使用します",
  "warn.visual.targetReachedEarly": "目標の {seconds} 秒に達したため、使えるショットが {remaining} 件残りました",
  "warn.visual.nothingSurvived": "映像からのカットで残ったクリップがありません",
  "warn.length.shortOfAbout":
    "目標の約 {seconds} 秒に対して {total} 秒になりました。使える素材が足りませんでした",
  "warn.length.shortOfTarget":
    "目標の {seconds} 秒に対して {total} 秒になりました。使える素材が足りませんでした",
  "warn.length.trimmedWorst":
    "目標の {seconds} 秒に合わせて {running} 秒に切り詰めました（画質の低い順に {dropped} 件を除外）",
  "warn.length.trimmedTail":
    "目標の {seconds} 秒に合わせて {running} 秒に切り詰めました（末尾から {dropped} 件を除外）",
  "warn.length.nothingFits":
    "{seconds} 秒に収まるものがありません。いちばん短いクリップでも目標より長くなっています",
  "warn.reframe.cropRisk":
    "{count} 件のクリップに印を付けました。中央のクロップから外れた位置に被写体がある可能性があるため、書き出す前に確認してください",
  "warn.music.cappedToTrack":
    "音楽の長さに合わせて編集を {seconds} 秒で止めました。音楽より長くする場合は尺を指定してください",
  "warn.music.startPastEnd":
    "開始位置 {start} 秒は {duration} 秒の曲の終わりを超えています。先頭から再生します",
  "warn.music.snappedToBeat": "開始位置を {moved} 秒ずらし、最も近いビートの {start} 秒に合わせました",
  "warn.music.chunkClamped":
    "{start} 秒から {wanted} 秒を指定しましたが、曲の残りが {available} 秒しかないため {actual} 秒になりました",
  "warn.music.runsPastPicture":
    "音楽が映像の最後より {overhang} 秒長くなっています。編集を伸ばすか、音楽を短くしてください",
  "warn.music.stopsEarly": "音楽が映像より {shortfall} 秒早く終わります",
  "warn.music.fromReference":
    "参考動画で流れている {name} が音楽ライブラリにあったので、その曲に合わせて編集しました",
  "warn.music.referenceAmbiguous":
    "参考動画の音楽がライブラリの複数の曲（{name} と {other}）に似ています。違う場合は曲を直接指定してください",
  "warn.music.referenceNotIndexed":
    "音楽ライブラリを解析中です（{total} 曲中 {done} 曲）。参考動画の音楽はまだ照合できませんが、次回の実行では使えます",
  "warn.language.uncertain":
    "{file}：{language} である確率は {confidence} です。異なる場合はパネルで言語を指定してください",
  "warn.plan.lowConfidence":
    "{count} 件のクリップの信頼度が低くなっています。文字起こしが不確かな音声か、画質の基準をぎりぎり満たしたショットです。書き出す前に該当箇所を確認してください。",
  "warn.reframe.fitted":
    "{count} 件のクリップを {width}x{height} にリサイズしました。比率が同じなので切れている部分はありません",
  "warn.reframe.scaled":
    "{count} 件のクリップを {width}x{height} に合わせて拡大しました。画面端に写っているものは切れています",
  "warn.transcript.notImported":
    "{mediaId}：文字起こしをテキストベース編集に渡せませんでした（{detail}）。カット自体には影響ありません",
  "warn.media.proxyAttached":
    "{count} 件のクリップは元の解像度では再生が追いつかないため、プロキシを使用します。プログラムモニターの「プロキシの切り替え」をオンにしてください",
  "warn.media.proxyBuilding":
    "{count} 件のクリップは再生が滑らかになりません。プロキシを作成中です（編集内容には影響しません。作成が終わると再生も追いつきます）",
  "warn.beat.snapped":
    "{bpm} BPM のビートに合わせてカットを {snapped} 箇所配置しました{held_clause}{dropped_clause}",
  "warn.beat.gridUnavailable":
    "ビート検出の信頼度が {confidence} で基準の {threshold} を下回るため、音楽ではなく話し方に合わせてカットしました（誤ったビートに合わせるより、合わせない方が安全です）",
  "warn.timebase.followedFootage":
    "素材に合わせてシーケンスを {chosen}fps に設定しました（レシピの指定は {recipe}fps ですが、素材のフレームがちょうど収まりません）",
  "warn.visual.rateBelowMinimum":
    "指定のカット間隔は {take} 秒のショットになり、このレシピの最小値 {minimum} 秒より短くなります。ビートに合わせたショットは断片ではないため、指定を優先しました",
  "warn.cut.rateNeedsPictures":
    "カット間隔は映像からカットする場合にのみ適用されます。この編集は話に合わせているため、指定は使用されませんでした。モンタージュ形式を選ぶか「映像からカット」を有効にしてください",
  "warn.reference.settingsTaken": "参考動画から取得: {settings}",
  "warn.reference.matched":
    "参考動画の {shots} ショット中 {matched} 件が素材内で見つかりました（類似度 {worst}〜{best}、しきい値 {floor}）。",
  "warn.reference.shotUnfilled":
    "参考動画の {shot} に合う素材が見つからなかったため、そのショットは編集に含まれず、時間は他へ配分されました。",
  "warn.reference.rhythmOnly":
    "参考動画のショットが素材内に見つからなかったため、カットのリズムのみを使用し、ショットは品質順に割り当てました。",
  "warn.reference.reused":
    "{count} 件のショットが複数回使われています。参考動画のカット数に対して使える素材が足りません。",
  "warn.reference.nothingMatched":
    "参考動画をこの素材に適用できなかったため、通常の方法で編集を構成しました。",
  "warn.reference.noShots":
    "参考動画からショットを検出できませんでした。ワンカットの動画にはコピーできるカット構成がありません。",
  "warn.reference.noVisualSpans":
    "参考動画との照合には映像が必要です。解析可能な素材を選んでください。",
  "warn.reference.noModel":
    "画像モデルが利用できないため参考動画を使用できませんでした。通常の方法で編集を構成しました。",
  "warn.reference.describedInstead":
    "説明文と参考動画の両方が指定されています。順序は説明文を使用し、参考動画は無視しました。",
  "warn.reference.roles":
    "参考動画では {holds} 個のショットが人物の発話を長く映しています。素材側で発話とみなされたのは {talking} 個で、それらを優先的に割り当てました。",
  "warn.story.settingsApplied": "説明文から適用した設定: {settings}",
  "warn.subtitles.borrowedProvider":
    "{recipe} レシピは文字起こしを行わないため、字幕には {provider} を使用しました",
  "warn.subtitles.failed":
    "{file} を文字起こしできなかったため字幕はありません（{detail}）。編集自体には影響ありません",
  "warn.build.oldPremiere":
    "この Premiere は {found} です。パネルは {needed} 以降を前提としているため、一部の処理は古い方式にフォールバックします。Premiere の更新が根本的な解決策です。",
  "warn.clips.noSubclipApi":
    "この Premiere はサブクリップを作成できないため、マスターに In/Out を設定して配置しました。クリップの長さがプランと一致しているか確認してください。この方法は一部のバージョンで正確でないことがあります。",
  "warn.subtitles.placed":
    "{file}: キャプション {count} 件をキャプショントラックに配置しました",
  "warn.subtitles.stale":
    "{file} は以前のビルドで既にプロジェクトに読み込まれています。古い方をビンから削除して読み込み直さないと、前の編集の字幕が表示されます",
  "warn.subtitles.wrongTrack":
    "{file} がキャプションではなくクリップとして配置されました（{how}）。Cmd-Z を 1 回押して取り消し、.srt をキャプショントラックへドラッグしてください",
  "warn.subtitles.dragToTrack":
    "{file} をプロジェクトに読み込みました。キャプショントラックへドラッグしてください。自動配置はこの環境では機能しませんでした（{detail}）。",
  "warn.subtitles.transcriptSchema":
    "Premiere の文字起こしスキーマを取得しました。キー {keys}、サンプル {sample}",
  "warn.subtitles.imported":
    "{file} を読み込みました。キャプショントラックに入っていない場合はドラッグしてください",
  "warn.subtitles.notImported":
    "{file} は書き出しましたが Premiere が読み込めませんでした（{detail}）。ファイル > 読み込み から手動で追加してください",
  "warn.speech.protected":
    "発話の途中で切れないよう、カット {count} 箇所を語の切れ目に移動しました",
  "warn.speech.paceTooFast":
    "語の途中のカットが、修正できたカットより多くなっています。このカット間隔ではショットが発話より短いため、間隔を長くするか「文を途中で切らない」をオフにしてください",
  "warn.speech.couldNotProtect":
    "カット {count} 箇所は語の途中のままです。その部分は途切れなく話しているため切れる隙間がありません",
  "warn.speech.beatsGaveWay":
    "文を最後まで残すことと拍ちょうどで切ることは両立しないため、一部のカットは拍からずれています",
  "warn.silence.removed":
    "無音 {seconds} 秒を削除し、発話の前後に最大 {allowed} 秒を残しました",
  "warn.silence.shotDropped":
    "このショットには発話がありません",
  "warn.silence.allDropped":
    "無音を削除すると編集が空になるため、そのままにしました。許容量を増やすかオフにしてください",
  "warn.subtitles.written":
    "字幕 {count} 件を {file} に書き出しました。Premiere の ファイル > 読み込み でキャプショントラックに追加できます",
  "warn.subtitles.none":
    "完成した編集に文字起こしのある素材が含まれていないため、字幕は書き出されませんでした",

  "warn.story.noRunningOrder":
    "説明文にショットの順序が書かれていないため、設定のみに使用しました。順序を指定するには「〜から始まり、次に〜」のように書いてください",
  "warn.story.beatUnfilled":
    "「{beat}」に合う映像が見つからなかったため、そのショットは編集に含まれていません。その分の尺は他のショットに配分しました",
  "warn.story.assembled":
    "指定された {total} ショットのうち {matched} ショットが映像内に見つかりました",
  "warn.story.nothingMatched":
    "指定されたショットが映像内に見つからなかったため、通常の方法で編集を作成しました",
  "warn.story.noModel":
    "画像認識モデルを利用できないため、説明文を使用できませんでした。通常の方法で編集を作成しました",
  "warn.story.noVisualSpans":
    "説明文で編集するには映像の解析が必要です。「映像からカット」を有効にしてください",
  "warn.timebase.chosen":
    "指定に従い、シーケンスを {chosen} fps に設定しました。",
  "warn.timebase.mixedRates":
    "{count} 個のクリップが {chosen}fps のシーケンスにぴったり収まらないため、つなぎ目が 1 フレームずれることがあります。{files} の編集点を確認してください",
  "warn.swap.candidatesTruncated":
    "{file} には使用できるショットが {found} 個ありますが、上位 {kept} 個のみを差し替え候補として表示します。記憶にあるショットが一覧にない場合があります",
  "warn.sequence.rateMismatch":
    "シーケンスは {actual}fps ですが、この編集案は {wanted}fps 用に作られています。クリップの長さが指定どおりになりません",
};

const CATALOGUES = { en: EN, ja: JA };
const LANGUAGES = [
  { value: "en", label: "English" },
  { value: "ja", label: "日本語" },
];

/** Numbers arrive as full floats; a warning does not want fifteen decimals. */
function formatValue(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) return String(value);
  if (Number.isInteger(value)) return String(value);
  return String(Math.round(value * 100) / 100);
}

/**
 * @param {string} lang
 * @param {string} key
 * @param {Record<string, any>} [params]
 * @param {string} [fallback] used when neither catalogue has the key
 */
function translate(lang, key, params, fallback) {
  const table = CATALOGUES[lang] || EN;
  const template = table[key] !== undefined ? table[key] : EN[key];
  if (template === undefined) return fallback !== undefined ? fallback : key;
  return String(template).replace(/\{(\w+)\}/g, (whole, name) => {
    if (!params || params[name] === undefined) return whole;
    return formatValue(params[name]);
  });
}

/**
 * Render an engine warning in the editor's language.
 *
 * Falls back to the English the engine already rendered, so a warning added to
 * the engine without a translation shows in English rather than disappearing —
 * and one written before this mechanism existed still shows at all.
 *
 * @param {string} lang
 * @param {string | {code?: string, message?: string, messageKey?: string, params?: object}} warning
 */
function translateWarning(lang, warning) {
  // Most warnings from the apply side are still plain English strings. Returning
  // "" for those would silently swallow them, which is the one outcome worse
  // than showing an editor a warning they cannot read.
  if (typeof warning === "string") return warning;
  const w = warning || {};
  const english = w.message || "";
  if (!w.messageKey) return english;
  return translate(lang, `warn.${w.messageKey}`, w.params, english);
}

/** Keys present in English but missing from another catalogue. */
function missingKeys(lang) {
  const table = CATALOGUES[lang] || {};
  return Object.keys(EN).filter((k) => table[k] === undefined);
}

/** @param {string} lang */
function makeTranslator(lang) {
  const t = (key, params, fallback) => translate(lang, key, params, fallback);
  t.warning = (warning) => translateWarning(lang, warning);
  t.lang = lang;
  return t;
}

module.exports = {
  EN, JA, CATALOGUES, LANGUAGES,
  translate, translateWarning, missingKeys, makeTranslator, formatValue,
};
