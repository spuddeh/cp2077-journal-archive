// Sweep the subtitle text that scenes carry INSIDE themselves, which sweep_subtitles.wscript misses.
//
// Most scenes keep their subtitles in a separate resource under \localization\en-us\subtitles\.
// Some carry them in the scene's own locStore instead, and those have no subtitle resource at all -
// ep1\quest\main_quests\q301\scenes\q301_00_holocall_hook.scene is one, with 168 en_us entries and
// nothing under the subtitles tree. Sweeping only the resources loses every line in those scenes.
//
// locStore holds two parallel lists. A vdEntry names a locale, a locstringId and a variantId; the
// vpEntry with that variantId holds the text. signature.val separates the takes:
//
//   1 = male       2 = female       3 = one take for both
//
// Measured against a line already in the archive: sig 1 matched text_male and sig 2 matched text,
// which is the reverse of the order the field names suggest.
//
// Run it with the wolvenkit MCP's run_wscript, then copy the output into raw/.

const OUT = [];
let files = 0, withStore = 0, errs = 0, lines = 0;

for (const f of wkit.GetArchiveFiles()) {
    const p = f.FileName ?? f.Name;
    if (!p || !p.toLowerCase().endsWith('.scene')) continue;
    if (p.toLowerCase().includes('\\versions\\')) continue;   // superseded patch copies
    files++;
    try {
        const doc = JSON.parse(wkit.GameFileToJson(wkit.GetFileFromArchive(p, OpenAs.GameFile)));
        const store = doc?.Data?.RootChunk?.locStore;
        const vd = store?.vdEntries || [];
        const vp = store?.vpEntries || [];
        if (!vd.length) continue;

        // vpeIndex, never variantId. A ruid is larger than Number.MAX_SAFE_INTEGER, so using one as
        // an object key rounds it: distinct variants collide and a line gets another line's text.
        // The symptom is content from the wrong locale - Polish, or a run of NULs.
        const bySid = {};
        for (const e of vd) {
            if (e?.localeId !== 'en_us') continue;
            const sid = String(e?.locstringId?.ruid ?? '');
            const idx = e?.vpeIndex;
            const content = (idx !== undefined && idx >= 0 && idx < vp.length) ? vp[idx]?.content : undefined;
            if (!sid || content === undefined || content === '') continue;
            (bySid[sid] ??= {})[String(e?.signature?.val)] = content;
        }

        let emitted = 0;
        for (const sid of Object.keys(bySid)) {
            const v = bySid[sid];
            const female = v['2'] ?? v['3'] ?? null;
            const male = v['1'] ?? v['3'] ?? null;
            if (female === null && male === null) continue;
            OUT.push(JSON.stringify({ p: p, s: sid, f: female, m: male }));
            emitted++;
        }
        if (emitted) { withStore++; lines += emitted; }
    } catch (ex) {
        // Not every .scene converts. Count it and carry on rather than losing the whole sweep.
        errs++;
    }
}

wkit.SaveToRaw('scene_subtitles_en_us.jsonl', OUT.join('\n'));
logger.Info('scenes=' + files + ' withEmbeddedText=' + withStore + ' errors=' + errs + ' lines=' + lines);
