"""Build a queryable dataset from Cyberpunk 2077's journal resource.

Input is the WolvenKit-JSON form of four game files (see README for the extraction
commands). Output is one JSONL per entry kind plus a SQLite database with an FTS5
index over every title and body.

    python build.py --raw raw --out data
"""

import argparse
import json
import os
import re
import sqlite3
import sys

# Journal node types that carry player-readable text, mapped to the kind they
# become in the output. A type absent from this map is structure, not content.
KIND_OF_NODE = {
    "gameJournalOnscreen": "shard",
    "gameJournalCodexEntry": "codex",
    "gameJournalEmail": "mail",
    "gameJournalFile": "file",
    "gameJournalContact": "sms",
    "gameJournalInternetPage": "internet",
    "gameJournalQuest": "quest",
    "gameJournalTarot": "tarot",
}

# Child collections that hold entries. `entries` is the journal's own tree; the
# rest hang off internet pages, which do not use `entries` at all.
CHILD_KEYS = ("entries",)


# --------------------------------------------------------------------------
# Localization
# --------------------------------------------------------------------------

class LocTable:
    """LocKey -> English string, plus the secondaryKey that names each entry.

    The secondaryKey is a semantic path such as
    `Story-base-gameplay-static_data-database-...`. It says where a string was
    authored, which is often the only clue to what an untitled entry is.
    """

    def __init__(self):
        self.text = {}
        self.origin = {}

    def load(self, path):
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        entries = doc["Data"]["RootChunk"]["root"]["Data"]["entries"]
        for e in entries:
            key = str(e.get("primaryKey", "")).strip()
            if not key:
                continue
            value = e.get("femaleVariant") or e.get("maleVariant") or ""
            self.text[key] = value
            secondary = e.get("secondaryKey") or ""
            if secondary:
                self.origin[key] = secondary
        return len(entries)

    def resolve(self, field):
        """Resolve a localization field to (text, lockey, origin).

        A field is `{"unk1": ..., "value": "LocKey#12345"}`. The value is
        occasionally a literal string rather than a key, so both are handled.
        """
        if not isinstance(field, dict):
            return "", None, None
        raw = field.get("value")
        if raw is None:
            return "", None, None
        raw = str(raw).strip()
        if not raw:
            return "", None, None
        m = re.match(r"(?i)^lockey#(.+)$", raw)
        if not m:
            return unescape(raw), None, None
        key = m.group(1).strip()
        return unescape(self.text.get(key, "")), key, self.origin.get(key)


def unescape(value):
    """The journal stores newlines as the two characters backslash-n."""
    return value.replace("\\n", "\n").replace("\r\n", "\n").strip()


# --------------------------------------------------------------------------
# Tree walking
# --------------------------------------------------------------------------

def children(node):
    for key in CHILD_KEYS:
        for handle in node.get(key) or []:
            if isinstance(handle, dict) and isinstance(handle.get("Data"), dict):
                yield handle["Data"]


def child_of_type(node, node_type):
    for c in children(node):
        if c.get("$type") == node_type:
            return c
    return None


def slug(value):
    value = re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_")
    return value or "unnamed"


# --------------------------------------------------------------------------
# Per-kind extraction
# --------------------------------------------------------------------------

