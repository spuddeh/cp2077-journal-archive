# Cyberpunk 2077 text archive

Every word of text in the game: the journal resource - shards, phone conversations, codex
entries, computer mail and files, the in-game internet, the quest log, the tarot readings -
plus every line of spoken dialogue. Resolved against the en-us string tables and written as
JSONL plus a SQLite database with a full-text index.

Built for querying, not for browsing. One `SELECT` replaces reading thousands of files.

| Kind | Entries | What it is |
| --- | --- | --- |
| `subtitle` | 104,895 | Spoken dialogue, one record per line |
| `shard` | 1,804 | Shards and journal entries the player picks up |
| `mail` | 733 | Emails on computers and terminals |
| `internet` | 678 | Pages of the in-game internet |
| `sms` | 556 | Phone conversations, threaded, both speakers |
| `codex` | 486 | Database and lore entries, with their descriptions |
| `quest` | 393 | Quest log: description, phases, objectives |
| `file` | 328 | Non-email files on computers and terminals |
| `tarot` | 28 | Tarot card readings |

Base game and Phantom Liberty, tagged per entry by `source`.

## Using it

```bash
python build.py --db-only     # data/journal.db, about two seconds
python explore.py             # query console in the browser, http://127.0.0.1:8777
```

`explore.py` is a full-text box, a SQL box and a list of saved queries. An `id` in a result
opens the whole record, a `scene` reads it back in order, a speaker expands to everything
that character says. The database is opened read-only and the SQL box takes `SELECT`,
`WITH` and `EXPLAIN` only, so nothing typed there can damage the archive.

The search box takes `field:value` alongside the search terms - `speaker:johnny
addressee:alt`, `blackwall kind:shard`. Full text covers `title` and `text`; everything
else filters. `speaker:` and `addressee:` match the normalised key, so case does not
matter.

The SQL box is CodeMirror, with `Ctrl`+`Space` completing table and column names read from
the database itself. It lives in `vendor/codemirror/` (MIT, committed) so the console works
with no network. Python side is standard library only - no install step, and the page falls
back to a plain text box if the vendor directory is missing.

```sql
-- full text, ranked
SELECT id, kind, title FROM search
  WHERE search MATCH 'blackwall' ORDER BY rank LIMIT 20;

-- every thread with one contact
SELECT title, text FROM entries WHERE kind = 'sms' AND contact = 'Judy Alvarez';

-- a whole scene back in order, once a search has found one line of it
SELECT line, text FROM entries WHERE scene = 'quest/q101/q101_07_ripperdoc' ORDER BY line;

-- one entry with its full structure
SELECT data FROM entries WHERE id = 'codex/glossary/world/blackwall';
```

`entries.data` holds the complete record; the other columns are there so a query can
filter without parsing JSON. The `search` table indexes `title` and `text` only.

The JSONL files carry the same records, one per line, if a stream is easier than a query.

`USAGE.md` is the cookbook: the query shapes that answer real questions, and the traps that
make a wrong answer look like a right one.

## What is in a record

Common to every kind:

| Field | Meaning |
| --- | --- |
| `id` | Stable slug, `kind/journal/folder/chain/entry_id` |
| `kind` | One of the eight above |
| `source` | `base` or `ep1` |
| `journal_id` | The entry's own id in the journal resource |
| `path` | Its folder chain, as authored |
| `title`, `text` | Resolved English strings |
| `lockeys` | The LocKey numbers the strings came from |
| `loc_paths` | The `secondaryKey` naming where each string was authored |

`lockeys` is the field that makes this usable for modding: it is the identifier a mod
needs to reference or override a line, and it survives the game changing the text.

Kind-specific fields include `contact` and `messages` on `sms`, `sender` and `addressee`
on `mail`, `address` and `blocks` on `internet`, and `phases` with nested `objectives`
on `quest`.

`subtitle` records carry `scene` and `line` instead of `lockeys` - a subtitle is keyed by a
64-bit `string_id`, not a LocKey, and lives in a per-scene resource. Two more appear when
the source line needs them:

- `text_male` - V is voiced twice and about 5% of lines differ by the player's gender, so
  `text` is the feminine reading and `text_male` the masculine one.
- `spoken_original` - the untranslated foreign line, when `text` is what a Kiroshi implant
  renders. `<kiroshi>` carries a translation and `<mothertongue>` deliberately does not, so
  a mothertongue line stays in its own language in `text`, exactly as the player sees it.

