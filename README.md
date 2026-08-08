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
```

```sql
-- full text, ranked
SELECT id, kind, title FROM search
  WHERE search MATCH 'blackwall' ORDER BY rank LIMIT 20;

-- every thread with one contact
SELECT title, text FROM entries WHERE kind = 'sms' AND contact = 'Judy Alvarez';

-- a whole scene back in order, once a search has found one line of it
SELECT line, text FROM entries WHERE scene = 'quest/q101/q101_01_meeting_dex' ORDER BY line;

-- one entry with its full structure
SELECT data FROM entries WHERE id = 'codex/glossary/world/blackwall';
```

`entries.data` holds the complete record; the other columns are there so a query can
filter without parsing JSON. The `search` table indexes `title` and `text` only.

The JSONL files carry the same records, one per line, if a stream is easier than a query.

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

## Limits

**A subtitle does not name its speaker.** The resource holds text and a string id, nothing
else; the scene path is the only attribution there is. Naming speakers means reading the
`.scene` files, which this does not do.

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