class Builder:
    def __init__(self, loc):
        self.loc = loc
        self.records = []
        self.skipped = 0

    def add(self, rec):
        self.records.append(rec)

    def run(self, root, source):
        self.walk(root, source, [])

    def walk(self, node, source, path):
        node_type = node.get("$type", "")
        node_id = node.get("id", "")
        kind = KIND_OF_NODE.get(node_type)

        if kind:
            handler = getattr(self, "build_" + kind)
            handler(node, source, path)
            # A handled node owns its whole subtree; nothing below is a
            # separate top-level record.
            if kind != "internet":
                return

        next_path = path + [node_id] if node_id else path
        for c in children(node):
            self.walk(c, source, next_path)

    # -- shards -----------------------------------------------------------

    def build_shard(self, node, source, path):
        title, t_key, t_org = self.loc.resolve(node.get("title"))
        body, b_key, b_org = self.loc.resolve(node.get("description"))
        if not (title or body):
            self.skipped += 1
            return
        self.add({
            "id": f"shard/{'/'.join(path[1:] + [node.get('id', '')])}".rstrip("/"),
            "kind": "shard",
            "source": source,
            "node_type": "gameJournalOnscreen",
            "journal_id": node.get("id", ""),
            "path": path[1:],
            "category": path[-1] if len(path) > 1 else "",
            "title": title,
            "text": body,
            "tag": node.get("tag") or "",
            "icon_id": tweak(node.get("iconID")),
            "lockeys": drop_none({"title": t_key, "text": b_key}),
            "loc_paths": drop_none({"title": t_org, "text": b_org}),
        })

    # -- codex ------------------------------------------------------------

    def build_codex(self, node, source, path):
        title, t_key, t_org = self.loc.resolve(node.get("title"))
        desc = child_of_type(node, "gameJournalCodexDescription")
        body, b_key, b_org, subtitle = "", None, None, ""
        if desc is not None:
            body, b_key, b_org = self.loc.resolve(desc.get("textContent"))
            subtitle = self.loc.resolve(desc.get("subTitle"))[0]
        if not (title or body):
            self.skipped += 1
            return
        self.add({
            "id": f"codex/{'/'.join(path[1:] + [node.get('id', '')])}".rstrip("/"),
            "kind": "codex",
            "source": source,
            "node_type": "gameJournalCodexEntry",
            "journal_id": node.get("id", ""),
            "path": path[1:],
            "category": path[1] if len(path) > 1 else "",
            "group": path[2] if len(path) > 2 else "",
            "title": title,
            "subtitle": subtitle,
            "text": body,
            "lockeys": drop_none({"title": t_key, "text": b_key}),
            "loc_paths": drop_none({"title": t_org, "text": b_org}),
        })

    # -- computer mail and files -----------------------------------------

    def build_mail(self, node, source, path):
        self._build_document(node, source, path, "mail", "gameJournalEmail")

    def build_file(self, node, source, path):
        self._build_document(node, source, path, "file", "gameJournalFile")

    def _build_document(self, node, source, path, kind, node_type):
        title, t_key, t_org = self.loc.resolve(node.get("title"))
        body, b_key, b_org = self.loc.resolve(node.get("content"))
        if not (title or body):
            self.skipped += 1
            return
        rec = {
            "id": f"{kind}/{'/'.join(path[1:] + [node.get('id', '')])}".rstrip("/"),
            "kind": kind,
            "source": source,
            "node_type": node_type,
            "journal_id": node.get("id", ""),
            "path": path[1:],
            "group": path[-1] if len(path) > 1 else "",
            "title": title,
            "text": body,
            "lockeys": drop_none({"title": t_key, "text": b_key}),
            "loc_paths": drop_none({"title": t_org, "text": b_org}),
        }
        if node_type == "gameJournalEmail":
            rec["sender"] = self.loc.resolve(node.get("sender"))[0]
            rec["addressee"] = self.loc.resolve(node.get("addressee"))[0]
        self.add(rec)

    # -- SMS threads ------------------------------------------------------

    def build_sms(self, node, source, path):
        """One record per conversation, messages in authored order.

        The contact's display name is a lockey on the contact node; V's replies
        are `gameJournalPhoneChoiceEntry` nodes inside a choice group, and the
        speaker is the `sender` field, not an `isPlayer` flag.
        """
        contact_id = node.get("id", "")
        contact_name = self.loc.resolve(node.get("name"))[0] or pretty(contact_id)

        for conv in children(node):
            if conv.get("$type") != "gameJournalPhoneConversation":
                continue
            thread, seq = [], 0
            for item in children(conv):
                itype = item.get("$type")
                if itype == "gameJournalPhoneMessage":
                    text, key, org = self.loc.resolve(item.get("text"))
                    if not text:
                        continue
                    thread.append({
                        "seq": seq,
                        "kind": "message",
                        "sender": item.get("sender") or "NPC",
                        "id": item.get("id", ""),
                        "text": text,
                        "lockey": key,
                        "loc_path": org,
                        "quest_important": bool(item.get("isQuestImportant")),
                        "delay": item.get("delay"),
                    })
                    seq += 1
                elif itype == "gameJournalPhoneChoiceGroup":
                    options = []
                    for opt in children(item):
                        if opt.get("$type") != "gameJournalPhoneChoiceEntry":
                            continue
                        text, key, org = self.loc.resolve(opt.get("text"))
                        if not text:
                            continue
                        options.append({
                            "id": opt.get("id", ""),
                            "text": text,
                            "lockey": key,
                            "loc_path": org,
                            "quest_important": bool(opt.get("isQuestImportant")),
                        })
                    if not options:
                        continue
                    thread.append({
                        "seq": seq,
                        "kind": "choice",
                        "sender": "Player",
                        "id": item.get("id", ""),
                        "options": options,
                    })
                    seq += 1

            if not thread:
                self.skipped += 1
                continue

            title = self.loc.resolve(conv.get("title"))[0]
            self.add({
                "id": f"sms/{slug(contact_id)}/{conv.get('id', '')}".rstrip("/"),
                "kind": "sms",
                "source": source,
                "node_type": "gameJournalPhoneConversation",
                "journal_id": conv.get("id", ""),
                "path": path[1:] + [contact_id],
                "contact": contact_name,
                "contact_id": contact_id,
                "contact_type": node.get("type") or "",
                "title": title or f"{contact_name}: {conv.get('id', '')}",
                "text": render_thread(contact_name, thread),
                "messages": thread,
                "message_count": len(thread),
            })

    # -- in-game internet -------------------------------------------------

    def build_internet(self, node, source, path):
        """A page's readable content is its `texts` array, which is not part of
        the `entries` tree, so it is collected explicitly.
        """
        blocks = []
        for handle in node.get("texts") or []:
            t = handle.get("Data") if isinstance(handle, dict) else None
            if not isinstance(t, dict) or t.get("$type") != "gameJournalInternetText":
                continue
            text, key, org = self.loc.resolve(t.get("text"))
            if not text:
                continue
            blocks.append({
                "name": cname(t.get("name")),
                "text": text,
                "lockey": key,
                "loc_path": org,
                "link": t.get("linkAddress") or "",
            })
        if not blocks:
            self.skipped += 1
            return
        body = "\n\n".join(b["text"] for b in blocks)
        headers = [b["text"] for b in blocks if "header" in b["name"].lower()]
        self.add({
            "id": f"internet/{'/'.join(path[1:] + [node.get('id', '')])}".rstrip("/"),
            "kind": "internet",
            "source": source,
            "node_type": "gameJournalInternetPage",
            "journal_id": node.get("id", ""),
            "path": path[1:],
            "site": path[1] if len(path) > 1 else "",
            "address": node.get("address") or "",
            "title": headers[0] if headers else (node.get("address") or node.get("id", "")),
            "text": body,
            "blocks": blocks,
            "block_count": len(blocks),
        })

    # -- quests -----------------------------------------------------------

    def build_quest(self, node, source, path):
        title, t_key, t_org = self.loc.resolve(node.get("title"))
        desc_node = child_of_type(node, "gameJournalQuestDescription")
        desc = self.loc.resolve(desc_node.get("description"))[0] if desc_node else ""

        phases = []
        for phase in children(node):
            if phase.get("$type") != "gameJournalQuestPhase":
                continue
            objectives = []
            for obj in children(phase):
                if obj.get("$type") != "gameJournalQuestObjective":
                    continue
                otext, okey, oorg = self.loc.resolve(obj.get("description"))
                if not otext:
                    continue
                objectives.append({
                    "id": obj.get("id", ""),
                    "text": otext,
                    "lockey": okey,
                    "loc_path": oorg,
                    "optional": bool(obj.get("optional")),
                    "counter": obj.get("counter"),
                })
            if objectives:
                phases.append({"id": phase.get("id", ""), "objectives": objectives})

        if not (title or desc or phases):
            self.skipped += 1
            return

        lines = [desc] if desc else []
        for p in phases:
            lines.append(f"[{p['id']}]")
            for o in p["objectives"]:
                lines.append(("- (optional) " if o["optional"] else "- ") + o["text"])
        self.add({
            "id": f"quest/{'/'.join(path[1:] + [node.get('id', '')])}".rstrip("/"),
            "kind": "quest",
            "source": source,
            "node_type": "gameJournalQuest",
            "journal_id": node.get("id", ""),
            "path": path[1:],
            "quest_type": node.get("type") or "",
            "district_id": node.get("districtID") or "",
            "recommended_level": tweak(node.get("recommendedLevelID")),
            "title": title,
            "description": desc,
            "text": "\n".join(lines),
            "phases": phases,
            "objective_count": sum(len(p["objectives"]) for p in phases),
            "lockeys": drop_none({"title": t_key}),
            "loc_paths": drop_none({"title": t_org}),
        })

    # -- tarot ------------------------------------------------------------

    def build_tarot(self, node, source, path):
        name, n_key, n_org = self.loc.resolve(node.get("name"))
        body, b_key, b_org = self.loc.resolve(node.get("description"))
        if not (name or body):
            self.skipped += 1
            return
        self.add({
            "id": f"tarot/{node.get('id', '')}",
            "kind": "tarot",
            "source": source,
            "node_type": "gameJournalTarot",
            "journal_id": node.get("id", ""),
            "path": path[1:],
            "index": node.get("index"),
            "title": name,
            "text": body,
            "lockeys": drop_none({"title": n_key, "text": b_key}),
            "loc_paths": drop_none({"title": n_org, "text": b_org}),
        })


