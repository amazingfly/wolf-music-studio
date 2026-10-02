# Wolfcore V5: urgency and vocal pace

All nine V4 concepts rewritten as V5. Source prompts and output artifacts are
preserved. The six-minute runner settings and per-song seeds are retained for a
more useful comparison; changes are in style and lyrics.

## What the local evidence says

The generated ABC scores available at review time contain:

| Track | Requested BPM | Score Q: quarter-note BPM |
| --- | ---: | ---: |
| Frizzed and Fractured V2 | 155 | 146 |
| Bone and Iron V3 | 168 | 167 |
| Pelt and Polish V3 | 168 | 160 |
| Bone Chewer V4 | 156 | 150 |
| Neon Frequency V4 | 154 | 158 |

These are score tempo declarations, not measured audio-tempo guarantees. Only
two V4 scores were available locally during this review. Frizzed's TrackTags
analysis reports 100.53 BPM, differing from its score's 146; this discrepancy
is a reason not to treat automatic beat estimates or one BPM number as the
whole explanation for perceived energy.

V4 already requested more than 100 BPM. It also asked for sustained open vowels,
broad four-bar phrases, intimate passages, spacious solos, and half-time drops.
My interpretation is that these conflicting performance directions encouraged
the slow vocal pacing the user heard. That is a hypothesis informed by the
prompts and the user's listening feedback, not a controlled causal result.

The successful references instead foreground electronicore, metalcore,
aggrotech, chugs, kicks, forceful delivery, and action-filled verse lines.
Frizzed's actual success matters more than copying its many scream instructions
literally, since the user wants mostly clear female singing.

## Research

- [Firsthand YuE2 tempo-editing report](https://www.reddit.com/r/StableDiffusion/comments/1wlaawx/yue2_ksampler_16x_faster_fix_for_long_songs_and/): the author reports making a cover faster by editing the ABC tempo. This is more direct evidence of a tempo-control mechanism than speculative tag tricks. The unrelated sampling-speed discussion concerns computation, not musical speed.
- [YuE2 Studio maintainer settings](https://github.com/vrgamegirl19/Yue2_Studio/blob/main/docs/settings.md): tempo belongs in Style; ABC provides an explicit score tempo. A score declaration still does not ensure the performed audio or vocal phrasing will match exactly.
- [Firsthand heavy-metal discussion](https://www.reddit.com/r/StableDiffusion/comments/1whx7kp/ive_been_playing_around_with_yue2_and_i_must_say/): a commenter reports late-chorus timing trouble despite changing sampling parameters. That is an anecdotal cover result, not proof of a fix for original metalcore tracks.

Targeted searches did not establish a reliable YuE2-user consensus that a
particular tag order, repetition of BPM, or a numeric threshold forces fast
vocals. The queued config therefore does not pretend to have a hard BPM lock.
This repository's JSON runner currently accepts style/lyrics, not an explicit
ABC or BPM-override field. V5 uses the supported interface and leaves the
active generation pipeline intact. Explicit score editing would be a separate
implementation and rendering step, not something a made-up JSON field can do.

## Changes applied to all nine tracks

- 168–174 BPM requests, matching or slightly exceeding the faster preferred references.
- Electronicore/heavy melodic metalcore/aggrotech first; removed the dominant
  symphonic power-ballad direction and expansive vocal-production instructions.
- Immediate full-band openings, continuous sixteenth-note motion, driving double
  kick and full-time snare; the same forward pulse requested through every section.
- Clear female pitched rhythmic singing with rapid syllables, clipped consonants,
  brief belt peaks and short urgent melodic hooks.
- Sparse male clean replies; one male growled line in each two-line Breakdown,
  answered by a sharp clean female line. No alternating screamed verses.
- Action-oriented, more densely worded verses, four-line choruses, short bridges,
  brief instrumental breaks, quick comic/emotional payoffs instead of soft endings.
- Each final chorus changes with the story's outcome. All nine original concepts
  remain recognizable rather than being replaced with generic speed slogans.

Validation: nine unique V5 IDs, all nine V4 songs represented, original seeds
retained, supported JSON fields only, full planning mode, 360-second target,
valid duration bounds, section-label and lyric checks. This validates the input,
not the resulting performance; fast vocal pacing must be checked by listening.
