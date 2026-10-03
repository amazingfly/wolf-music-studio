#!/usr/bin/env python3
"""Create verified lossless, strictly truncated masters after local downloads.

Also runnable independently: python processTracks.py /path/to/run-or-outputs
"""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys

import autoTrim
from track_registry import DEFAULT_DATABASE, atomic_json, key_for, locked_database, record_for, merge_record

SETTINGS = autoTrim.Settings(late_audio='truncate')
WORKFLOW_VERSION = 1


def prompt_for(source):
    request = source.parent / 'request.json'
    if request.exists():
        value = json.loads(request.read_text())
        if not isinstance(value, dict):
            raise ValueError(f'Invalid request object: {request}')
        return value
    requests = source.parent.parent / 'requests.jsonl'
    if requests.exists():
        for line in requests.read_text().splitlines():
            if line.strip():
                value = json.loads(line)
                if value.get('id') == source.parent.name:
                    return value
    return {}


def register(source, target, report, database, error=None):
    now = datetime.now(timezone.utc).isoformat()
    workflow = {
        'version': WORKFLOW_VERSION, 'engine_version': autoTrim.VERSION,
        'status': 'failed' if error else 'complete', 'method': 'truncate',
        'format': 'FLAC', 'lossless': True, 'original_path': str(source),
        'output_path': str(target), 'report_path': str(target.with_name(target.stem + '_trim.json')),
        'updated_at': now,
    }
    if error:
        workflow['error'] = str(error)
    else:
        workflow.update({field: report[field] for field in (
            'source_sha256', 'output_sha256', 'pcm_sha256', 'input_seconds',
            'output_seconds', 'removed_seconds', 'keep_frame_intervals',
            'samplerate', 'channels', 'subtype', 'flags', 'settings')})
    with locked_database(database) as data:
        original = dict(record_for(data, source))
        original.update(track_identifier=source.parent.name, file_name=source.name,
                        file_path=str(source), generation_batch=source.parent.parent.name,
                        audio_workflow={**workflow, 'role': 'original'}, trimmed=False)
        if original.get('visualizer_workflow') and (error or
                original['visualizer_workflow'].get('audio_sha256') != report['output_sha256']):
            original['visualizer_workflow'] = {**original['visualizer_workflow'], 'status': 'stale'}
        # Failed audio processing must still be recorded even if prompt JSON is damaged.
        try:
            prompt = prompt_for(source)
        except (OSError, ValueError, AttributeError) as exc:
            prompt = {}
            original['prompt_error'] = str(exc)
        if prompt:
            original['prompt'] = prompt
            original['title'] = prompt.get('title', source.parent.name)
        data[key_for(data, source)] = original
        atomic_json(source.with_name('audio_metadata.json'), original)
        old = record_for(data, target)
        if error and not old.get('audio_workflow'):
            return
        master = dict(old)
        if master.get('visualizer_workflow') and (error or
                master['visualizer_workflow'].get('audio_sha256') != report['output_sha256']):
            master['visualizer_workflow'] = {**master['visualizer_workflow'], 'status': 'stale'}
        if not error:
            # A changed crop invalidates analysis of the previous audio, but tags can
            # be inherited for browsing until TrackTags analyzes this master itself.
            changed = old.get('audio_workflow', {}).get('output_sha256') != report['output_sha256']
            if changed:
                for field in ('audio_metrics', 'description', 'description_keywords', 'analysis_source_path', 'analysis_audio_sha256'):
                    master.pop(field, None)
                for field in ('hashtags', 'top_genres', 'hashtags_top5'):
                    if original.get(field):
                        master[field] = original[field]
                if master.get('hashtags'):
                    master['hashtags_top5'] = ' '.join(master['hashtags'][:5])
                master['analysis_status'] = 'inherited' if master.get('hashtags') else 'pending'
                from_original = bool(original.get('hashtags') or original.get('top_genres'))
                master['analysis_source_path'] = str(source if from_original else target)
                master['analysis_source_sha256'] = (report['source_sha256'] if from_original else
                    old.get('audio_workflow', {}).get('output_sha256', report.get('previous_output_sha256')))
                master['inherited_from_previous_revision'] = not from_original and bool(master.get('hashtags'))
            master.update(preferred_audio=str(target), visualizer_audio=str(target),
                          trimmed=True, duration_seconds=report['output_seconds'])
        master.update(track_identifier=source.parent.name, file_name=target.name,
                      file_path=str(target), generation_batch=source.parent.parent.name,
                      audio_workflow={**workflow, 'role': 'master'})
        if prompt:
            master['prompt'] = prompt
            master['title'] = prompt.get('title', source.parent.name)
        data[key_for(data, target)] = master
        atomic_json(target.with_name(target.stem + '_metadata.json'), master)