# --------------------------------------------------------------------------
# Spoken dialogue
# --------------------------------------------------------------------------

# Two inline tags wrap foreign speech, and they mean opposite things.
#   <kiroshi      l="mex" o="Salud, amiga." t="Cheers, friend." b="" a=""/>
#   <mothertongue l="mex" m="Fuera, chica"                     b="" a=". Get her outside!"/>
# A Kiroshi implant translates, so `t` is what the player reads and `o` is what is
# actually spoken. Mothertongue has no `t` at all - the line stays untranslated on
# screen by design, and `m` is what is shown.
TAG = re.compile(r"<\s*(kiroshi|mothertongue)\b([^>]*?)/?\s*>", re.IGNORECASE)
ATTR = re.compile(r'(\w+)\s*=\s*"([^"]*)"')


def render_line(value):
    """Resolve the inline tags to what the player reads, and report the spoken original."""
    if not value:
        return "", None
    spoken = []

    def replace(m):
        tag = m.group(1).lower()
        attrs = dict(ATTR.findall(m.group(2)))
        before, after = attrs.get("b", ""), attrs.get("a", "")
        if tag == "kiroshi":
            shown = attrs.get("t", "")
            if attrs.get("o"):
                spoken.append(attrs["o"])
        else:
            shown = attrs.get("m", "")
        return f"{before}{shown}{after}"

    text = TAG.sub(replace, value).strip()
    return unescape(text), (" / ".join(spoken) if spoken else None)


