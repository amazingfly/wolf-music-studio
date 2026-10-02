#!/usr/bin/env python3
"""
ModernGL High-Speed GPU Visualizer (Modular Architecture)
"""
import os
import sys
import argparse
from copy import deepcopy
import json
from pathlib import Path

from vis.config import load_config
from vis.renderer import render_visualizer_pipeline


def parse_args():
    parser = argparse.ArgumentParser(description="ModernGL High-Speed Modular GPU Visualizer")
    parser.add_argument("audio", nargs="?", default=None, help="Path to single .ogg file or folder")
    parser.add_argument("-c", "--config", default="config.json", help="Path to JSON configuration file (default: config.json)")
    parser.add_argument("-o", "--output-dir", default=None, help="Output directory (overrides config)")
    parser.add_argument("--fps", type=int, default=None, help="FPS (overrides config)")
    parser.add_argument("--no-cellphone", action="store_true", help="Disable 9:16 portrait mode")
    parser.add_argument("--no-widescreen", action="store_true", help="Disable 16:9 widescreen mode")
    parser.add_argument("--max-duration", type=float, default=None, help="Max duration in seconds to render (for testing)")
    parser.add_argument("--max-frames", type=int, default=None, help="Max frame count to render (for testing)")
    parser.add_argument('--karaoke', action='store_true', help='Recover lyrics and add word-highlighted captions')
    parser.add_argument('--lyrics', help='Explicit original lyric text/JSON prompt')
    parser.add_argument('--prompt-root', default=os.environ.get('YUE2_ROOT', str(Path(__file__).resolve().parent.parent / 'yue2')))
    parser.add_argument('--vocals', help='Already separated vocals for a single song')
    parser.add_argument('--words', help='Existing/editable karaoke words.json for a single song')
    parser.add_argument('--prepare-only', action='store_true', help='Prepare captions without rendering video')
    return parser.parse_args()


def main():
    args = parse_args()
    config = load_config(args.config)

    # CLI overrides for config
    video_cfg = config.get("video", {})
    fps = args.fps if args.fps is not None else video_cfg.get("fps", 30)
    output_dir = args.output_dir if args.output_dir is not None else video_cfg.get("output_dir", "./output")

    if args.audio:
        if os.path.isdir(args.audio):
            audio_files = [os.path.join(args.audio, f) for f in os.listdir(args.audio) if f.lower().endswith((".ogg", ".wav", ".flac", ".mp3"))]
        elif os.path.isfile(args.audio):
            audio_files = [args.audio]
        else:
            print(f"[!] Path '{args.audio}' not found.")
            sys.exit(1)
    else:
        ogg_dir = "./ogg"
        if not os.path.exists(ogg_dir):
            os.makedirs(ogg_dir, exist_ok=True)
            print(f"[!] Drop .ogg files in './ogg' and run again.")
            sys.exit(0)
        audio_files = [os.path.join(ogg_dir, f) for f in os.listdir(ogg_dir) if f.lower().endswith((".ogg", ".wav", ".flac", ".mp3"))]

    if not audio_files:
        print("[!] No audio files found to process.")
        sys.exit(0)

    if len(audio_files) != 1 and (args.words or args.vocals or args.lyrics):
        raise ValueError('--words, --vocals and --lyrics require a single audio file')

    render_wide = not args.no_widescreen
    render_port = not args.no_cellphone

    if render_wide:
        os.makedirs(os.path.join(output_dir, "widescreen"), exist_ok=True)
    if render_port:
        os.makedirs(os.path.join(output_dir, "portrait"), exist_ok=True)

    for idx, audio_path in enumerate(audio_files, 1):
        song_config = deepcopy(config)
        if args.karaoke or args.words or args.prepare_only:
            from vis.karaoke import prepare, validate_timeline, fingerprint
            if args.words:
                timeline = validate_timeline(json.loads(Path(args.words).read_text()))
                if timeline.get('audio_sha256') != fingerprint(audio_path):
                    raise ValueError('The supplied caption timeline belongs to a different audio file')
                words_file = args.words
            else:
                words_file = prepare(audio_path, output_root=os.path.join(output_dir, 'karaoke'),
                                     prompt_root=args.prompt_root, lyrics_file=args.lyrics, vocals=args.vocals)
            song_config.setdefault('karaoke', {})['words_file'] = str(words_file)
        if args.prepare_only:
            continue
        stem = os.path.splitext(os.path.basename(audio_path))[0]
        print(f"\n========================================")
        print(f" Processing [{idx}/{len(audio_files)}]: {os.path.basename(audio_path)}")
        print(f"========================================")

        if render_wide:
            out_wide = os.path.join(output_dir, "widescreen", f"{stem}.mp4")
            render_visualizer_pipeline(
                audio_path=audio_path,
                output_mp4=out_wide,
                width=1280,
                height=720,
                fps=fps,
                config=song_config,
                max_duration=args.max_duration,
                max_frames=args.max_frames,
            )

        if render_port:
            out_port = os.path.join(output_dir, "portrait", f"{stem}.mp4")
            render_visualizer_pipeline(
                audio_path=audio_path,
                output_mp4=out_port,
                width=720,
                height=1280,
                fps=fps,
                config=song_config,
                max_duration=args.max_duration,
                max_frames=args.max_frames,
            )


if __name__ == "__main__":
    main()