def process_track(source, database=DEFAULT_DATABASE, overwrite=False, force=False):
    """Idempotent, locked processing; never replace an unrelated existing export by default."""
    source = Path(source).resolve()
    if source.name != 'audio.flac' or not source.is_file():
        raise ValueError(f'Expected an original audio.flac: {source}')
    target = source.with_name(source.parent.name + '.flac')
    if target == source:
        raise ValueError('Track directory must not be named audio; output would replace the source')
    report_path = target.with_name(target.stem + '_trim.json')
    with source.with_name('.audio_processing.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            if Path(database).exists() and not isinstance(json.loads(Path(database).read_text()), dict):
                raise ValueError(f'Expected a JSON object: {database}; refusing to overwrite it')
            before = autoTrim.sha256(source)
            receipt_path = source.with_name('result.json')
            if receipt_path.exists():
                receipt = json.loads(receipt_path.read_text())
                if receipt.get('status') != 'complete':
                    raise ValueError('Generation receipt is not complete; wait for download completion')
                if receipt.get('audio_sha256') and receipt['audio_sha256'] != before:
                    raise ValueError('Original audio differs from generation receipt; redownload before processing')
            previous = json.loads(report_path.read_text()) if report_path.exists() else {}
            if not isinstance(previous, dict):
                raise ValueError(f'Invalid trimming report: {report_path}')
            owned = (previous.get('source') == str(source) and previous.get('output') == str(target)
                     and previous.get('variant') == 'truncate')
            previous_output_hash = None
            if target.exists():
                output_hash = autoTrim.sha256(target)
                previous_output_hash = output_hash
                intact = owned and previous.get('output_sha256') == output_hash
                current = (intact and previous.get('source_sha256') == before
                           and previous.get('version') == autoTrim.VERSION
                           and previous.get('settings') == asdict(SETTINGS))
                if current and not force:
                    register(source, target, previous, database)
                    return {**previous, 'processing_status': 'unchanged'}
                if not intact and not overwrite:
                    raise FileExistsError(f'Preserving existing or modified export: {target}; use --overwrite explicitly')
            report = autoTrim.trim_file(source, SETTINGS, overwrite=target.exists(),
                                       output_path=target, report_path=report_path)
            if previous_output_hash is not None:
                report['previous_output_sha256'] = previous_output_hash
                atomic_json(report_path, report)
            register(source, target, report, database)
            return {**report, 'processing_status': 'processed'}
        except Exception as exc:
            try:
                register(source, target, {}, database, error=exc)
            except Exception as recording_error:
                raise RuntimeError(f'{exc}; could not record failure: {recording_error}') from exc
            raise


def tag_processed_track(target, database=DEFAULT_DATABASE):
    """Tag the verified master with a separate CPU process; retry failed/missing analysis."""
    target = Path(target).resolve()
    from importlib.util import spec_from_file_location, module_from_spec
    script = Path(__file__).resolve().parent/'outputs/trackTags.py'
    spec = spec_from_file_location('track_tags_status', script)
    tags = module_from_spec(spec)
    spec.loader.exec_module(tags)
    with target.with_name('.track_tagging.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = json.loads(Path(database).read_text())
        record = record_for(data, target)
        if tags.is_finished(record) and autoTrim.sha256(target) == record.get('analysis_audio_sha256'):
            if record.get('tagging_workflow', {}).get('status') == 'failed':
                merge_record(target, {'tagging_workflow': {'status': 'complete',
                             'updated_at': datetime.now(timezone.utc).isoformat()}}, database)
            return {'status': 'complete', 'hashtags_top5': record['hashtags_top5'], 'cached': True}
        environment = {**os.environ, 'TF_CPP_MIN_LOG_LEVEL': '2', 'CUDA_VISIBLE_DEVICES': '-1',
                       'OMP_NUM_THREADS': '2', 'TF_NUM_INTRAOP_THREADS': '2', 'TF_NUM_INTEROP_THREADS': '1'}
        interpreter = os.environ.get('YUE2_TAG_PYTHON', sys.executable)
        try:
            subprocess.run([interpreter, '-u', str(script), str(target), '--database', str(database)],
                           check=True, timeout=600, env=environment)
            record = record_for(json.loads(Path(database).read_text()), target)
            if not tags.is_finished(record) or autoTrim.sha256(target) != record.get('analysis_audio_sha256'):
                raise ValueError('Tagger did not publish current, complete master analysis')
            workflow = {'status': 'complete', 'updated_at': datetime.now(timezone.utc).isoformat()}
            merge_record(target, {'tagging_workflow': workflow}, database)
            return {**workflow, 'hashtags_top5': record['hashtags_top5'], 'cached': False}
        except Exception as exc:
            merge_record(target, {'tagging_workflow': {'status': 'failed', 'error': str(exc),
                         'updated_at': datetime.now(timezone.utc).isoformat()}}, database)
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path, help='Directory to recursively scan, or a single audio.flac')
    parser.add_argument('--database', type=Path, default=DEFAULT_DATABASE, help='Global JSON database path')
    parser.add_argument('--force', action='store_true', help='Recompute unchanged masters already owned by this workflow')
    parser.add_argument('--overwrite', action='store_true', help='Allow replacing existing or modified named exports')
    parser.add_argument('--no-tags', action='store_true', help='Trim only, without running TrackTags')
    args = parser.parse_args(argv)
    directory = args.directory.resolve()
    if not directory.exists():
        parser.error(f'Path does not exist: {directory}')
    if directory.is_file() and directory.name != 'audio.flac':
        parser.error('A single input file must be named audio.flac')
    sources = [directory] if directory.is_file() else sorted(directory.rglob('audio.flac'))
    if not sources:
        parser.error(f'No original audio.flac files found: {directory}')
    counts = {'processed': 0, 'unchanged': 0, 'failed': 0, 'tagging_failed': 0}
    runs = set()
    for source in sources:
        try:
            result = process_track(source, args.database, args.overwrite, args.force)
            counts[result['processing_status']] += 1
            print(f"{source.parent.name}: {result['processing_status']} -> {Path(result['output']).name}", flush=True)
            if not args.no_tags:
                try:
                    tagged = tag_processed_track(result['output'], args.database)
                    print(f"  Top 5: {tagged['hashtags_top5']}", flush=True)
                except Exception as exc:
                    counts['tagging_failed'] += 1
                    print(f'Tagging failed (rerun to retry): {source.parent.name}: {exc}', flush=True)
        except Exception as exc:
            counts['failed'] += 1
            print(f'{source}: FAILED: {exc}', flush=True)
        runs.add(source.parent.parent)
    from track_catalog import sync_run_safely
    for run in sorted(runs):
        sync_run_safely(run)
    print(f"Processed {counts['processed']}, unchanged {counts['unchanged']}, failed {counts['failed']}, tagging failed {counts['tagging_failed']}. Database: {args.database}")
    return 1 if counts['failed'] or counts['tagging_failed'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
