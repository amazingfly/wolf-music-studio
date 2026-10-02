import os
import numpy as np
import moderngl
from tqdm import tqdm

from .effects.base import EFFECT_REGISTRY, get_effect_class
from .effects.feedback import FeedbackPass
from .audio import extract_audio_features, compute_frame_signals
from .encoder import FFmpegEncoder


class ModernGLRenderer:
    """
    Manages the ModernGL rendering context, multi-pass layer compositing,
    ping-pong feedback loops, and video encoding pipe.
    """

    def __init__(self, width: int, height: int, config: dict):
        self.width = width
        self.height = height
        self.config = config
        try:
            self.ctx = moderngl.create_standalone_context(backend='egl')
        except Exception:
            self.ctx = moderngl.create_standalone_context()
        print(f"[+] OpenGL device: {self.ctx.info['GL_RENDERER']}")

        # Allocate scene framebuffer & texture
        self.scene_tex = self.ctx.texture((width, height), 4)
        self.scene_fbo = self.ctx.framebuffer(color_attachments=[self.scene_tex])

        # Allocate ping-pong feedback framebuffers & textures
        self.tex_a = self.ctx.texture((width, height), 4)
        self.fbo_a = self.ctx.framebuffer(color_attachments=[self.tex_a])

        self.tex_b = self.ctx.texture((width, height), 4)
        self.fbo_b = self.ctx.framebuffer(color_attachments=[self.tex_b])

        # Clear ping-pong textures with black
        blank_data = np.zeros((height, width, 4), dtype=np.uint8).tobytes()
        self.tex_a.write(blank_data)
        self.tex_b.write(blank_data)
        self.scene_tex.write(blank_data)

        # Initialize feedback pass
        fb_cfg = config.get("feedback", {})
        self.feedback_enabled = fb_cfg.get("enabled", True)
        if self.feedback_enabled:
            self.feedback_pass = FeedbackPass(
                self.ctx, width, height, params=fb_cfg, layer_config={"name": "feedback_pass"}
            )
        else:
            self.feedback_pass = None

        # Instantiate configured layers
        self.scene_layers = []
        self.overlay_layers = []
        self._init_layers()
        self.output_tex = self.ctx.texture((width, height), 4)
        self.output_fbo = self.ctx.framebuffer(color_attachments=[self.output_tex])
        self.captions = None
        if config.get('karaoke', {}).get('words_file'):
            from .captions import CaptionOverlay
            self.captions = CaptionOverlay(self.ctx, width, height, config['karaoke']['words_file'],
                                           config['karaoke'].get('font'))

    def _init_layers(self):
        layer_configs = self.config.get("layers", [])
        # Sort layers by order
        sorted_configs = sorted(layer_configs, key=lambda x: x.get("order", 0))

        for l_cfg in sorted_configs:
            if not l_cfg.get("enabled", True):
                continue

            l_type = l_cfg.get("type", "")
            # Skip unhandled overlay placeholders for now
            if l_type == "overlay" and l_type not in EFFECT_REGISTRY:
                continue

            if l_type in EFFECT_REGISTRY:
                cls = get_effect_class(l_type)
                layer = cls(
                    self.ctx,
                    self.width,
                    self.height,
                    params=l_cfg.get("params", {}),
                    layer_config=l_cfg,
                )
                if l_cfg.get("order", 0) >= 100 or l_type == "overlay":
                    self.overlay_layers.append(layer)
                else:
                    self.scene_layers.append(layer)
                print(f"[+] Loaded layer: {layer.name} (type: {l_type}, order: {layer.order}, blend: {layer.blend_mode})")
            else:
                print(f"[!] Warning: Unknown layer type '{l_type}', skipping.")

    def apply_blend_mode(self, blend_mode: str):
        """Configures ModernGL blend state for the incoming layer."""
        mode = blend_mode.lower()
        if mode == "replace" or mode == "none":
            self.ctx.disable(moderngl.BLEND)
        elif mode == "additive" or mode == "add":
            self.ctx.enable(moderngl.BLEND)
            self.ctx.blend_func = (moderngl.ONE, moderngl.ONE)
        elif mode == "alpha" or mode == "normal":
            self.ctx.enable(moderngl.BLEND)
            self.ctx.blend_func = (moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA)
        elif mode == "screen":
            self.ctx.enable(moderngl.BLEND)
            self.ctx.blend_func = (moderngl.ONE, moderngl.ONE_MINUS_SRC_COLOR)
        elif mode == "multiply":
            self.ctx.enable(moderngl.BLEND)
            self.ctx.blend_func = (moderngl.DST_COLOR, moderngl.ZERO)
        else:
            self.ctx.enable(moderngl.BLEND)
            self.ctx.blend_func = (moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA)

    def render_frame(self, frame_ctx: dict, current_fbo, prev_tex):
        """
        Renders all layers for a single frame:
        1. Render scene layers into scene_fbo.
        2. Composite with feedback into current_fbo.
        3. Render overlays (e.g. captions/karaoke).
        """
        # --- 1. Scene Layers Pass ---
        self.scene_fbo.use()
        self.ctx.disable(moderngl.BLEND)
        self.scene_fbo.clear(0.0, 0.0, 0.0, 1.0)

        for layer in self.scene_layers:
            self.apply_blend_mode(layer.blend_mode)
            layer.render(frame_ctx)

        # --- 2. Feedback Pass ---
        if self.feedback_enabled and self.feedback_pass:
            current_fbo.use()
            self.ctx.disable(moderngl.BLEND)
            self.feedback_pass.render_feedback(self.scene_tex, prev_tex, frame_ctx)
            target_fbo = current_fbo
        else:
            target_fbo = self.scene_fbo

        # Copy the finished scene: overlays must never enter the feedback history.
        if self.overlay_layers or self.captions:
            self.ctx.copy_framebuffer(self.output_fbo, target_fbo)
            target_fbo = self.output_fbo
            target_fbo.use()

        # --- 3. Overlay Layers Pass ---
        if self.overlay_layers:
            target_fbo.use()
            for layer in self.overlay_layers:
                self.apply_blend_mode(layer.blend_mode)
                layer.render(frame_ctx)

        if self.captions:
            self.captions.render(frame_ctx['time'])

        return target_fbo

    def destroy(self):
        """Releases all allocated ModernGL and effect resources."""
        for layer in self.scene_layers + self.overlay_layers:
            layer.destroy()
        if self.feedback_pass:
            self.feedback_pass.destroy()
        if self.captions:
            self.captions.destroy()
        self.output_fbo.release()
        self.output_tex.release()
        self.scene_fbo.release()
        self.scene_tex.release()
        self.fbo_a.release()
        self.tex_a.release()
        self.fbo_b.release()
        self.tex_b.release()
        self.ctx.release()


