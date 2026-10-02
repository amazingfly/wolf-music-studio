#!/usr/bin/env python3
"""Interactive YuE2 track library: search, listen, and copy prompts or hashtags."""
from __future__ import annotations

import argparse
import curses
from datetime import date
import json
from pathlib import Path
import queue
import re
import sqlite3
import sys
import textwrap
import threading
import time

from track_catalog import DEFAULT_DB, ROOT, hashtags, initialized, load_tracks, prompt_text, search, sync_catalog
from track_player import Player, copy_clipboard
from track_catalog import create_category, list_categories, toggle_favorite, export_category

FILTERS = [
    ('category', 'Favorite category (exact name)'),
    ('batch', 'Batch contains'), ('from', 'Date from (YYYY-MM-DD)'), ('to', 'Date through (YYYY-MM-DD)'),
    ('tags', 'All tags (spaces or commas)'), ('trimmed', 'Trimmed: blank / yes / no'),
    ('files', 'FLAC: blank / original-only / named'), ('status', 'Status: blank / available / pending / missing'),
    ('untagged', 'Untagged only: blank / yes'), ('text', 'Style, lyrics, genre, BPM, key or description'),
]


def validate_filters(values):
    values = {key: value.strip() for key, value in values.items()}
    for key in ('from', 'to'):
        if values.get(key):
            values[key] = date.fromisoformat(values[key]).isoformat()
    if values.get('from') and values.get('to') and values['from'] > values['to']:
        raise ValueError('Start date must not be after end date')
    for key, allowed in {'trimmed': {'', 'yes', 'no'}, 'files': {'', 'original-only', 'named'},
                         'status': {'', 'available', 'pending', 'missing'}, 'untagged': {'', 'yes'}}.items():
        if values.get(key, '') not in allowed:
            raise ValueError(f'{key}: use ' + ' / '.join(sorted(allowed - {''})))
    return {key: value.strip() for key, value in values.items() if value.strip()}


def clock(value):
    value = max(0, int(value or 0))
    return f'{value // 60}:{value % 60:02d}'


def detail_lines(track, file_index=0):
    if not track:
        return ['No matching tracks.', '', 'Type a song name, or use F2 for advanced search.', 'Esc clears the name search.']
    files = track['files']
    selected = files[file_index % len(files)] if files else None
    metadata = selected['metadata'] if selected else {}
    metrics = metadata.get('audio_metrics', {})
    lines = [track['title'], f"ID: {track['id']}", f"Batch: {track['batch']}",
             'Favorites: ' + (', '.join(track.get('categories', [])) or '(none; F11 to add)'),
             f"Date: {track['date'] or 'unknown'}", f"Status: {track['status']} | Duration: {clock(track['duration']) if track['duration'] else 'unknown'}",
             f"Trimmed file: {'yes' if track['trimmed'] else 'no'} | Only audio.flac: {'yes' if track['only_audio_flac'] else 'no'} | Named FLAC: {'yes' if track['named_flac'] else 'no'}",
             f"Processing: {track.get('processing_status', 'pending')} | Original duration: {clock(track.get('original_duration', track['duration']))}",
             '', 'FILES (F6 selects version for playback and tag copy)']
    for index, file in enumerate(files):
        lines.append(f"{'>' if index == file_index else ' '} {file['name']} [{file['kind']}{'' if file['exists'] else ', missing'}]")
    if selected:
        lines += ['', selected['path'], '', 'TAGS FOR SELECTED FILE', hashtags(metadata) or '(not tagged)',
                  f"BPM: {metrics.get('bpm', '?')} | Key: {metrics.get('key', '?')} | Danceability: {metrics.get('danceability', '?')}",
                  metadata.get('description', '')]
        lines.extend(f"{genre.get('genre', '')}: {genre.get('confidence', '')}" for genre in metadata.get('top_genres', []))
        if metadata.get('analysis_source_path') and (metadata.get('analysis_status') == 'inherited' or metadata['analysis_source_path'] != selected['path']):
            label = 'Tags inherited from: ' if metadata.get('analysis_status') == 'inherited' else 'Analysis reused from: '
            if metadata.get('inherited_from_previous_revision'):
                label = 'Tags inherited from previous audio revision: '
            lines.append(label + metadata['analysis_source_path'])
        workflow = metadata.get('audio_workflow', {})
        if workflow.get('role') == 'master' and workflow.get('status') == 'complete':
            lines += ['Truncated lossless master | removed ' + clock(workflow.get('removed_seconds')),
                      'Visualizer audio: ' + workflow['output_path']]
        video = metadata.get('visualizer_workflow', {})
        if video:
            lines.append('Karaoke video: ' + video.get('status', 'unknown'))
            for orientation, result in video.get('videos', {}).items():
                lines.append(orientation.capitalize() + ': ' + result['path'])
            if video.get('words_file'):
                lines.append('Word timeline: ' + video['words_file'])
            if video.get('error'):
                lines += ['Video error: ' + video['error'], 'Video log: ' + video.get('log_path', '')]
    lines += ['', 'SONG CONFIG (F3 copies complete JSON)']
    if track.get('prompt'):
        lines += ['Style: ' + track['prompt'].get('style', track['prompt'].get('prompt', '')),
                  f"Seed: {track['prompt'].get('seed', '?')} | CoT: {track['prompt'].get('cot', '?')}", '',
                  track['prompt'].get('lyrics', '')]
    else:
        lines.append('No source prompt found; this may be an exported audio file.')
    return lines