def load_speakers(raw_dir):
    """ruid -> who says the line, swept out of the .scene files.

    A subtitle resource names no speaker. The `.scene` files do: a
    `scnscreenplayDialogLine` carries the same id in `locstringId.ruid` alongside
    `speaker` and `addressee` actor ids, which resolve against the scene's own
    `actors` list. `scnscreenplayChoiceOption` entries are V's dialogue options and
    carry no speaker because they never need one.

    A ruid can appear in more than one scene, so the scene's own basename breaks the
    tie before anything else is considered.
    """
    by_ruid = {}
    for name in sorted(os.listdir(raw_dir)):
        if not (name.startswith("speakers") and name.endswith(".jsonl")):
            continue
        with open(os.path.join(raw_dir, name), encoding="utf-8") as f:
            for raw_line in f:
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                row = json.loads(raw_line)
                stem = os.path.splitext(os.path.basename(row["p"]))[0].lower()
                by_ruid.setdefault(row["r"], []).append({
                    "speaker": row.get("s"),
                    "addressee": row.get("a"),
                    "is_choice": bool(row.get("c")),
                    "stem": stem,
                })
    return by_ruid


def speaker_key(name):
    """Group key for an actor name, which scenes author with inconsistent case."""
    return re.sub(r"[\s_]+", " ", str(name)).strip().lower()


def pick_speaker(candidates, scene):
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    stem = scene.rsplit("/", 1)[-1].lower()
    for c in candidates:
        if c["stem"] == stem:
            return c
    named = [c for c in candidates if c["speaker"]]
    pool = named or candidates
    if len({c["speaker"] for c in pool}) == 1:
        return pool[0]
    return dict(pool[0], ambiguous=True)


