#!/usr/bin/env python3
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# Configuration
SOURCE_DIR = Path(os.environ.get('YUE2_ROOT', str(Path(__file__).resolve().parent.parent / 'yue2'))) / 'outputs'
DEST_OGG_DIR = Path("./ogg")
DB_JSON_PATH = Path("audio_database.json")
VIS_SCRIPT = str(Path(__file__).resolve().parent / "main.py")


def get_clean_name_without_trim(filename: str) -> str:
    """Removes 'TRIM_' or 'TRIM' prefix from filename."""
    if filename.startswith("TRIM_"):
        return filename[5:]
    elif filename.startswith("TRIM"):
        return filename[4:]
    return filename


def main():
    print(f" Scanning directory: {SOURCE_DIR}")
    if not SOURCE_DIR.exists():
        print(f" Error: Source directory '{SOURCE_DIR}' does not exist.")
        sys.exit(1)

    DEST_OGG_DIR.mkdir(parents=True, exist_ok=True)

    # Step 1: Scan all .flac and .ogg files
    audio_files = []
    files_by_name_size = {}

    for file_path in SOURCE_DIR.rglob("*"):
        if file_path.is_file() and file_path.suffix.lower() in [".flac", ".ogg"]:
            file_stat = file_path.stat()
            size_bytes = file_stat.st_size
            filename = file_path.name
            parent_dir = file_path.parent.name
            rel_path = str(file_path.relative_to(SOURCE_DIR))

            key = (filename, size_bytes)
            files_by_name_size.setdefault(key, []).append(str(file_path))

            audio_files.append(
                {
                    "filename": filename,
                    "stem": file_path.stem,
                    "extension": file_path.suffix.lower(),
                    "size_bytes": size_bytes,
                    "parent_directory": parent_dir,
                    "absolute_path": str(file_path.resolve()),
                    "relative_path": rel_path,
                    "is_duplicate": False,  # Will be updated below
                }
            )

    # Step 2: Mark duplicates (Exact filename match AND exact byte size match)
    duplicate_keys = {
        key for key, paths in files_by_name_size.items() if len(paths) > 1
    }
    for item in audio_files:
        key = (item["filename"], item["size_bytes"])
        if key in duplicate_keys:
            item["is_duplicate"] = True

    # Save JSON Database
    with open(DB_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(audio_files, f, indent=2)

    print(
        f" Database generated: {DB_JSON_PATH} ({len(audio_files)} tracks indexed)"
    )
    print(f" Found {len(duplicate_keys)} duplicate filename+size groups.")

    # Step 3: Identify existing OGG files for check
    # Collect all existing OGG stems / clean names across source tree and destination folder
    existing_ogg_stems = set()

    for item in audio_files:
        if item["extension"] == ".ogg":
            existing_ogg_stems.add(item["stem"])
            existing_ogg_stems.add(get_clean_name_without_trim(item["stem"]))

    if DEST_OGG_DIR.exists():
        for ogg_file in DEST_OGG_DIR.glob("*.ogg"):
            existing_ogg_stems.add(ogg_file.stem)

    # Step 4: Check TRIM FLAC files for missing OGG conversions
    trim_flacs = [
        item
        for item in audio_files
        if item["extension"] == ".flac" and item["filename"].startswith("TRIM")
    ]

    print(f" Found {len(trim_flacs)} TRIM .flac tracks.")

    for flac_item in trim_flacs:
        flac_path = Path(flac_item["absolute_path"])
        flac_stem = flac_item["stem"]
        clean_stem = get_clean_name_without_trim(flac_stem)

        # Check if converted ogg exists anywhere
        is_converted = (
            flac_stem in existing_ogg_stems or clean_stem in existing_ogg_stems
        )

        if not is_converted:
            # Convert near-lossless OGG using ffmpeg (libvorbis qscale 9 ~320kbps)
            output_ogg_same_dir = flac_path.with_suffix(".ogg")
            print(f" Converting: {flac_path.name} -> {output_ogg_same_dir.name}")

            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                str(flac_path),
                "-c:a",
                "libvorbis",
                "-qscale:a",
                "9",
                str(output_ogg_same_dir),
            ]

            try:
                subprocess.run(
                    cmd,
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
                existing_ogg_stems.add(flac_stem)
                existing_ogg_stems.add(clean_stem)
            except subprocess.CalledProcessError as e:
                print(
                    f" Failed to convert {flac_path.name}: {e.stderr.decode()}"
                )

    # Step 5: Transfer every TRIM*.ogg to ./ogg removing 'TRIM_' prefix
    # Search both source tree and current working directory
    all_ogg_files = list(SOURCE_DIR.rglob("TRIM*.ogg")) + list(
        Path(".").glob("TRIM*.ogg")
    )

    transferred_count = 0
    for ogg_path in all_ogg_files:
        clean_filename = get_clean_name_without_trim(ogg_path.name)
        target_path = DEST_OGG_DIR / clean_filename

        if ogg_path.resolve() != target_path.resolve():
            shutil.copy2(ogg_path, target_path)
            transferred_count += 1

    print(f" Transferred {transferred_count} TRIM .ogg files to {DEST_OGG_DIR}")

    # Step 6: Launch python vis2.py
    print(f" Launching {VIS_SCRIPT}...")
    if Path(VIS_SCRIPT).exists():
        subprocess.run([sys.executable, VIS_SCRIPT])
    else:
        print(f" Warning: '{VIS_SCRIPT}' not found in current directory.")


if __name__ == "__main__":
    main()
