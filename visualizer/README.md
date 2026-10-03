> Component reference notes. Use the top-level [README](../README.md) and installation guide for portable setup/service names; dated campaign details describe the original deployment.

# GPU Audio Visualizer (Modular Architecture)

## Automatic YuE2 karaoke videos (V7)

`vis2GPUV7.py` is the new automation entry point. The existing V6 entry point and
visual effects/recognition modules are preserved. FLAC is accepted directly: an
OGG conversion is unnecessary. V7 recovers and aligns lyrics with the existing
HTDemucs → Vulkan Whisper → CPU CTC pipeline, then renders both widescreen and
portrait MP4s with word highlighting, retaining the original mix as the video audio.

YuE2's verified local download hook first creates its strictly truncated FLAC,
then adds a persistent render job. A separate user service,
`yue2-visualizer.service`, processes one job at a time, so downloads and Colab
generation can continue while lyrics/video are processed. Each job snapshots the
exact generation prompt and visualizer config; no broad lyric search or matching
against another song version occurs. Audio, prompt, config, and renderer changes
produce distinct jobs. Unchanged jobs are reused.

Video output:

```text
output/yue2/<generation-batch>/<track>/<job-id-prefix>/
  widescreen/<track>.mp4
  portrait/<track>.mp4
  render_receipt.json
```

Caption timelines, cleaned lyrics, model logs and stems are cached under
`output/karaoke`. Job state and `render.log` live under
`/path/to/wolf-music-studio/yue2/queue/visualizer/jobs/<job-id>/`.
YuE2's `outputs/master_database.json` and per-file sidecars contain
`visualizer_workflow`: queued/running/completed/failure state, video paths,
word timeline path, review counts, source checksum, and log/job locations.
The track library refreshes on state changes.

```bash
# Show the queue:
python /path/to/wolf-music-studio/yue2/visualizer_queue.py --status

# Backfill any directory containing processed YuE2 FLAC masters:
python /path/to/wolf-music-studio/yue2/visualizer_queue.py --enqueue /path/to/run

# Retry failed jobs after fixing their reported problem:
python /path/to/wolf-music-studio/yue2/visualizer_queue.py --retry-failed

# Service/log controls:
systemctl --user status yue2-visualizer.service
journalctl --user -u yue2-visualizer.service -f

# Standalone V7, using an explicit track generation prompt:
python vis2GPUV7.py /path/to/track.flac --lyrics /path/to/request.json -o output/manual

# Reuse manually corrected word timings (checked against exact source audio):
python vis2GPUV7.py /path/to/track.flac --words /path/to/words.json -o output/manual
```

V7 renders to temporary MP4s, checks H.264/AAC streams, dimensions, frame rate and
audio/video duration, then publishes each complete video atomically. Interrupted
jobs resume after restart and can reuse an already verified orientation. Failures
retry up to three times, with delays that allow other tracks to proceed; failures
remain visible for explicit retry. Modified/missing videos or edited word timelines
requeue on the next harvest/enqueue. Model inference and render work stay local.
Karaoke text still needs review for singing/growling; the prompt is a reference,
and the recognition/alignment limits documented below still apply.

High-performance, hardware-accelerated (ModernGL + VAAPI / libx264) audio visualizer engine with a decoupled, layer-based compositing pipeline and JSON-driven effect configurations.

## Architecture Overview

```
vis/
├── __init__.py           # Public exports
├── config.py             # JSON config loader with automatic defaults
├── audio.py              # Audio feature extraction (librosa STFT transients, mid/high energies)
├── encoder.py            # Streaming FFmpeg pipeline (VAAPI hardware acceleration + libx264 fallback)
├── renderer.py           # ModernGL multi-layer renderer & feedback compositor
├── karaoke.py            # Prompt lookup, vocal separation, recognition and CTC alignment
├── captions.py           # Word highlighting and GPU caption overlay
└── effects/
    ├── __init__.py       # Auto-registers effects
    ├── base.py           # BaseEffect, register_effect decorator, and registry
    ├── tunnel.py         # 3D Curving Wormhole Tunnel + electric arcs effect
    ├── speaker_orbs.py   # Cartoon speaker cones + soundwave rings + inter-lightning arcs
    ├── feedback.py       # Motion feedback decay and trail accumulation pass
    └── plasma_waves.py   # Example plug-and-play audio-reactive plasma wave effect
```

---

## Configuration (`config.json`)