class Browser:
    def __init__(self, db=DEFAULT_DB, outputs=None, name='', player=None):
        self.db = Path(db)
        self.outputs = Path(outputs or ROOT / 'outputs')
        self.name = name
        self.filters = {}
        self.tracks = load_tracks(self.db)
        self.rows = []
        self.selected = 0
        self.file_index = 0
        self.details_scroll = 0
        self.player = player or Player()
        self.events = queue.Queue()
        self.refreshing = False
        self.message = 'Type to search names. F3 copies prompt; F4 copies tags. F1 shows help.'
        self.last_reload = time.monotonic()
        self.apply_search()

    @property
    def track(self):
        return self.rows[self.selected] if self.rows else None

    @property
    def file(self):
        files = self.track['files'] if self.track else []
        return files[self.file_index % len(files)] if files else None

    def apply_search(self, preserve=False):
        location = self.track['location'] if preserve and self.track else None
        old_file = self.file['path'] if preserve and self.file else None
        self.rows = search(self.tracks, self.name, self.filters)
        self.selected = next((i for i, row in enumerate(self.rows) if row['location'] == location), 0)
        files = self.track['files'] if self.track else []
        self.file_index = next((i for i, f in enumerate(files) if f['path'] == old_file),
                               next((i for i, f in enumerate(files) if f['exists']), 0))
        if not preserve:
            self.details_scroll = 0

    def move(self, delta):
        self.selected = min(max(self.selected + delta, 0), max(len(self.rows) - 1, 0))
        self.file_index = next((i for i, f in enumerate(self.track['files']) if f['exists']), 0) if self.track else 0
        self.details_scroll = 0

    def copy(self, kind):
        try:
            if kind == 'prompt':
                if not self.track:
                    raise ValueError('Select a track first')
                text = prompt_text(self.track)
            else:
                text = hashtags(self.file['metadata']) if self.file else ''
                if not text:
                    raise ValueError('The selected file has no TrackTags hashtags')
            self.message = f'Copying {kind}...'
            def task():
                try:
                    self.events.put(('message', copy_clipboard(text) + f' ({kind})'))
                except Exception as exc:
                    self.events.put(('message', str(exc)))
            threading.Thread(target=task, daemon=True).start()
        except ValueError as exc:
            self.message = str(exc)

    def refresh(self):
        if self.refreshing:
            return
        self.refreshing = True
        self.message = 'Refreshing prompts, tags and files in the background...'
        def task():
            try:
                count, warnings = sync_catalog(self.outputs, self.db)
                self.events.put(('refresh', f'Imported {count} tracks' + (f'; {len(warnings)} warnings: {warnings[0]}' if warnings else '')))
            except Exception as exc:
                self.events.put(('refresh', f'Refresh failed: {exc}'))
        threading.Thread(target=task, daemon=True).start()

    def poll(self):
        while not self.events.empty():
            kind, value = self.events.get_nowait()
            self.message = value
            if kind == 'refresh':
                self.refreshing = False
                self.last_reload = 0
        if time.monotonic() - self.last_reload > 2:
            self.tracks = load_tracks(self.db)
            self.apply_search(preserve=True)
            self.last_reload = time.monotonic()

    def handle(self, key):
        """Return a modal action or 'quit'; ordinary text always searches names."""
        if key in ('\x11', curses.KEY_F12):
            return 'quit'
        if key == curses.KEY_F1:
            return 'help'
        if key in (curses.KEY_F2, '\x01'):
            return 'advanced'
        if key in (curses.KEY_F11, '\x07'):
            return 'categories'
        if key in (curses.KEY_F3, '\x03'):
            self.copy('prompt')
        elif key in (curses.KEY_F4, '\x14'):
            self.copy('tags')
        elif key in (curses.KEY_F5, '\x12'):
            self.refresh()
        elif key in (curses.KEY_F6, '\x0f'):
            if self.track and self.track['files']:
                self.file_index = (self.file_index + 1) % len(self.track['files'])
                self.message = 'Selected version: ' + self.file['name']
        elif key in (curses.KEY_F7, '\x10'):
            self.player.pause()
        elif key == curses.KEY_F8:
            self.player.stop()
        elif key in (curses.KEY_LEFT, curses.KEY_F9):
            self.player.seek(-10)
        elif key in (curses.KEY_RIGHT, curses.KEY_F10):
            self.player.seek(10)
        elif key == '\x02':
            self.player.seek(-30)
        elif key == '\x06':
            self.player.seek(30)
        elif key in ('\n', '\r', curses.KEY_ENTER):
            try:
                if not self.file:
                    raise ValueError('No audio file is available for this track')
                self.player.play(self.file['path'])
                self.message = 'Playing: ' + self.track['title'] + ' / ' + self.file['name']
            except ValueError as exc:
                self.message = str(exc)
        elif key == curses.KEY_UP:
            self.move(-1)
        elif key == curses.KEY_DOWN:
            self.move(1)
        elif key == curses.KEY_HOME:
            self.move(-len(self.rows))
        elif key == curses.KEY_END:
            self.move(len(self.rows))
        elif key == curses.KEY_NPAGE:
            self.details_scroll += 10
        elif key == curses.KEY_PPAGE:
            self.details_scroll = max(0, self.details_scroll - 10)
        elif key in (curses.KEY_BACKSPACE, '\x7f', '\b'):
            self.name = self.name[:-1]
            self.apply_search()
        elif key in ('\x1b', '\x15'):
            self.name = ''
            self.apply_search()
        elif isinstance(key, str) and key.isprintable():
            self.name += key
            self.apply_search()

    def draw(self, screen):
        screen.erase()
        height, width = screen.getmaxyx()
        if height < 16 or width < 70:
            put(screen, 0, 0, 'Enlarge terminal to at least 70 x 16. Ctrl+Q quits.')
            screen.refresh()
            return
        put(screen, 0, 0, 'YuE2 TRACK LIBRARY', curses.A_BOLD)
        put(screen, 1, 0, 'Name > ' + self.name + '_', curses.A_BOLD)
        active = ' | '.join(f'{key}={value}' for key, value in self.filters.items())
        put(screen, 2, 0, f'{len(self.rows)} matches / {len(self.tracks)} tracks' + (' | ' + active if active else ' | F2 advanced search'))
        split = min(max(30, width * 2 // 5), 60)
        body_height = height - 10
        start = max(0, self.selected - body_height // 2)
        start = min(start, max(0, len(self.rows) - body_height))
        for offset, track in enumerate(self.rows[start:start + body_height]):
            label = ('*' if track.get('categories') else ' ') + ('T ' if track['trimmed'] else '  ') + track['title']
            put(screen, 4 + offset, 0, label, curses.A_BOLD | curses.A_UNDERLINE if start + offset == self.selected else 0, split - 1)
        lines = []
        for paragraph in detail_lines(self.track, self.file_index):
            for line in paragraph.split('\n'):
                lines.extend(textwrap.wrap(line, max(1, width - split - 2), replace_whitespace=False) or [''])
        self.details_scroll = min(self.details_scroll, max(0, len(lines) - body_height))
        for offset, line in enumerate(lines[self.details_scroll:self.details_scroll + body_height]):
            put(screen, 4 + offset, split, line)
        state = dict(self.player.state)
        mode = 'Stopped' if state['idle'] else ('Paused' if state['paused'] else 'Playing')
        put(screen, height - 6, 0, f"{mode} {clock(state['position'])} / {clock(state['duration'])} | {state['path']}", curses.A_BOLD)
        put(screen, height - 5, 0, state['error'] or self.message)
        put(screen, height - 4, 0, 'Enter Play | F7 Pause | F8 Stop | Left/Right Seek 10s | Ctrl+B/F Seek 30s')
        put(screen, height - 3, 0, 'F3 Copy prompt | F4 Copy tags | F6 Audio version | PgUp/PgDn Details')
        put(screen, height - 2, 0, 'F2 Advanced | F5 Refresh | Up/Down Select | Esc Clear name | Ctrl+Q Quit')
        put(screen, height - 1, 0, 'F11/Ctrl+G Favorites | * Favorite | T Trimmed | F1 Help')
        screen.refresh()

    def run(self, screen):
        if curses.has_colors():
            # ANSI white (7) is usually light gray. Use exact RGB endpoints.
            black, white = (16, 231) if curses.COLORS >= 256 else (0, 15 if curses.COLORS >= 16 else 7)
            if curses.can_change_color():
                curses.init_color(black, 0, 0, 0)
                curses.init_color(white, 1000, 1000, 1000)
            curses.init_pair(1, black, white)
            screen.bkgd(' ', curses.color_pair(1))
            screen.attrset(curses.color_pair(1))
        curses.curs_set(0)
        curses.raw()  # Ctrl+C is Copy prompt; Ctrl+Q quits (disable terminal flow control).
        screen.keypad(True)
        screen.timeout(100)
        try:
            while True:
                self.poll()
                self.draw(screen)
                try:
                    key = screen.get_wch()
                except curses.error:
                    continue
                action = self.handle(key)
                if action == 'quit':
                    break
                if action == 'help':
                    show_help(screen)
                elif action == 'advanced':
                    result = advanced(screen, self.filters)
                    if result is not None:
                        self.filters = result
                        self.apply_search()
                elif action == 'categories':
                    category_screen(screen, self)
        finally:
            self.player.close()


def put(screen, y, x, text, attr=0, limit=None):
    height, width = screen.getmaxyx()
    if y >= height or x >= width:
        return
    # Strip control characters from imported prompts before displaying them.
    text = ''.join(char if char.isprintable() else ' ' for char in str(text))
    try:
        screen.addnstr(y, x, text, max(0, min(limit or width, width - x - 1)), attr)
    except curses.error:
        pass


def advanced(screen, current):
    values = {key: current.get(key, '') for key, _ in FILTERS}
    selected = 0
    message = 'Enter applies | Tab/Up/Down changes field | F4 clears all | Esc cancels'
    while True:
        screen.erase()
        put(screen, 0, 0, 'ADVANCED SEARCH', curses.A_BOLD)
        visible = max(1, (screen.getmaxyx()[0] - 5) // 2)
        start = max(0, selected - visible + 1)
        for index in range(start, min(len(FILTERS), start + visible)):
            key, label = FILTERS[index]
            put(screen, 2 + (index - start) * 2, 0, label + ': ' + values[key] + ('_' if selected == index else ''),
                curses.A_BOLD | curses.A_UNDERLINE if selected == index else 0)
        put(screen, screen.getmaxyx()[0] - 2, 0, message)
        screen.refresh()
        try:
            char = screen.get_wch()
        except curses.error:
            continue
        key = FILTERS[selected][0]
        if char == '\x1b':
            return None
        if char in ('\n', '\r', curses.KEY_ENTER):
            try:
                return validate_filters(values)
            except ValueError as exc:
                message = str(exc)
        elif char in ('\t', curses.KEY_DOWN):
            selected = (selected + 1) % len(FILTERS)
        elif char in (curses.KEY_BTAB, curses.KEY_UP):
            selected = (selected - 1) % len(FILTERS)
        elif char == curses.KEY_F4:
            values = {key: '' for key, _ in FILTERS}
        elif char == '\x15':
            values[key] = ''
        elif char in (curses.KEY_BACKSPACE, '\x7f', '\b'):
            values[key] = values[key][:-1]
        elif isinstance(char, str) and char.isprintable():
            values[key] += char


def text_dialog(screen, title, initial=''):
    value = initial
    while True:
        screen.erase()
        put(screen, 1, 0, title, curses.A_BOLD)
        width = max(1, screen.getmaxyx()[1] - 4)
        put(screen, 3, 0, '> ' + (value + '_')[-width:])
        put(screen, 5, 0, 'Enter accepts | Esc cancels | Ctrl+U clears | Backspace edits')
        screen.refresh()
        try:
            key = screen.get_wch()
        except curses.error:
            continue
        if key == '\x1b':
            return None
        if key in ('\n', '\r', curses.KEY_ENTER):
            return value
        if key == '\x15':
            value = ''
        elif key in (curses.KEY_BACKSPACE, '\x7f', '\b'):
            value = value[:-1]
        elif isinstance(key, str) and key.isprintable():
            value += key


def category_screen(screen, browser):
    selected = 0
    track = browser.track
    message = 'Space adds/removes the selected track. Tracks can belong to several categories.'
    while True:
        categories = list_categories(browser.db)
        selected = min(selected, max(0, len(categories) - 1))
        current = categories[selected] if categories else None
        screen.erase()
        put(screen, 0, 0, 'FAVORITE CATEGORIES', curses.A_BOLD)
        put(screen, 1, 0, 'Selected track: ' + (track['title'] if track else '(none)'))
        height = screen.getmaxyx()[0]
        visible = max(1, height - 8)
        start = max(0, selected - visible + 1)
        if not categories:
            put(screen, 3, 0, 'No categories yet. Press N to create one.')
        for index in range(start, min(len(categories), start + visible)):
            category = categories[index]
            member = track and category['name'] in track.get('categories', [])
            label = f"[{'x' if member else ' '}] {category['name']} ({category['count']} examples)"
            put(screen, 3 + index - start, 0, label, curses.A_BOLD | curses.A_UNDERLINE if index == selected else 0)
        put(screen, height - 4, 0, message)
        put(screen, height - 3, 0, 'N New category | Space Add/remove track | E Export category JSON')
        put(screen, height - 2, 0, 'Enter Browse category | A All tracks | Up/Down Select | Esc Back')
        screen.refresh()
        try:
            key = screen.get_wch()
        except curses.error:
            continue
        try:
            if key == '\x1b':
                return
            if key == curses.KEY_UP:
                selected = max(0, selected - 1)
            elif key == curses.KEY_DOWN:
                selected = min(max(0, len(categories) - 1), selected + 1)
            elif key in ('n', 'N'):
                name = text_dialog(screen, 'New favorite category')
                if name is not None:
                    category_id = create_category(name, browser.db)
                    selected = next(i for i, item in enumerate(list_categories(browser.db)) if item['id'] == category_id)
                    message = 'Category created. Press Space to add the selected track.'
            elif key == ' ' and current:
                if not track:
                    raise ValueError('Select a track in the browser first')
                added = toggle_favorite(current['id'], track, browser.db)
                memberships = track.setdefault('categories', [])
                if added:
                    memberships.append(current['name'])
                else:
                    memberships.remove(current['name'])
                message = ('Added to ' if added else 'Removed from ') + current['name']
                browser.tracks = load_tracks(browser.db)
                browser.apply_search(preserve=True)
            elif key in ('e', 'E') and current:
                slug = re.sub(r'[^\w.-]+', '_', current['name']).strip('._-') or 'favorites'
                destination = text_dialog(screen, 'Export prompt configs to JSON (existing files are not overwritten)',
                                          str(ROOT / 'examples' / 'favorites' / (slug + '.json')))
                if destination is not None:
                    if not destination.strip():
                        raise ValueError('Enter an export filename')
                    path, count = export_category(current['id'], destination, browser.db)
                    message = f'Exported {count} examples: {path}'
                    browser.message = message
            elif key in ('\n', '\r', curses.KEY_ENTER) and current:
                browser.name = ''
                browser.filters = {'category': current['name']}
                browser.tracks = load_tracks(browser.db)
                browser.apply_search()
                return
            elif key in ('a', 'A'):
                browser.name = ''
                browser.filters = {}
                browser.apply_search()
                return
        except (ValueError, OSError, sqlite3.Error) as exc:
            message = str(exc)


def show_help(screen):
    lines = [
        'TRACK LIBRARY — press Esc or Enter to return', '',
        'Typing searches song titles and IDs. All name words must match.',
        'Up/Down selects a track. Enter plays the selected version.',
        'F6 / Ctrl+O cycles original, named, and trimmed audio versions.',
        'Playback prefers an existing TRIM_ file, then a named file, then audio.flac.',
        'F7 / Ctrl+P pauses. F8 stops. Left/Right seeks 10 seconds.',
        'Ctrl+B / Ctrl+F seeks 30 seconds. Search never stops playback.', '',
        'F3 / Ctrl+C copies the complete source song JSON to your clipboard.',
        'F4 / Ctrl+T copies only the selected file hashtags: #metal #rock',
        'No tags are substituted from a different version.', '',
        'F11 / Ctrl+G opens favorite categories: N creates, Space adds/removes,',
        'E exports prompt-only JSON, Enter browses a category. * marks favorites.',
        'F2 / Ctrl+A opens advanced search. Blank fields mean any value.',
        'Tags match all entered hashtags across any file version of a track.',
        'Text searches style, lyrics, descriptions, genres and audio metrics.',
        'Dates use local completion date, falling back to the batch date.',
        'F5 / Ctrl+R rescans files and tags in the background.',
        'Updates from generation and TrackTags appear automatically.', '',
        'PgUp/PgDn scrolls details. Esc / Ctrl+U clears the name search.',
        'Ctrl+Q or F12 quits and stops playback.',
        'Clipboard uses the desktop clipboard, or OSC 52 on supporting terminals.',
    ]
    while True:
        screen.erase()
        for index, line in enumerate(lines):
            put(screen, index, 0, line)
        screen.refresh()
        try:
            key = screen.get_wch()
        except curses.error:
            continue
        if key in ('\x1b', '\n', '\r', curses.KEY_ENTER):
            return


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('name', nargs='?', default='', help='Initial song-name search')
    parser.add_argument('--refresh', action='store_true', help='Import all prompts, tags and file locations before opening')
    parser.add_argument('--index-only', action='store_true', help='Refresh the catalog and exit without opening the interface')
    parser.add_argument('--outputs', type=Path, default=ROOT / 'outputs', help='Output root (default: this project outputs)')
    parser.add_argument('--db', type=Path, help='Catalog path (default: OUTPUTS/track_catalog.sqlite3)')
    args = parser.parse_args(argv)
    outputs = args.outputs.expanduser().resolve()
    db = args.db.expanduser().resolve() if args.db else outputs / 'track_catalog.sqlite3'
    try:
        if not outputs.is_dir():
            parser.error(f'Output directory does not exist: {outputs}')
        if args.refresh or args.index_only or not initialized(db):
            count, warnings = sync_catalog(outputs, db)
            print(f'Catalog: {count} tracks imported into {db}')
            for warning in warnings:
                print('Warning: ' + warning, file=sys.stderr)
        if args.index_only:
            return 0
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            parser.error('Interactive mode requires a terminal; use --index-only to refresh without one')
        browser = Browser(db, outputs, args.name)
        try:
            curses.wrapper(browser.run)
        finally:
            browser.player.close()
    except (OSError, ValueError, sqlite3.Error) as exc:
        parser.exit(1, f'Error: {exc}\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