def is_corrupt(text):
    """True when a payload is not text.

    Some embedded scene payloads ship as a run of NUL bytes, or as a fragment still carrying
    another locale's tag with its first byte overwritten. The corruption is in the shipped file.
    """
    return bool(text) and chr(0) in text


def build_subtitles(path, speakers=None):
    """One record per line, not per scene.

    A scene file runs to hundreds of lines, so a scene-level record would answer
    "which conversation mentions this" when the question is "which line".
    """
    speakers = speakers or {}
    records = []
    corrupt = []
    prefix = re.compile(r"^(base|ep1)\\localization\\en-us\\subtitles\\", re.IGNORECASE)
    # A scene carrying its own text has no resource under the subtitles tree, so its rows arrive
    # keyed by the .scene path instead.
    scene_prefix = re.compile(r"^(base|ep1)\\", re.IGNORECASE)
    per_scene = {}
    with open(path, encoding="utf-8") as f:
        for raw_line in f:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            row = json.loads(raw_line)
            game_path = row["p"]
            source = game_path.split("\\", 1)[0].lower()
            if game_path.lower().endswith(".scene"):
                scene = scene_prefix.sub("", game_path).removesuffix(".scene").replace("\\", "/")
            else:
                scene = prefix.sub("", game_path).removesuffix(".json").replace("\\", "/")
            index = per_scene.get(scene, 0)
            per_scene[scene] = index + 1

            if is_corrupt(row.get("f")) or is_corrupt(row.get("m")):
                corrupt.append(row)
                continue
            text, spoken = render_line(row.get("f"))
            male, _ = render_line(row.get("m"))
            if not text and not male:
                continue
            if not text:
                text, male = male, ""

            rec = {
                "id": f"subtitle/{scene}/{row['s']}",
                "kind": "subtitle",
                "source": source,
                "node_type": "localizationPersistenceSubtitleEntry",
                "journal_id": str(row["s"]),
                "path": scene.split("/")[:-1],
                "scene": scene,
                "category": scene.split("/")[0],
                "line": index,
                "string_id": str(row["s"]),
                "title": scene.rsplit("/", 1)[-1],
                "text": text,
            }
            # V is voiced twice, and ~5% of lines differ by the player's gender.
            if male and male != text:
                rec["text_male"] = male
            if spoken:
                rec["spoken_original"] = spoken

            who = pick_speaker(speakers.get(str(row["s"])), scene)
            if who:
                if who["is_choice"]:
                    rec["speaker"] = "V"
                    rec["is_choice"] = True
                else:
                    if who["speaker"]:
                        rec["speaker"] = who["speaker"]
                    if who["addressee"]:
                        rec["addressee"] = who["addressee"]
                if who.get("ambiguous"):
                    rec["speaker_ambiguous"] = True
            # Scenes name the same character inconsistently - Johnny and johnny, Panam and
            # panam - so grouping on the authored name splits a character across rows.
            if rec.get("speaker"):
                rec["speaker_key"] = speaker_key(rec["speaker"])
            if rec.get("addressee"):
                rec["addressee_key"] = speaker_key(rec["addressee"])
            records.append(rec)
    if corrupt:
        print(f"  dropped {len(corrupt):,} payloads the game ships corrupt "
              f"(see report_corrupt_payloads.py)")
    return records


def render_thread(contact, thread):
    """Flatten a conversation into readable text so full-text search hits it."""
    lines = []
    for item in thread:
        if item["kind"] == "message":
            who = contact if item["sender"] != "Player" else "V"
            lines.append(f"{who}: {item['text']}")
        else:
            for opt in item["options"]:
                lines.append(f"V [choice]: {opt['text']}")
    return "\n".join(lines)


def pretty(value):
    return " ".join(w.capitalize() for w in str(value).split("_")) if value else ""


def cname(field):
    if isinstance(field, dict):
        return str(field.get("$value", ""))
    return str(field or "")


def tweak(field):
    if isinstance(field, dict):
        value = str(field.get("$value", ""))
        return "" if value in ("0", "") else value
    return ""


def drop_none(mapping):
    return {k: v for k, v in mapping.items() if v}


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