All visualizer properties, video encoding options, audio analysis parameters, and effect layer assignments are configured in [config.json](file:///path/to/wolf-music-studio/visualizer/config.json). **You do not need to modify Python code to tweak effects.**

### Layer Configuration Example

```json
{
  "name": "tunnel",
  "type": "tunnel",
  "enabled": true,
  "order": 10,
  "blend_mode": "replace",
  "opacity": 1.0,
  "params": {
    "speed_base": 1.5,
    "speed_percussion_scale": 2.5,
    "curve_amplitude_base": 0.55,
    "curve_amplitude_scale": 0.45,
    "rebound_pulse_freq": 2.2,
    "distortion_scale": 0.25,
    "pinpoint_radius_base": 0.009,
    "pinpoint_radius_scale": 0.006,
    "arcs_count": 3,
    "hue_speed": 40.0
  }
}
```

### Layer Parameters
- **`enabled`**: `true` or `false` to toggle effects on or off.
- **`order`**: Integer render order. Lower numbers render first (background), higher numbers render on top (foreground).
- **`blend_mode`**: Supported blend modes:
  - `"replace"`: Overwrites the framebuffer (standard for background layers).
  - `"additive"`: Adds light values together (ideal for glowing orbs, arcs, lasers).
  - `"alpha"`: Standard alpha transparency (`SRC_ALPHA, ONE_MINUS_SRC_ALPHA`).
  - `"screen"`: Screen blending mode.
  - `"multiply"`: Multiplicative blending.
- **`opacity`**: Base layer opacity (0.0 to 1.0).
- **`params`**: Any key-value parameters specific to that effect.

---

## Adding a New Effect

To add a new visual effect:

1. Create a new file in `vis/effects/my_effect.py`:
   ```python
   import moderngl
   from .base import BaseEffect, register_effect, QUAD_VERTEX_SHADER

   MY_FRAGMENT_SHADER = """
   #version 330
   uniform vec2 u_resolution;
   uniform float u_time;
   uniform float u_perc_exp;
   uniform float u_opacity;
   uniform float u_custom_param;
   in vec2 v_uv;
   out vec4 fragColor;

   void main() {
       // Your GLSL effect logic here
       vec3 col = vec3(sin(u_time), cos(u_time), u_perc_exp);
       fragColor = vec4(col * u_opacity, 1.0);
   }
   """

   @register_effect("my_effect")
   class MyEffect(BaseEffect):
       def __init__(self, ctx, width, height, params=None, layer_config=None):
           super().__init__(ctx, width, height, params, layer_config)
           prog = self.ctx.program(
               vertex_shader=QUAD_VERTEX_SHADER,
               fragment_shader=MY_FRAGMENT_SHADER,
           )
           self.setup_fullscreen_quad(prog)
           self.set_uniform_safe("u_resolution", (float(width), float(height)))
           self.set_uniform_safe("u_custom_param", float(self.params.get("custom_param", 1.0)))

       def render(self, frame_ctx: dict):
           self.set_uniform_safe("u_time", float(frame_ctx["time"]))
           self.set_uniform_safe("u_perc_exp", float(frame_ctx["perc_exp"]))
           self.set_uniform_safe("u_opacity", float(self.opacity))
           self.vao.render(moderngl.TRIANGLE_STRIP)
   ```

2. Export it in `vis/effects/__init__.py`:
   ```python
   from .my_effect import MyEffect
   ```

3. Add the layer to [config.json](file:///path/to/wolf-music-studio/visualizer/config.json):
   ```json
   {
     "name": "my_new_layer",
     "type": "my_effect",
     "enabled": true,
     "order": 25,
     "blend_mode": "additive",
     "opacity": 0.8,
     "params": {
       "custom_param": 2.5
     }
   }
   ```

---

## Karaoke captions

```bash
# One-time local setup (downloads models; no cloud inference):
./scripts/setup_karaoke.sh

# Recover lyrics and render both widescreen and portrait:
python3 vis2GPUV6.py frizzed_and_fractured_v2.ogg --karaoke

# Reuse the vocal stem generated during development:
python3 vis2GPUV6.py frizzed_and_fractured_v2.ogg --karaoke \
  --vocals output/karaoke/stems/htdemucs/frizzed_and_fractured_v2/vocals.wav

# Prepare/review captions first, without rendering:
python3 vis2GPUV6.py frizzed_and_fractured_v2.ogg --prepare-only

# Render an existing or manually corrected timeline (audio hash checked):
python3 vis2GPUV6.py frizzed_and_fractured_v2.ogg --words /path/to/words.json
```

The pipeline finds the exact song ID/title in `/path/to/wolf-music-studio/yue2`,
including completed queue inputs. It does not mistake a rewrite's `source_id` for
the original prompt. Use `--prompt-root DIRECTORY` elsewhere, or `--lyrics FILE`
for a text file or a specific JSON prompt. Conflicting exact prompts require an
explicit choice. Stage directions are removed; sung replies such as `(Male: HIT!)`
are preserved.

HTDemucs isolates vocals on CPU. Quantized Whisper large-v3 runs on Vulkan twice:
once on the stem with a short lyric vocabulary hint, and once independently on
the original mix. Both disable accumulated recognition text context (`-mc 0`) to
reduce repeated hallucinations. The primary pass is selected using prompt
compatibility and phrase-loop evidence; the other supplies correction candidates.
Wav2Vec2 large CTC alignment compares short corrections against acoustic evidence
and measures each word's boundaries within its own phrase window. It never aligns
the complete transcript across the whole song, so a bad phrase cannot pull all
later words backwards. Words are never evenly distributed over a line. Unsupported
passages are reported, and actual departures from the prompt can remain captions.

The RX 480/580 Polaris path uses Vulkan for Whisper, OpenGL for visual effects and
caption compositing, and VAAPI for H.264 encoding. HTDemucs and the PyTorch CTC model
use CPU: this implementation does not require CUDA or ROCm. Text is rasterized only
when its phrase/highlight changes, uploaded as a texture, and composited into a
separate output framebuffer so it cannot leave feedback trails. White bold type,
a dark rounded panel, and a gold active word keep it legible in both formats.

Outputs under `output/karaoke/<song>-<fingerprint>/` include `words.json`, cleaned
lyrics, original prompt/provenance, both raw recognitions and logs, and cached CTC
emissions. `words.json` is editable: each word has `start`, `end`, `confidence`, and
`review`. The JSON also records proposed corrections, accepted/rejected decisions,
and unresolved reference differences. Low acoustic confidence is a review signal,
**not a measured probability of transcription accuracy**. Singing and growling
still need listening review; agreement with the input prompt does not establish
what was actually sung. A failed alignment stops rather than inventing uniform
word timings. Original song audio remains in the video.

Preparation uses the full song even with `--max-duration` so a preview and a full
render share the same lyric alignment. Model/audio/prompt hashes invalidate cached
results when inputs change. Reuse `--words` to preserve manual edits explicitly.

Catastrophic timelines are held before rendering: more than 70% weakly supported
words (at least 20 words), words escaping their recognition phrase by over two
seconds, lengthy immediate phrase loops combined with weak support, or more than
20% unalignable words. These are failure guards, not an accuracy guarantee.
`quality.json` and word decisions retain the evidence. The renderer writes
`karaoke_review.json`, and the queue records `needs_review` without retrying the
same captions. Previous videos and original audio remain available.

After reviewing or repairing an audio-bound timeline, queue a new snapshot:

```bash
./studio visualize --enqueue /path/to/track/track.flac --words /path/to/words.json
```

Run the launcher from the repository root. `--words` requires one audio file;
the queue freezes the supplied JSON and verifies its audio checksum before
rendering. Normal backfills use `--enqueue DIRECTORY` and the current pipeline.

Advanced preparation:

```bash
python3 -m vis.karaoke song.ogg --whisper-model models/ggml-medium.en-q5_0.bin \
  --alignment-model WAV2VEC2_ASR_BASE_960H --threads 4
# Add --cpu if Vulkan is unavailable. Default: large-v3 + large CTC.
python3 -m unittest discover -s tests -v
```

Dependencies are pinned to Torch/TorchAudio 2.8 because the CTC API used here is
removed in 2.9. Setup also needs FFmpeg, Vulkan headers/loader and `glslc`, Mesa
Vulkan/VAAPI drivers, CMake/C++ tools, and DejaVu Sans. The setup script lists Ubuntu
package names. Models download on setup/first use; inference stays local.

References: [whisper.cpp](https://github.com/ggml-org/whisper.cpp),
[Demucs](https://github.com/facebookresearch/demucs),
[PyTorch CTC alignment](https://docs.pytorch.org/audio/2.1/tutorials/ctc_forced_alignment_api_tutorial.html).

---

## Usage

```bash
# Render widescreen & portrait for all .ogg files in ./ogg using config.json:
python3 vis2GPUV6.py

# Render a single file with custom config:
python3 vis2GPUV6.py my_song.ogg --config custom_config.json

# Render widescreen only:
python3 vis2GPUV6.py my_song.ogg --no-cellphone

# Quick preview (e.g., first 5 seconds):
python3 vis2GPUV6.py my_song.ogg --max-duration 5
```
