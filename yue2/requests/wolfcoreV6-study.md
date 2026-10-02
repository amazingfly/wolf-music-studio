# Wolfcore V6: preserve the successful recipe, compare seeds

## Evidence and limits

The listening references are **Frizzed and Fractured V2**, **Bone and Iron V3**, and **Pelt and Polish V3**, with Frizzed the strongest reference. These are the exact versions used, not their earlier or later namesakes. The conclusions below come from their saved prompts and the user's listening feedback. V6 audio has not been evaluated yet; these are hypotheses for a controlled listening experiment, not proven causal rules.

Sources:

- `outputs/2026-09-25_02-22-39_706383_WolfCore/requests.jsonl`: `frizzed_and_fractured_v2`, seed 85301.
- `outputs/2026-09-26_18-51-22_286288_wolfcorev3/requests.jsonl`: `bone_and_iron_v3`, seed 85300, and `pelt_and_polish_v3`, seed 85303.
- The four companion songs come from **wolfcorev3**, the same batch as the two V3 references. The separate older batch literally named `wolfV3` is not included in this experiment.

## What the successful prompts share

All three combine electronicore, heavy metalcore and aggrotech; 4/4; double-kick drums; downtuned seven-string chugs; distorted supersaw synth; a prominent female lead; brief male low-register responses; melodic/soaring chorus language; and explosive electronic-metal production. Frizzed specifies 155 BPM; the other two specify 168 BPM. The shared feature is driving rhythmic instrumentation and delivery, not one magic tempo number. Synthwave and 808 drops belong to the two V3 examples; deathcore breakdown wording belongs to Frizzed. Do not flatten these differences into a larger list of genres.

Their lyrics carry momentum through compact, physical verbs, closely spaced lines, short call-and-response pre-choruses and recurring four-line hooks. Verses move the incident forward, rather than explaining the same feeling repeatedly. A wolf girl's body, grooming, shifting or senses creates a practical problem; the action makes that problem worse; she fights back; the ending pays off both the stakes and the joke. Small indignities coexist with real anger or vulnerability. This character voice is as important to preserve as the genre labels.

The scaffolds alternate female rhythmic aggression with melodic hooks, sparse male punctuation, synth/guitar motion and a concentrated breakdown. Long-form section contrast does not require a ballad bridge. However, headings and parenthetical directions are text conditioning, not guaranteed transport controls. Requested BPM is not a measured output BPM.

Most importantly, the successful reference strings explicitly contain screaming, shrieking and growling. Their audio was nevertheless preferred. Replacing that vocabulary everywhere with an idealized clean-singing description would discard the strongest evidence we have. Preserve successful strings first; audition the actual output before deciding which word caused which behavior.

## Exact experiment

| V6 song | Basis | Baseline seed | Change |
|---|---|---:|---|
| Lavender and Lies | Frizzed and Fractured V2 | 85301 | Entirely new sung lyrics; exact original style, headings and standalone cues |
| Pitch and Panic | Bone and Iron V3 | 85300 | Entirely new sung lyrics; exact original style, headings and standalone cues |
| Claws and Cutlery | Pelt and Polish V3 | 85303 | Entirely new sung lyrics; exact original style, headings and standalone cues |
| Bone Chewer | Bone Chewer V3 | 85301 | Two small direction edits; sung words unchanged |
| Neon Frequency | Neon Frequency V3 | 85302 | Two small direction edits; sung words unchanged |
| Silk and Slaughter | Silk and Slaughter V3 | 85304 | Two small direction edits; sung words unchanged |
| Gloss and Grit | Gloss and Grit V1 from wolfcorev3 | 85305 | Two small direction edits; sung words unchanged |
| The Ribbon and the Teeth | New story using Bone and Iron V3's exact style | 85306 | Original lyric with familiar section vocabulary; melodic bridge delivery |

Each song receives the baseline seed above plus **160601** and **160602**. The new song's baseline is newly chosen, not a seed from an existing recording. The same alternate seeds across songs make the comparison easier to track. Inside each triplet, all model request fields are identical except seed; IDs and display titles also differ to prevent output collisions. Original seeds are retained, but new words mean new outputs: an original seed does not preserve the original tune.

The four minimal edits replace `fast soaring female vocal` with Bone and Iron's exact `soaring melodic female chorus` in the style string. In Verse 2 only, `female aggressive metalcore scream` or `female frantic shriek` becomes Pelt and Polish's `female fast soaring vocal`; the instruments in that cue stay unchanged. No other lyric text, heading, tempo, growl or breakdown is changed. These are two linked edits, so any improvement cannot be attributed to one of them alone.

The reference rewrites preserve the number and placement of sung lines and standalone cues, including apparently unusual section names. Their word counts, including directions, are 664, 725 and 719, versus roughly 611, 644 and 638 in the references. The new song is 834 words including directions: somewhat longer, with an eight-line final narrative verse to resolve the rescue. All requests retain the pipeline's 360-second target and existing generation settings.

Generation order is one baseline pass through all eight songs, then all eight with 160601, then all eight with 160602. This provides an early sample of every concept rather than waiting for every repeat of the first song.

## Repeatable procedure

1. Choose a successful **exact version and saved request**, not its title alone. Keep its audio, seed, style, lyrics and engine settings together.
2. Copy the style verbatim. Preserve section order and standalone performance cues. Replace sung words with a concrete new incident; keep comparable line counts and phrase lengths. Read the verses aloud briskly to find awkward stresses.
3. Give the heroine a wolf-specific obstacle and something worth losing. Let each verse change the situation. Put the clearest emotional claim in a short hook. End with a physical consequence or character joke, not a generic victory slogan.
4. Make three otherwise identical requests: the source seed and two recorded alternatives. Keep the model/checkpoint, sampling configuration, duration, reference conditioning and code version stable when possible. Seed repeatability depends on the complete generation environment.
5. Validate with `run_config.songs`, enforce unique IDs, compare each triplet ignoring only ID/title/seed, and check that preserved style/cue strings truly match the source. Publish atomically only after validation.
6. Listen blind to seed labels if possible. Score urgency, intelligible female lead, soaring hook, excessive screaming, male dominance, story clarity and overall reference similarity from 1–5. Note timestamps where the pace drops. Record whether a track feels half-time even when drums are busy.
7. If one seed wins, retain that full request and audio as a new reference. If all seeds fail similarly, change one direction next time; if outcomes vary widely, sample seeds before rewriting the prompt again. Three seeds reveal some variability but are not enough to establish a reliable success rate.
8. Separate listening results from explanations. A higher BPM token, a particular heading or a new vocal adjective is only a hypothesis until comparable outputs support it. Do not add a large stack of corrective adjectives in response to one miss.

## Files and validation

`requests/wolfcoreV6.json` is the clean pipeline config (24 requests). `requests/wolfcoreV6-experiment.json` records source IDs, bases, seed mapping and the exact minimal edits outside the pipeline schema. The source output requests remain unchanged.

Before publication: JSON parsed by the real runner validator; 24 unique IDs; three distinct seeds per song; triplets identical except identity/title/seed; all three star styles and standalone cues exactly preserved; all sung lines in the four minimal edits unchanged. These checks validate the experiment and input format, not the musical result.
