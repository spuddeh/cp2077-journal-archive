# What each game version changed

Counts compare one build of the archive with the build before it, matched by `id`. Each build
is a tag: `v2.31.0`, `v2.5.0`.

## Game 2.5 (Edgerunners 2 Update)

112,197 entries, up from 110,373.

| Kind | 2.31 | 2.5 | New | Removed | Changed |
| --- | --- | --- | --- | --- | --- |
| `subtitle` | 105,367 | 107,104 | 1,740 | 3 | 5 |
| `shard` | 1,804 | 1,837 | 33 | 0 | 2 |
| `mail` | 733 | 736 | 3 | 0 | 1 |
| `internet` | 678 | 678 | 0 | 0 | 4 |
| `sms` | 556 | 575 | 19 | 0 | 9 |
| `codex` | 486 | 498 | 12 | 0 | 4 |
| `quest` | 393 | 396 | 3 | 0 | 3 |
| `file` | 328 | 333 | 5 | 0 | 0 |
| `tarot` | 28 | 40 | 12 | 0 | 0 |

- **New quests:** `mq061` Welcome To Paradise, `mq062_clampdown` Clampdown, `mq033_tarot_minor`
  You Tell Me.
- **New dialogue** is mostly Clampdown (`mq062`), the glamdoc vendors (`open_world/vendors/*_glamdoc_01`),
  Misty and Richard Night.
- **New tarot cards:** the Page, Knight and Queen of each suit, all from `mq033`.
- **New codex entries:** Arasaka Memorial, Night Corp Headquarters, NC Prison, Richard Night, Aera,
  Shō Gun Rose, Issam Zahir, Serena "The Styliste" Zhao, Spite Vondell, Dove Vondell, Zéna Delicé,
  and a `wex` entry the game titles `!OBSOLETE`.
- **New files and mail** belong to Clampdown: five prison records and rosters, three monitoring emails.
- **Removed:** three lines of `sq025_04_briefing`.
- **Changed:** the four Archer Bandit product pages, `q000_tutorial`, `mq057_motorbreath` and
  `sts_pac_cvi_02` in the quest log, and a handful of codex and SMS entries.

The cooked journal is ordered differently in 2.5, so a line diff of `data/*.jsonl` is mostly
reordering. Compare by `id`.