def write_jsonl(records, path):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def write_sqlite(records, path):
    if os.path.exists(path):
        os.remove(path)
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE entries (
            id            TEXT PRIMARY KEY,
            kind          TEXT NOT NULL,
            source        TEXT NOT NULL,
            journal_id    TEXT,
            path          TEXT,
            title         TEXT,
            text          TEXT,
            contact       TEXT,
            category      TEXT,
            quest_type    TEXT,
            address       TEXT,
            scene         TEXT,
            line          INTEGER,
            speaker       TEXT,
            speaker_key   TEXT,
            addressee     TEXT,
            addressee_key TEXT,
            data          TEXT NOT NULL
        );
        CREATE INDEX idx_kind    ON entries(kind);
        CREATE INDEX idx_contact ON entries(contact);
        CREATE INDEX idx_cat     ON entries(category);
        CREATE INDEX idx_scene   ON entries(scene, line);
        CREATE INDEX idx_speaker ON entries(speaker_key);
        CREATE INDEX idx_addr    ON entries(addressee_key);
        CREATE VIRTUAL TABLE search USING fts5(
            id UNINDEXED, kind UNINDEXED, title, text,
            tokenize = "unicode61 remove_diacritics 2"
        );
    """)
    rows, search_rows = [], []
    for r in records:
        rows.append((
            r["id"], r["kind"], r["source"], r.get("journal_id", ""),
            "/".join(r.get("path") or []), r.get("title", ""), r.get("text", ""),
            r.get("contact", ""), r.get("category", ""), r.get("quest_type", ""),
            r.get("address", ""), r.get("scene", ""), r.get("line"),
            r.get("speaker", ""), r.get("speaker_key", ""), r.get("addressee", ""), r.get("addressee_key", ""),
            json.dumps(r, ensure_ascii=False),
        ))
        search_rows.append((r["id"], r["kind"], r.get("title", ""), r.get("text", "")))
    db.executemany("INSERT OR REPLACE INTO entries VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    db.executemany("INSERT INTO search VALUES (?,?,?,?)", search_rows)
    db.commit()
    db.execute("VACUUM")
    db.close()


def sample_id(records):
    """A real id for the worked example, so the index never ships a placeholder."""
    for r in records:
        if r["kind"] == "codex" and r.get("title") == "The Blackwall":
            return r["id"]
    return records[0]["id"] if records else ""


def write_index(records, by_kind, path, loc_counts):
    total_words = sum(len((r.get("text") or "").split()) for r in records)
    lines = [
        "# Cyberpunk 2077 journal archive - index",
        "",
        "Generated by `build.py`. Every readable entry in the game's journal",
        "resource, resolved against the en-us string table.",
        "",
        f"- Entries: **{len(records):,}**",
        f"- Words of text: **{total_words:,}**",
        f"- Localized strings available: **{loc_counts:,}**",
        "",
        "## Entry kinds",
        "",
        "| Kind | Count | What it is | File |",
        "| --- | --- | --- | --- |",
    ]
    blurb = {
        "shard": "Shards and journal entries the player picks up",
        "codex": "Database / lore entries, with their full descriptions",
        "sms": "Phone conversations, threaded, both speakers, with V's reply options",
        "mail": "Emails on computers and terminals",
        "file": "Non-email files on computers and terminals",
        "internet": "Pages of the in-game internet",
        "quest": "Quest log: description, phases and objectives",
        "tarot": "Tarot card readings",
        "subtitle": "Spoken dialogue, one record per line, tagged by scene",
    }
    for kind in sorted(by_kind, key=lambda k: -len(by_kind[k])):
        lines.append(
            f"| `{kind}` | {len(by_kind[kind]):,} | {blurb.get(kind, '')} | `{kind}.jsonl` |"
        )
    lines += [
        "",
        "## Querying",
        "",
        "```sql",
        "-- full text, ranked",
        "SELECT id, kind, title FROM search",
        "  WHERE search MATCH 'NEAR(arasaka tower, 5)' ORDER BY rank LIMIT 20;",
        "",
        "-- everything one contact ever texted",
        "SELECT title, text FROM entries WHERE kind='sms' AND contact='Judy Alvarez';",
        "",
        "-- one entry, full structure",
        f"SELECT data FROM entries WHERE id='{sample_id(records)}';",
        "```",
        "",
        "## Contacts by thread count",
        "",
    ]
    contacts = {}
    for r in by_kind.get("sms", []):
        contacts[r["contact"]] = contacts.get(r["contact"], 0) + 1
    top = sorted(contacts.items(), key=lambda kv: -kv[1])[:25]
    lines.append("| Contact | Threads |")
    lines.append("| --- | --- |")
    for name, count in top:
        lines.append(f"| {name} | {count} |")
    lines.append("")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))


# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--raw", default="raw", help="directory holding the four converted game files")
    ap.add_argument("--out", default="data", help="output directory")
    ap.add_argument("--db-only", action="store_true",
                    help="rebuild journal.db from the committed JSONL, without the game files")
    args = ap.parse_args()

    if args.db_only:
        records = []
        for name in sorted(os.listdir(args.out)):
            if name.endswith(".jsonl"):
                with open(os.path.join(args.out, name), encoding="utf-8") as f:
                    records.extend(json.loads(line) for line in f if line.strip())
        if not records:
            sys.exit(f"No .jsonl files in {args.out}")
        write_sqlite(records, os.path.join(args.out, "journal.db"))
        print(f"journal.db rebuilt from {len(records):,} records")
        return

    need = {
        "journal_base": "journal_base.json",
        "journal_ep1": "journal_ep1.json",
        "onscreens_base": "onscreens_base.json",
        "onscreens_ep1": "onscreens_ep1.json",
    }
    paths = {k: os.path.join(args.raw, v) for k, v in need.items()}
    missing = [p for p in paths.values() if not os.path.exists(p)]
    if missing:
        sys.exit("Missing input files (see README for how to produce them):\n  " + "\n  ".join(missing))

    loc = LocTable()
    loaded = loc.load(paths["onscreens_base"]) + loc.load(paths["onscreens_ep1"])
    print(f"Loaded {loaded:,} localized strings ({len(loc.text):,} unique keys)")

    builder = Builder(loc)
    for source, key in (("base", "journal_base"), ("ep1", "journal_ep1")):
        with open(paths[key], encoding="utf-8") as f:
            doc = json.load(f)
        root = doc["Data"]["RootChunk"]["entry"]["Data"]
        before = len(builder.records)
        builder.run(root, source)
        print(f"{source}: {len(builder.records) - before:,} entries")

    records = builder.records

    # Spoken dialogue comes from 3,800 separate subtitle resources rather than the journal,
    # so it is swept out by a wscript (see README) instead of read here.
    subs = os.path.join(args.raw, "subtitles_en_us.jsonl")
    if os.path.exists(subs):
        speakers = load_speakers(args.raw)
        if speakers:
            print(f"speakers: {len(speakers):,} ids swept from the .scene files")
        lines = build_subtitles(subs, speakers)
        attributed = sum(1 for r in lines if r.get("speaker"))
        records.extend(lines)
        pct = (100 * attributed / len(lines)) if lines else 0
        print(f"subtitles: {len(lines):,} lines, {attributed:,} with a speaker ({pct:.1f}%)")
    else:
        print(f"subtitles: skipped, no {subs}")

    # Around 1,600 scenes keep their subtitles inside the scene rather than in a resource under
    # the subtitles tree, and those have no resource at all - sweeping only the resources loses
    # every line in them.
    scene_subs = os.path.join(args.raw, "scene_subtitles_en_us.jsonl")
    if os.path.exists(scene_subs):
        seen = {r["journal_id"] for r in records if r.get("journal_id")}
        extra = [r for r in build_subtitles(scene_subs, load_speakers(args.raw))
                 if r["journal_id"] not in seen]
        records.extend(extra)
        print(f"scene-embedded subtitles: {len(extra):,} lines a resource does not carry")
    else:
        print(f"scene subtitles: skipped, no {scene_subs}")

    by_kind = {}
    for r in records:
        by_kind.setdefault(r["kind"], []).append(r)

    os.makedirs(args.out, exist_ok=True)
    for kind, group in by_kind.items():
        write_jsonl(group, os.path.join(args.out, f"{kind}.jsonl"))
    write_sqlite(records, os.path.join(args.out, "journal.db"))
    write_index(records, by_kind, os.path.join(args.out, "INDEX.md"), len(loc.text))

    print(f"\n{len(records):,} entries, {builder.skipped:,} empty nodes skipped")
    for kind in sorted(by_kind, key=lambda k: -len(by_kind[k])):
        print(f"  {kind:<10} {len(by_kind[kind]):>6,}")


if __name__ == "__main__":
    main()