def render_visualizer_pipeline(
    audio_path: str,
    output_mp4: str = "output.mp4",
    width: int = 1280,
    height: int = 720,
    fps: int = 30,
    config: dict = None,
    max_duration: float = None,
    max_frames: int = None,
):
    """
    Executes the complete modular audio visualization rendering pipeline.
    """
    if config is None:
        from .config import load_config
        config = load_config()

    audio_cfg = config.get("audio", {})
    video_cfg = config.get("video", {})

    audio_data = extract_audio_features(
        audio_path,
        fps=fps,
        n_fft=audio_cfg.get("n_fft", 2048),
        mid_range=tuple(audio_cfg.get("mid_freq_range", [180, 2000])),
        high_range=tuple(audio_cfg.get("high_freq_range", [2000, 8000])),
        percussion_decay=audio_cfg.get("percussion_decay", 0.30),
        energy_decay=audio_cfg.get("energy_decay", 0.65),
        max_duration=max_duration,
    )

    signals = compute_frame_signals(
        audio_data,
        fps=fps,
        k_exp=audio_cfg.get("exp_factor", 3.8),
        morph_speed=audio_cfg.get("morph_speed", 0.3),
        max_frames=max_frames,
    )

    num_frames = signals["num_frames"]
    times = signals["times"]
    perc_exps = signals["perc_exps"]
    morph_params = signals["morph_params"]
    highs = signals["highs"]
    mids = signals["mids"]

    renderer = ModernGLRenderer(width, height, config)

    use_vaapi = video_cfg.get("use_vaapi", True)
    vaapi_device = video_cfg.get("vaapi_device", "/dev/dri/renderD128")
    audio_bitrate = video_cfg.get("audio_bitrate", "192k")

    encoder = FFmpegEncoder(
        audio_path=audio_path,
        output_mp4=output_mp4,
        width=width,
        height=height,
        fps=fps,
        use_vaapi=use_vaapi,
        vaapi_device=vaapi_device,
        audio_bitrate=audio_bitrate,
    ).start()

    print(f"[*] ModernGL Modular Pipeline Active ({width}x{height} @ {fps}fps - {num_frames} frames)...")

    current_fbo, current_tex = renderer.fbo_a, renderer.tex_a
    prev_tex = renderer.tex_b

    try:
        for i in tqdm(range(num_frames), desc=f"Rendering {os.path.basename(output_mp4)}"):
            frame_ctx = {
                "time": float(times[i]),
                "frame_index": i,
                "perc_exp": float(perc_exps[i]),
                "morph_param": float(morph_params[i]),
                "highs": float(highs[i]),
                "mids": float(mids[i]),
                "width": width,
                "height": height,
            }

            active_fbo = renderer.render_frame(frame_ctx, current_fbo, prev_tex)

            frame_bytes = active_fbo.read(components=3, alignment=1)
            encoder.write_frame(frame_bytes)

            current_fbo, prev_tex = (
                (renderer.fbo_b, renderer.tex_a)
                if current_fbo == renderer.fbo_a
                else (renderer.fbo_a, renderer.tex_b)
            )

    finally:
        try:
            encoder.close()
        finally:
            renderer.destroy()

    print(f"[✔] Render complete: {output_mp4}")
