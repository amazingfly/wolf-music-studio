# Frizzed and Fractured v2 — karaoke verification

Tested on 2026-09-28 with the complete 255-second local OGG.

Pipeline: HTDemucs vocal separation → Whisper large-v3 Q5 (vocal stem with vocabulary hints + independent original-mix pass) → Wav2Vec2 large CTC acoustic comparison → full-timeline word alignment → GPU caption overlay.

## Results

- 451 words, all with valid increasing start/end times inside the audio duration.
- 16 acoustic correction decisions accepted. Full decisions and raw recognitions are retained alongside the timeline.
- 132 words have acoustic confidence below 0.35 and are flagged for listening review. These are not 132 proven errors.
- No words discarded at recognizer phrase boundaries in the final alignment.
- 12 automated tests passed, including synthetic acoustic peaks/repeated letters, absolute timing across gaps, exact prompt selection, contraction normalization, missing-verse protection, inactive highlights in gaps, portrait fit, and GPU overlay isolation.
- Vulkan log confirms the Polaris GPU, not a software device. Driver name: AMD Radeon RX 580 Series (RADV POLARIS10).
- OpenGL confirms the same Polaris GPU; both renders use h264_vaapi on /dev/dri/renderD128.
- Separation and CTC run on CPU. No CUDA/ROCm required.
- Full widescreen and portrait exports: 1280×720 and 720×1280, H.264/AAC, 30 fps. FFprobe confirms exactly 255 seconds for both audio and video in both files. Both complete files decode without FFmpeg errors. Extracted frames were visually inspected for readable text, correct orientation, and active-word highlighting.
- Cache reuse, Python compilation, shell syntax, and git whitespace checks passed.

## Accuracy limits

No manually transcribed/timed ground truth was available, so no word error rate or millisecond alignment accuracy is claimed. Visual and structural checks do not replace listening review of screamed/growled passages. The original generation prompt is not ground truth for the performed song. Both recognizers omit some intended material; the pipeline does not force entire missing passages into the video.

## Files

- [Widescreen video](output/karaoke-video/widescreen/frizzed_and_fractured_v2.mp4)
- [Portrait video](output/karaoke-video/portrait/frizzed_and_fractured_v2.mp4)
- [Editable word timeline](output/karaoke/frizzed_and_fractured_v2-287ab49b8a47a1ae/words.json)
- [Original prompt](output/karaoke/frizzed_and_fractured_v2-287ab49b8a47a1ae/prompt.json)

## Reproduce

```bash
python3 -m unittest discover -s tests -v
python3 vis2GPUV6.py frizzed_and_fractured_v2.ogg --karaoke \
  --vocals output/karaoke/stems/htdemucs/frizzed_and_fractured_v2/vocals.wav \
  -o output/karaoke-video
```

Use `--words output/karaoke/frizzed_and_fractured_v2-287ab49b8a47a1ae/words.json` instead of `--karaoke --vocals ...` to render this exact saved/editable timeline.
