// Sweep every en-us subtitle resource into one JSONL for build.py.
//
// There are 3,800 of them - one per scene - so they cannot go through the MCP a call at a
// time. This runs the whole set inside WolvenKit in about eight seconds.
//
// Run it with the wolvenkit MCP's run_wscript, then copy the output into raw/.

const NEEDLE = '\\localization\\en-us\\subtitles\\';

let files = 0, errs = 0, lines = 0;
const out = [];

for (const f of wkit.GetArchiveFiles()) {
    const p = f.FileName ?? f.Name;
    if (!p) continue;
    const lp = p.toLowerCase();
    if (!lp.includes(NEEDLE) || !lp.endsWith('.json')) continue;
    try {
        const doc = JSON.parse(wkit.GameFileToJson(wkit.GetFileFromArchive(p, OpenAs.GameFile)));
        const root = doc && doc.Data && doc.Data.RootChunk && doc.Data.RootChunk.root;
        const entries = (root && root.Data && root.Data.entries) || [];
        for (const e of entries) {
            if (!e.femaleVariant && !e.maleVariant) continue;
            out.push(JSON.stringify({ p: p, s: e.stringId, f: e.femaleVariant, m: e.maleVariant }));
            lines++;
        }
        files++;
    } catch (ex) {
        // Not every file under the tree is CR2W. Count it and carry on rather than
        // losing the whole sweep to one bad read.
        errs++;
    }
}

wkit.SaveToRaw('subtitles_en_us.jsonl', out.join('\n'));
logger.Info('files=' + files + ' errors=' + errs + ' lines=' + lines);
