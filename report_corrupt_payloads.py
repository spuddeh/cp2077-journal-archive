"""Report the scene subtitle payloads the game ships broken.

A scene's embedded locStore pairs a descriptor with a payload. In a small number of cases the
payload's content is not text: a run of NUL bytes, or a fragment carrying another locale's tag
(\\x00l-plZobacz\u0119). The corruption is in the shipped file - reading the same entry through
WolvenKit's converter in Python and through a wscript gives the identical bytes - so it is
CD PROJEKT RED's data rather than a conversion fault.

Writes raw/corrupt_payloads.json and prints a per-quest summary.
"""

import json
import os
import re
import sys
from collections import Counter, defaultdict

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
SWEEP = r"D:\Modding\CP2077 Mods\.wkit-mcp-dump\wscript\raw\scene_subtitles_en_us.jsonl"

# A locale tag left in the payload, with its first byte overwritten: \x00l-pl, \x00e-de, \x00r-br.
LOCALE_FRAGMENT = re.compile(r"^\x00[a-z]-[a-z]{2}")


def classify(text):
    if text is None:
        return None
    if not text:
        return None
    if LOCALE_FRAGMENT.match(text):
        return "locale fragment"
    if text.startswith("\x00"):
        return "NUL run" if set(text) <= {"\x00"} else "NUL prefix"
    if "\x00" in text:
        return "embedded NUL"
    return None


def quest_of(path):
    """The quest folder a scene belongs to, for grouping."""
    parts = path.split("\\")
    for i, seg in enumerate(parts):
        if seg == "scenes" and i:
            return parts[i - 1]
    return parts[-2] if len(parts) > 1 else "?"


def main():
    rows = [json.loads(l) for l in open(SWEEP, encoding="utf-8") if l.strip()]
    bad = []
    for r in rows:
        for which in ("f", "m"):
            kind = classify(r.get(which))
            if kind:
                bad.append({"scene": r["p"], "quest": quest_of(r["p"]), "string_id": r["s"],
                            "variant": "female" if which == "f" else "male",
                            "kind": kind, "sample": (r[which] or "")[:40]})

    print(f"rows swept: {len(rows)}   corrupt payloads: {len(bad)}  ({100*len(bad)/len(rows):.2f}%)")
    print("\nby kind:")
    for k, n in Counter(b["kind"] for b in bad).most_common():
        print(f"  {n:5}  {k}")

    by_quest = defaultdict(set)
    for b in bad:
        by_quest[b["quest"]].add(b["scene"])
    print(f"\nquests affected: {len(by_quest)}")
    print("  worst, by number of corrupt payloads:")
    for q, n in Counter(b["quest"] for b in bad).most_common(20):
        print(f"    {n:4}  {q:28} {len(by_quest[q])} scene(s)")

    dest = os.path.join(HERE, "raw", "corrupt_payloads.json")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    json.dump({"total_rows": len(rows), "corrupt": bad}, open(dest, "w", encoding="utf-8"), indent=1)
    print(f"\nwrote {dest}")


if __name__ == "__main__":
    main()
