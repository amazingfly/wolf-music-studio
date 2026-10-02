import argparse
import glob
import os
import tempfile
from pathlib import Path
import json
import urllib.request
import sys

# Model URLs & Filenames
MODEL_FILES = {
    "discogs-effnet-bs64-1.pb": "https://essentia.upf.edu/models/feature-extractors/discogs-effnet/discogs-effnet-bs64-1.pb",
    "genre_discogs400-discogs-effnet-1.json": "https://essentia.upf.edu/models/classification-heads/genre_discogs400/genre_discogs400-discogs-effnet-1.json"
}

EMBEDDING_GRAPH = "discogs-effnet-bs64-1.pb"
GENRE_JSON = "genre_discogs400-discogs-effnet-1.json"
SCRIPT_DIR = Path(__file__).resolve().parent
EMBEDDING_GRAPH = str(SCRIPT_DIR / EMBEDDING_GRAPH)
GENRE_JSON = str(SCRIPT_DIR / GENRE_JSON)
MASTER_DB_FILE = str(SCRIPT_DIR / "master_database.json")
sys.path.insert(0, str(SCRIPT_DIR.parent))
from track_registry import merge_record


def update_catalog(path):
    """Keep the browser current without making tagging depend on catalog health."""
    try:
        root = str(SCRIPT_DIR.parent)
        if root not in sys.path:
            sys.path.insert(0, root)
        from track_catalog import sync_catalog
        run = Path(path).resolve().parent.parent
        generated = (run / 'requests.jsonl').exists() or (Path(path).parent / 'result.json').exists()
        _, warnings = sync_catalog(outputs=SCRIPT_DIR, run=run if generated else None)
        for warning in warnings:
            print(f"  Catalog warning: {warning}")
    except Exception as exc:
        print(f"  Catalog update deferred (run trackLibrary.py --refresh): {exc}")

def download_models_if_missing():
    """Ensure required model files are present locally."""
    opener = urllib.request.build_opener()
    opener.addheaders = [('User-agent', 'Mozilla/5.0')]
    urllib.request.install_opener(opener)

    for filename, url in MODEL_FILES.items():
        filename = str(SCRIPT_DIR / filename)
        if not os.path.exists(filename):
            print(f"Downloading missing model file: {filename}...")
            urllib.request.urlretrieve(url, filename)
            print(f"  ✓ {filename} downloaded.")

def analyze_track(file_path, classes):
    """Extracts features, runs Essentia EffNet, and generates metadata."""
    import numpy as np
    import essentia.standard as es

    # 1. Load full audio at 16kHz
    audio = es.MonoLoader(filename=file_path, sampleRate=16000)()

    # 2. Run EffNet Discogs model (Outputs shape: [num_frames, 400])
    effnet_model = es.TensorflowPredictEffnetDiscogs(graphFilename=EMBEDDING_GRAPH)
    predictions = effnet_model(audio)

    # 3. Average predictions across all frames
    mean_predictions = np.mean(predictions, axis=0)

    # Top 5 genres
    top_indices = np.argsort(mean_predictions)[::-1][:5]
    top_genres = []
    hashtags = []

    for idx in top_indices:
        raw_label = classes[idx]
        clean_label = raw_label.replace("---", " / ")
        score = float(mean_predictions[idx])
        top_genres.append({"genre": clean_label, "confidence": round(score, 4)})
        
        # Format hashtag (e.g., #Industrial, #Darkwave)
        sub_tag = clean_label.split(" / ")[-1].replace(" ", "").replace("-", "")
        hashtags.append(f"#{sub_tag}")

    # 4. Extract acoustic metrics (using *_ to safely handle all return values)
    bpm, *_ = es.RhythmExtractor2013()(audio)
    key, scale, *_ = es.KeyExtractor()(audio)
    danceability, *_ = es.Danceability()(audio)

    desc_words = []
    if bpm < 90:
        desc_words.append("slow-tempo")
    elif bpm < 128:
        desc_words.append("mid-tempo")
    else:
        desc_words.append("up-tempo")

    desc_words.append(f"{int(round(bpm))} BPM")
    desc_words.append(f"{key} {scale}")

    if danceability > 1.2:
        desc_words.append("highly rhythmic/danceable")
    elif danceability < 0.8:
        desc_words.append("atmospheric/ambient feel")

    for g in top_genres[:3]:
        desc_words.append(g["genre"].lower())

    description = f"A {desc_words[0]} {top_genres[0]['genre']} track in {key} {scale} at {int(round(bpm))} BPM."

    return {
        "top_genres": top_genres,
        "hashtags": hashtags,
        "description": description,
        "description_keywords": desc_words,
        "audio_metrics": {
            "bpm": round(float(bpm), 2),
            "key": f"{key} {scale}",
            "danceability": round(float(danceability), 2)
        }
    }

