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
  "setup.musicFolder": "Music folder",
  "setup.notSet": "not set",
  "setup.notSetOptional": "not set (optional)",
  "setup.hint":
    "Plans store paths relative to a root, so moving to a NAS later means changing " +
    "these settings and nothing else. The music folder is optional and lives " +
    "wherever you keep your library — it does not have to sit with the footage.",
  "setup.language": "Panel language",

  // --- new edit ------------------------------------------------------------
  "edit.title": "New edit",
  "edit.name": "Name",
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
  "edit.look": "Look",
  "edit.spokenLanguage": "Spoken language",
  "edit.create": "Create edit",
  "edit.createHint": "Runs in the background. The plan appears below when it is ready.",
  "edit.visual": "Cut from the pictures, ignoring speech",
  "edit.visualHint": "For promos and b-roll. Leave off to cut to what is said.",
  "edit.noMediaRoot": "Set a media root above.",
  "edit.noVideoFiles": "No video files in the media root.",
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
  "plan.title": "Plan",
  "plan.refresh": "Refresh",
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
  "diag.title": "Diagnostics",
  "diag.selfTest": "Run self-test",
  "diag.selfTestHint":
    "Exercises the Premiere API against this build and writes a report to " +
    "/tmp/autoedit-selftest/report.json. Run this first on a new machine.",
  "log.title": "Log",

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
  "err.recipeRequired": "Choose a recipe",
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
  "warn.music.startPastEnd":
    "start {start}s is past the end of the {duration}s track; starting from the beginning instead",
  "warn.music.snappedToBeat": "start moved {moved}s to the nearest beat, at {start}s",
  "warn.music.chunkClamped":
    "asked for {wanted}s from {start}s but the track only has {available}s left, so the bed is {actual}s",
  "warn.music.runsPastPicture":
    "the music runs {overhang}s past the last frame of picture — extend the edit or shorten the chunk",
  "warn.music.stopsEarly": "the music stops {shortfall}s before the picture does",
  "warn.language.uncertain":
    "{file}: only {confidence} sure this is {language} — set the language in the panel if that is wrong",
  "warn.plan.lowConfidence":
    "{count} clip(s) scored low confidence — either speech the transcript was unsure about, or shots that only just passed the quality gates. Review those before trusting the cut.",
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
  "warn.timebase.mixedRates":
    "{count} clip(s) are not an exact fit for the {chosen}fps sequence, so those cuts can be a frame out — check the joins on {files}",
};

const JA = {
  // --- fixed choices -------------------------------------------------------
  "aspect.source": "元の比率のまま",
  "aspect.landscape": "横位置 16:9",
  "aspect.vertical": "縦位置 9:16",
  "aspect.square": "正方形 1:1",
  "aspect.portrait45": "縦位置 4:5",
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
  "setup.musicFolder": "音楽フォルダ",
  "setup.notSet": "未設定",
  "setup.notSetOptional": "未設定（任意）",
  "setup.hint":
    "プランはルートからの相対パスで保存されるため、将来 NAS に移す場合もこの設定を" +
    "変えるだけで済みます。音楽フォルダは任意で、素材と同じ場所になくても構いません。",
  "setup.language": "パネルの言語",

  // --- new edit ------------------------------------------------------------
  "edit.title": "新規編集",
  "edit.name": "名前",
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
  "edit.look": "カラー",
  "edit.spokenLanguage": "話されている言語",
  "edit.create": "編集を作成",
  "edit.createHint": "バックグラウンドで実行されます。完了するとプランが下に表示されます。",
  "edit.visual": "音声を無視して映像でカットする",
  "edit.visualHint": "プロモや B ロール向けです。オフにすると話した内容に合わせてカットします。",
  "edit.noMediaRoot": "上で素材フォルダを設定してください。",
  "edit.noVideoFiles": "素材フォルダに動画ファイルがありません。",
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
  "plan.title": "プラン",
  "plan.refresh": "更新",
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
  "diag.title": "診断",
  "diag.selfTest": "セルフテストを実行",
  "diag.selfTestHint":
    "このバージョンの Premiere API を検証し、/tmp/autoedit-selftest/report.json に" +
    "結果を書き出します。新しい PC では最初にこれを実行してください。",
  "log.title": "ログ",

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
  "err.recipeRequired": "レシピを選んでください",
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
  "warn.music.startPastEnd":
    "開始位置 {start} 秒は {duration} 秒の曲の終わりを超えています。先頭から再生します",
  "warn.music.snappedToBeat": "開始位置を {moved} 秒ずらし、最も近いビートの {start} 秒に合わせました",
  "warn.music.chunkClamped":
    "{start} 秒から {wanted} 秒を指定しましたが、曲の残りが {available} 秒しかないため {actual} 秒になりました",
  "warn.music.runsPastPicture":
    "音楽が映像の最後より {overhang} 秒長くなっています。編集を伸ばすか、音楽を短くしてください",
  "warn.music.stopsEarly": "音楽が映像より {shortfall} 秒早く終わります",
  "warn.language.uncertain":
    "{file}：{language} である確率は {confidence} です。異なる場合はパネルで言語を指定してください",
  "warn.plan.lowConfidence":
    "{count} 件のクリップの信頼度が低くなっています。文字起こしが不確かな音声か、画質の基準をぎりぎり満たしたショットです。書き出す前に該当箇所を確認してください。",
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
  "warn.timebase.mixedRates":
    "{count} 個のクリップが {chosen}fps のシーケンスにぴったり収まらないため、つなぎ目が 1 フレームずれることがあります。{files} の編集点を確認してください",
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
