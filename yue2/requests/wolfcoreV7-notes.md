# Replacement V7: exact star references

This replaces the cancelled V7 from `2026-09-29_00-34-43_022571_wolfcoreV7`. That run and its artifacts are retained, its queue job is cancelled, and its config/build script/notes are archived under `requests/archive/wolfcoreV7-superseded-20260929/`.

Authoritative references:

- Bone and Iron V3 and Pelt and Polish V3: `outputs/2026-09-26_18-51-22_286288_wolfcorev3/requests.jsonl`.
- Frizzed and Fractured V2: `outputs/2026-09-25_02-22-39_706383_WolfCore/requests.jsonl`.
- Bone and Iron V2 is not used in this replacement.

All eight songs use an exact source style string. There is no new common genre blend or increased BPM. The three star-derived stories retain every original heading, standalone performance cue, sung-line count, section order and original seed. Their new lyrics are tightened to lengths close to the actual sources rather than the longer V6 versions:

| New song | Exact template | Words including cues: new / source |
|---|---|---:|
| Lavender and Lies | Frizzed and Fractured V2 | 614 / 611 |
| Pitch and Panic | Bone and Iron V3 | 672 / 644 |
| Claws and Cutlery | Pelt and Polish V3 | 639 / 638 |
| The Ribbon and the Teeth | Bone and Iron V3 | 652 / 644 |

The ribbon song now also follows Bone and Iron V3's exact scaffold, instead of combining cues from different songs. Its seed 85306 is a chosen baseline, not an original reference seed. Comparable line and word counts are useful cadence constraints, not proof of identical syllables or melody.

Bone Chewer V3, Neon Frequency V3, Silk and Slaughter V3 and Gloss and Grit V1 are taken directly from the named September 26 batch. Their styles and sung words are unchanged. Only the Verse 2 female scream/shriek phrase becomes `female fast soaring vocal`, language already present in Pelt and Polish V3. Instruments in that cue and all other cues remain unchanged.

Each song has its baseline seed plus 160601 and 160602. Only ID/title/seed differ inside a triplet. The baseline pass runs first across all eight songs, followed by the two alternate passes. Total: 24 requests. Existing full planning and 360-second target settings remain unchanged.

Validation uses `run_config.songs`, exact style/cue comparisons, line-count checks for rewrites, unchanged sung-word checks for companion songs, and identical-request checks within seed triplets. `wolfcoreV7-experiment.json` records exact source paths, source-file SHA256 hashes and all eight bases; `build_wolfcoreV7.py` makes the transformation reviewable.

These are prompt and saved-source checks, not an audition or a guarantee of the musical result. The source recordings were not listened to in this session. Successful audio is the final reference; preserving its text inputs alone does not preserve the generated performance when lyrics change.
