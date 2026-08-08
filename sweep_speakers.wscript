// Sweep who says each line out of the .scene files.
//
// A subtitle resource names no speaker. A .scene does: every scnscreenplayDialogLine
// carries the subtitle's own id in locstringId.ruid, alongside `speaker` and `addressee`
// actor ids that resolve against the scene's `actors` list. scnscreenplayChoiceOption
// entries are V's dialogue options and carry no speaker, because they never need one.
//
// 8,553 scenes at roughly 2.8 MB of JSON each is ~24 GB to parse, which does not fit one
// run. Change NEEDLE and OUT and run it once per batch; build.py merges every
// raw/speakers*.jsonl it finds.
//
//     base\quest\        base\open_world\        base\media\        ep1\        dlc\

const NEEDLE = 'base\\quest\\';
const OUT = 'speakers_base_quest.jsonl';

let scenes = 0, errs = 0, rows = 0;
const out = [];

for (const f of wkit.GetArchiveFiles()) {
    const p = f.FileName ?? f.Name;
    if (!p) continue;
    const lp = p.toLowerCase();
    if (!lp.startsWith(NEEDLE) || !lp.endsWith('.scene')) continue;
    try {
        const root = JSON.parse(wkit.GameFileToJson(wkit.GetFileFromArchive(p, OpenAs.GameFile))).Data.RootChunk;

        // Actor ids are scene-local, so the name table is rebuilt per file. A player actor
        // has no actorName - it is whoever V is.
        //
        // PROPS ARE EXCLUDED, and that is not an omission. `props` is a separate id space
        // from `actors`, so folding both into one table lets a prop id overwrite an actor
        // of the same number - which is how a chair, a cigarette and a coin end up listed
        // as speakers of hundreds of lines.
        const names = {};
        for (const grp of ['actors', 'playerActors']) {
            for (const a of root[grp] || []) {
                if (!a.actorId) continue;
                const nm = a.actorName;
                names[a.actorId.id] = (nm && nm !== 'None') ? nm : (grp === 'playerActors' ? 'V' : null);
            }
        }

        const sp = root.screenplayStore || {};
        for (const ln of sp.lines || []) {
            out.push(JSON.stringify({
                r: ln.locstringId.ruid,
                s: names[(ln.speaker || {}).id] ?? null,
                a: names[(ln.addressee || {}).id] ?? null,
                p: p
            }));
            rows++;
        }
        for (const o of sp.options || []) {
            out.push(JSON.stringify({ r: o.locstringId.ruid, s: 'V', a: null, c: 1, p: p }));
            rows++;
        }
        scenes++;
    } catch (ex) {
        // Not every .scene parses. Count it and carry on rather than losing the batch.
        errs++;
    }
}

wkit.SaveToRaw(OUT, out.join('\n'));
logger.Info(OUT + ' scenes=' + scenes + ' errors=' + errs + ' rows=' + rows);