ANALYSIS_FIELDS = (
    "top_genres", "hashtags", "description", "description_keywords", "audio_metrics"
)


def is_finished(record):
    if isinstance(record, dict) and record.get('audio_workflow', {}).get('role') == 'master':
        workflow = record['audio_workflow']
        if (workflow.get('status') != 'complete' or record.get('analysis_status') != 'complete'
                or record.get('analysis_audio_sha256') != workflow.get('output_sha256')):
            return False
    return (
        isinstance(record, dict)
        and all(record.get(field) for field in ANALYSIS_FIELDS)
        and all(key in record["audio_metrics"] for key in ("bpm", "key", "danceability"))
    )


def track_title(path):
    """Match exports to generated tracks while keeping version suffixes distinct."""
    path = Path(path)
    title = path.parent.name if path.stem.lower() == "audio" else path.stem
    if title.lower().startswith("trim_"):
        title = title[5:]
    return title.casefold()


def save_json(path, data):
    """Replace JSON atomically so an interrupted write leaves the old file intact."""
    path = Path(path)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as f:
            temp_name = f.name
            json.dump(data, f, indent=4)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_name, path)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Tag audio and resume without reanalyzing completed tracks.")
    parser.add_argument("files", nargs="*", help="OGG, FLAC or WAV filenames (quoted globs also work); default: scan current directory")
    args = parser.parse_args(argv)
    extensions = {".ogg", ".flac", ".wav"}
    paths = []
    if args.files:
        for pattern in args.files:
            matches = [pattern] if Path(pattern).is_file() else sorted(glob.glob(pattern))
            if not matches:
                parser.error(f"No files match: {pattern}")
            for match in matches:
                path = Path(match)
                if not path.is_file() or path.suffix.lower() not in extensions:
                    parser.error(f"Expected an OGG, FLAC or WAV file: {match}")
                paths.append(path.resolve())
    else:
        paths = sorted(path.resolve() for path in Path.cwd().rglob("*")
                       if path.is_file() and path.suffix.lower() in extensions)
    paths = list(dict.fromkeys(paths))

    db_path = Path(MASTER_DB_FILE)
    master_db = json.loads(db_path.read_text()) if db_path.exists() else {}
    if not isinstance(master_db, dict):
        raise ValueError(f"Expected a JSON object in {db_path}; refusing to overwrite it")
    records_by_path = {}
    records_by_title = {}
    for key, record in master_db.items():
        if isinstance(record, dict) and record.get("file_path"):
            records_by_path.setdefault(str(Path(record["file_path"]).resolve()), []).append(key)
            if is_finished(record):
                records_by_title.setdefault(track_title(record["file_path"]), record)

    classes = None
    processed = skipped = restored = reused = errors = 0
    for path in paths:
        audio_path = str(path)
        existing_keys = records_by_path.get(audio_path, [])
        local_path = path.with_name(f"{path.stem}_metadata.json")
        completed = next((master_db[key] for key in existing_keys
                          if is_finished(master_db[key])), None)
        if completed is not None:
            if completed.get('audio_workflow', {}).get('role') == 'master':
                from autoTrim import sha256
                if sha256(path) != completed['analysis_audio_sha256']:
                    print(f'Error: master audio changed: {path}; rerun processTracks.py first')
                    errors += 1
                    continue
            if not local_path.exists():
                try:
                    _, completed = merge_record(path, completed, db_path)
                    print(f"Restored local metadata: {local_path}")
                    restored += 1
                except OSError as e:
                    print(f"  Error restoring {local_path}: {e}")
                    errors += 1
                    continue
            print(f"Skipping completed: {path}")
            skipped += 1
            update_catalog(path)
            continue

        # Generic generated files use their folder name; named songs retain their title.
        track_id = path.parent.name if path.stem.lower() == "audio" else path.stem
        key = existing_keys[0] if existing_keys else f"{track_id}_{path.name}"
        if key in master_db and key not in existing_keys:
            # Different folders can contain identically named songs.
            key = audio_path
            if key in master_db and key not in existing_keys:
                print(f"Error: database key collision for {path}; preserving existing record")
                errors += 1
                continue
        try:
            base = dict(master_db.get(key, {}))
            if local_path.exists():
                track_data = json.loads(local_path.read_text())
                if not isinstance(track_data, dict) or str(Path(track_data.get("file_path", "")).resolve()) != audio_path:
                    raise ValueError(f"Existing metadata is incomplete or belongs to another file: {local_path}; preserving it")
                if is_finished(track_data):
                    # Recover completed work absent from the database without inference.
                    key, track_data = merge_record(path, track_data, db_path)
                    master_db[key] = track_data
                    records_by_path[audio_path] = [key]
                    records_by_title.setdefault(track_title(path), track_data)
                    restored += 1
                    print(f"Restored completed metadata to database: {path}")
                    update_catalog(path)
                    continue
                if not track_data.get('audio_workflow'):
                    raise ValueError(f"Existing metadata is incomplete: {local_path}; preserving it")
                base.update(track_data)

            source = records_by_title.get(track_title(path))
            workflow = base.get('audio_workflow', {})
            input_hash = None
            if workflow.get('role') == 'master':
                from autoTrim import sha256
                # Recompute acoustic metrics for the cropped audio itself.
                source = None
                input_hash = sha256(path)
                if workflow.get('status') != 'complete' or input_hash != workflow.get('output_sha256'):
                    raise ValueError('Master audio is stale or changed; rerun processTracks.py first')
            if source is not None:
                print(f"Reusing completed analysis: {source['file_path']} -> {path}")
                analysis_result = {field: source[field] for field in ANALYSIS_FIELDS}
                analysis_result["analysis_source_path"] = source.get("analysis_source_path", source["file_path"])
            else:
                if classes is None:
                    download_models_if_missing()
                    with open(GENRE_JSON) as f:
                        classes = json.load(f)["classes"]
                print(f"Processing: {path} (ID: {track_id})")
                analysis_result = analyze_track(audio_path, classes)
            track_data = {
                **base,
                "track_identifier": track_id,
                "file_name": path.name,
                "file_path": audio_path,
                **analysis_result,
                'analysis_status': 'complete',
            }
            if input_hash:
                if sha256(path) != input_hash:
                    raise ValueError('Audio changed during tagging; retry')
                track_data['analysis_audio_sha256'] = input_hash
                track_data['analysis_source_path'] = audio_path
                track_data['analysis_source_sha256'] = input_hash
                track_data['inherited_from_previous_revision'] = False
            if not is_finished(track_data):
                raise ValueError("Analysis returned incomplete metadata")
            key, track_data = merge_record(path, track_data, db_path)
            master_db[key] = track_data
            records_by_path[audio_path] = [key]
            records_by_title.setdefault(track_title(path), track_data)
            if source is not None:
                reused += 1
            else:
                processed += 1
            print(f"  Saved -> {local_path}")
            update_catalog(path)
        except Exception as e:
            errors += 1
            print(f"  Error processing {path}: {e}")

    print(f"Done! Processed {processed}, reused {reused}, skipped {skipped}, restored {restored}, errors {errors}. Database: {db_path}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