Spoken lines also carry `speaker`, `addressee` and `speaker_key`, which come from the
`.scene` files rather than the subtitle resource - see below. `is_choice` marks V's
dialogue options, which are a separate list in the screenplay and have no speaker of their
own.

## Who says a line

A subtitle resource names no speaker. A `.scene` does: every `scnscreenplayDialogLine`
carries the subtitle's own id in `locstringId.ruid`, alongside `speaker` and `addressee`
actor ids that resolve against that scene's `actors` list. Sweeping all 8,553 scenes
attributes **104,798 of 104,895 lines**, and gives who each line is aimed at for free.

```sql
-- everything one character says
SELECT scene, text FROM entries WHERE speaker_key = 'judy';

-- who talks to whom, most first
SELECT speaker_key, addressee_key, count(*) c FROM entries
  WHERE kind = 'subtitle' AND addressee_key <> '' GROUP BY 1, 2 ORDER BY c DESC LIMIT 20;
```

**Group on `speaker_key`, not `speaker`.** Scenes author the same character inconsistently -
`Johnny` and `johnny`, `Panam` and `panam` - so the authored name splits one character
across rows. `speaker` keeps the value as written; `speaker_key` is the lowercased,
space-normalised form to group on. There are 3,403 distinct speakers.

## Limits

**Prop ids are a separate id space from actor ids.** Folding `props` into the actor name
table lets a prop overwrite an actor of the same number, which attributes hundreds of lines
to a chair, a cigarette and a coin. The sweep reads `actors` and `playerActors` only.

**97 lines have no speaker.** Their string ids appear in no scene's screenplay.

**Conversation order is authored order, not chronological order.** The journal stores a
thread's messages and choice groups in the order the writers laid them out in the editor.
The runtime picks a path through them from quest facts, so a reply can appear in the file
before the message it answers. Reconstructing true chronology needs the quest graph, which
this does not read. Subtitle `line` numbers are within-file order and carry the same caveat.

**English only.** The other 18 languages are the same journal against a different
`onscreens` file; the build takes a language by swapping two input paths.

**`maleVariant` is discarded.** Every entry checked carries its text in `femaleVariant`
with `maleVariant` empty; the build falls back to `maleVariant` when the first is blank.

## Regenerating from the game

`build.py` reads four WolvenKit-JSON files that are not committed, because they are
118 MB and derive entirely from the game install. Produce them with the wolvenkit MCP:

| Game path | Save to |
| --- | --- |
| `base\journal\cooked_journal.journal` | `raw/journal_base.json` |
| `ep1\journal\cooked_journal.journal` | `raw/journal_ep1.json` |
| `base\localization\en-us\onscreens\onscreens_final.json` | `raw/onscreens_base.json` |
| `ep1\localization\en-us\onscreens\onscreens_final.json` | `raw/onscreens_ep1.json` |

All four go through `convert_to_json` - the localization files are cooked CR2W despite
the `.json` extension, so extracting them raw yields binary.

Spoken dialogue is a fifth input, `raw/subtitles_en_us.jsonl`, and it is not four files but
3,800 - one subtitle resource per scene. Sweeping them one call at a time is not viable, so
`sweep_subtitles.wscript` does the whole set inside WolvenKit in about eight seconds. Run it
through the MCP's `run_wscript` and copy its output into `raw/`. The build skips subtitles
and says so if that file is absent.

Speakers are a sixth input, `raw/speakers*.jsonl`, from `sweep_speakers.wscript` over the
8,553 `.scene` files. That is roughly 24 GB of JSON to parse, so the script runs in batches -
`base\quest\`, `base\open_world\`, then everything else - about four minutes in total. The
build merges every `speakers*.jsonl` in `raw/`, and attributes nothing if none are present.

```bash
python build.py               # full rebuild
```

Re-run it after a game patch. The counts above come from game version 2310.

## Relation to the Nexus archive

This started from *Global Text Archive* by Kontrust87, which ships the same idea as
loose `.txt` files across 19 languages. Its categories 04 (SMS) and 05 (Codex) are
published as broken. Both causes are in the journal's shape:

- A codex entry holds only its title. The body is a child `gameJournalCodexDescription`
  node under the key `textContent`, so a search for `description` / `text` / `content` on
  the entry itself finds nothing.
- A phone message has no `isPlayer` field. The speaker is `sender`, and V's replies are
  `gameJournalPhoneChoiceEntry` nodes inside a choice group - a different type entirely.

This build reads the journal from the game rather than reusing that extraction.
