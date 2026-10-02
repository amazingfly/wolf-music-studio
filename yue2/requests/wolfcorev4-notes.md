# Wolfcore V4 writing and validation notes

Written 2026-09-28 for the existing 360-second YuE2 pipeline.

## Scope and references

Source batch: `outputs/2026-09-26_18-51-22_286288_wolfcorev3/requests.jsonl`.
Rewritten as new requests: Bone Chewer V3, Neon Frequency V3, Silk and Slaughter V3,
and Gloss and Grit V1. Their new request IDs end in V4 for this batch.
Bone and Iron V3 and Pelt and Polish V3 are preserved as references and excluded
from the new queue. Original output files and immutable requests remain intact.

Main reference, read before and after research: Frizzed and Fractured V2 from
`2026-09-25_02-22-39_706383_WolfCore`. Secondary references: Bone and Iron V3,
Pelt and Polish V3, and Last Howl Standing V2. These were prompt/lyric comparisons,
not an audio analysis. The user's listening judgment is the evidence that Frizzed
and Fractured V2 is the strongest sound reference; its many scream instructions
are not treated as a reliable description of the actual vocal balance.

The main reference has a concrete magical accident, wolf-specific embarrassment,
social stakes, a battle that escalates that embarrassment, a comeback, and a funny
coda. V4 retains that personal detail and electronic-metal propulsion while
making clean female power-metal/symphonic singing the explicit vocal center.
The darker new songs also draw on Last Howl Standing's loss and survival stakes.

## Research and application

- [Killswitch Engage guitarists on metalcore](https://www.musicradar.com/news/guitars/killswitch-engages-8-tips-on-mastering-metalcore-594489): tight riff articulation, focused tone, and avoiding excessive processing. Application: defined chugs and drums, room for the lead melody, and a contrasting half-time breakdown rather than nonstop maximal density.
- [Elize Ryd interview](https://myglobalmind.com/2013/06/22/exclusive-interview-with-elize-ryd-vocals-amaranthe/): melodic singing can contrast effectively with growls and heavy instrumentation. Application: powerful female clean lead, complementary clean male harmonies, and very brief harsh punctuation. This is a broad arrangement principle, not an imitation of a particular singer.
- [Floor Jansen interview](https://soniccathedral.com/zine/index.php/band-interviews/449-Floor%20Jansen%20Interview%202013): vocal expression connects technique to feeling. Application: the female performance moves between vulnerable narrative and full, sustained chorus belts; intensity need not mean screaming.
- [Firsthand YuE2 metal-vocal reports](https://www.reddit.com/r/StableDiffusion/comments/1wjqv8r/yue2_deathblack_metal_voices/): users report difficulty obtaining requested harsh voices; one describes token-limit problems. This concerns death/black metal, not an established metalcore recipe. No convincing YuE2-specific metalcore consensus was found. These are anecdotal reports and do not override the user's successful examples.
- [Firsthand YuE2 heavy-metal prompt and experience](https://www.reddit.com/r/StableDiffusion/comments/1whx7kp/ive_been_playing_around_with_yue2_and_i_must_say/): the poster reports late-song distortion/skipped lyrics in a long generation. Application: concise musical directions and a focused story arc; no inference that a prompt can guarantee vocal identity or exact timing.
- [Local official generation guide](../docs/generation.md): musical attributes in `style`, singable words and section labels in `lyrics`. V4 keeps performance directions out of sung text and uses ordinary section tags. Per-section singer assignments are conditioning requests, not guaranteed voice-routing controls.

## Nine songs

| Track | Story and emotional turn |
| --- | --- |
| Bone Chewer V4 | A hungry wolf discovers the witch's skeletons contain captive souls; appetite gives way to compassion and a rescue. |
| Neon Frequency V4 | A sonic trap overwhelms her ears; trusting a packmate's tapping heartbeat helps her free stolen voices and cubs. |
| Silk and Slaughter V4 | She ruins her mother's dress to rescue her brother from a witch's ballroom; its silk becomes a lifeline. |
| Gloss and Grit V4 | A cursed mirror steals her face; disastrous lipstick pawprints help reclaim her identity, without giving up her love of dressing up. |
| Static on the Veil V1 | A curse ruins her wedding coat; she uses its electrical charge to save the guests and discovers what her partner actually came to marry. |
| A Voice Without a Scent V1 | A witch borrows her dead mother's voice; scent and family memory distinguish love from a lethal imitation. |
| The Tailor of Thorns V1 | A grooming bargain turns shed fur into dolls binding her pack; she learns that being deceived is not the same as being to blame. |
| First Moon, Last Match V1 | An older sister protects a frightened first-time shifter from a demon; the younger sister's new claws help them both escape. |
| The Empty Place by the Fire V1 | Revenge has not repaired a survivor's grief; defending a new arrival lets her offer a home without replacing her lost pack. |

## Prompt and cadence choices

148–160 BPM, centered near the preferred reference's 155 BPM. Verses are pitched,
rhythmic clean singing; choruses use broader sustained phrases. Male clean voice
answers selected bridges or supports female-led harmonies. Each song requests
only two short male harsh accents in its Breakdown. The sound remains driven by
seven-string chugs, double kick, supersaws, arpeggios, sub bass, and strings.

Each story develops through changing verses; the final chorus changes to reflect
what actually happened. Short pre-chorus/breakdown phrases contrast with longer
story lines and sustained chorus hooks. Instrumental sections give guitar and
synth motifs room to develop. A line-length review flagged no exceptionally long
lines under a rough vowel-group check; this is not a guarantee of sung scansion.

Config validation uses `run_config.songs`, nine unique IDs/seeds, `cot=full`, and
the existing 360-second duration settings. Only supported song fields enter the
JSON. This notes file is not submitted to the music model. The intended clean/
harsh balance, voices, melodies, and story adherence require listening to the
resulting audio; JSON validation cannot establish those musical outcomes.
