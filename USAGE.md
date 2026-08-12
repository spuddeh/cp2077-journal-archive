# Query cookbook

The shapes that answer real questions against `data/journal.db`. Each one is here because it
came up, not because it completes a matrix.

Run them however is convenient. `python explore.py` puts them in a browser - the SQL box takes
any of these, and the saved-query list carries the ones below. Or from a shell:

```bash
python -c "import sqlite3,sys; sys.stdout.reconfigure(encoding='utf-8'); [print(r) for r in sqlite3.connect('data/journal.db').execute('''<QUERY>''')]"
```

`sys.stdout.reconfigure(encoding='utf-8')` is not optional on Windows. Night City text is full
of `¡`, `…` and curly quotes, and the console default mangles them into `?` - which reads
exactly like corrupt data.

---

## 1. Find every mention of a name or term

```sql
SELECT e.id, e.kind, e.title
FROM search s JOIN entries e ON e.id = s.id
WHERE search MATCH 'blackwall'
ORDER BY rank LIMIT 20;
```

`OR` widens, a quoted string is a phrase, and `NEAR()` bounds the distance:

```sql
WHERE search MATCH 'denzel OR cryer'
WHERE search MATCH '"night city"'
WHERE search MATCH 'NEAR(arasaka tower, 5)'
```

Search a single name rather than a full one first. `'sabara OR jesse'` finds the target and
also three unrelated people called Jesse - which is the point, because a phrase search for
`"jesse sabara"` would have hidden that the surname appears exactly once.

## 2. Prove a search found everything

FTS tokenises, so a term inside a compound or with odd punctuation can be missed. When the
answer is "how many, exactly", sweep the JSONL as plain substrings:

```python
import json, glob
hits = [json.loads(l) for f in glob.glob('data/*.jsonl')
        for l in open(f, encoding='utf-8') if 'sabara' in l.lower()]
```

Slower, and it cannot be fooled. Use it to confirm a count before reporting one.

## 3. Read one entry whole

```sql
SELECT data FROM entries WHERE id = 'codex/glossary/world/blackwall';
```

`data` is the complete record as JSON, including the fields with no column of their own -
`lockeys`, `messages`, `blocks`, `phases`, `spoken_original`.

## 4. Read a scene in order

```sql
SELECT line, speaker, addressee, text FROM entries
WHERE scene = 'quest/q101/q101_07_ripperdoc' ORDER BY line;
```

The natural follow-up to any subtitle hit: one search result expands into its surrounding
conversation.

## 5. Everything one character says

```sql
SELECT scene, line, text FROM entries WHERE speaker_key = 'judy' ORDER BY scene, line;
```

**`speaker_key`, never `speaker`.** Scenes author the same name with inconsistent case, so
`speaker` splits Judy across `Judy` (881 lines) and `judy` (854).

## 6. One exchange between two named characters

```sql
SELECT scene, line, text FROM entries
WHERE speaker_key = 'johnny' AND addressee_key = 'alt' ORDER BY scene, line;
```

`addressee_key` is the field full-text search cannot substitute for. Everything Johnny says
*to* Alt as she dies is three lines in `q108_19_soulkiller`; the rest of that scene he aims
at other people, and only the addressee separates them.

## 7. Find where the hits cluster

```sql
SELECT scene, count(*) c FROM entries
WHERE speaker_key = 'johnny' AND addressee_key = 'alt' GROUP BY scene ORDER BY c DESC;
```

Before reading 97 lines, learn that 88 of them are in two scenes. Same shape answers "which
scene carries the most dialogue" and "who talks to whom":

```sql
SELECT speaker_key, addressee_key, count(*) c FROM entries
WHERE kind = 'subtitle' AND addressee_key <> '' GROUP BY 1, 2 ORDER BY c DESC LIMIT 20;
```

## 8. Names this database cannot find

A character's display name is a LocKey on a TweakDB record, and neither the record path nor
this database contains it. Jesse Sabara is `Character.ma_hey_spr_11_enemy_001`. Search the
string table itself:

```python
import json
for p in ['raw/onscreens_base.json', 'raw/onscreens_ep1.json']:
    for e in json.load(open(p, encoding='utf-8'))['Data']['RootChunk']['root']['Data']['entries']:
        if 'sabara' in (e.get('femaleVariant') or '').lower():
            print(e['primaryKey'], e.get('secondaryKey'))
```

The `secondaryKey` names the record the string belongs to, which is how a name gets back to
a TweakDB path.

## 9. The same questions from the console's search box

`python explore.py` takes `field:value` beside the search terms, which covers most of the
above without writing SQL:

```
blackwall                        full text, ranked
"night city"                     a phrase
denzel OR cryer                  either
NEAR(arasaka tower, 5)           within five words
speaker:johnny addressee:alt     shape 6, no SQL
speaker:"maximum mike" radio     a filter narrowing a full-text search
blackwall kind:shard             either order, anywhere in the box
```

Full text reaches `title` and `text`. Everything else - `speaker`, `addressee`, `kind`,
`source`, `scene`, `contact`, `category`, `quest_type`, `address`, `id` - filters, and
`speaker:`/`addressee:` match on the normalised key, so the case trap in shape 5 cannot
bite. A filter with no search term reads in authored order rather than by rank.

Anything else is the SQL tab, where `Ctrl`+`Space` completes column names.

---

## Traps

**A TweakDB search for a character's name returns zero, and that is not absence.** Names live
in LocKeys pointed at by `fullDisplayName`, never in the record path. Both Denzel Cryer and
Jesse Sabara return zero from `tweakdb_search` while having records in the game.

**Authored order is not chronological order.** Verifiable rather than merely claimed: Alt
recites the opening of *The Love Song of J. Alfred Prufrock* in `q116_05_mikoshi`, and its
first five lines sit at `line` 20, 2, 19, 3, 4. The poem fixes the correct order, so the
scramble is in the file. From line 141 the same poem runs in exact sequence, so the disorder
is local to how the opening was laid out.

**Self-addressed lines are a real category.** `v -> v` is 706 lines of internal monologue and
`maximum mike -> maximum mike` is 546 lines of a radio DJ alone in a booth. Filtering
`speaker_key = addressee_key` as noise throws away real dialogue.

**A count from this database is a count of text, not of presence.** Cryer appears in two
journal entries and two spoken lines, and also has twelve bespoke asset files. Asking
"all references" means `search_archive_files` and `tweakdb_search` as well.
