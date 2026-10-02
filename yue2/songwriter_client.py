"""Gemma chat client with complete request/response provenance and live token logs."""
import json
from pathlib import Path
import time
import urllib.request
from track_registry import atomic_json
from gemma_prompt_cache import BRIEF_HEADER
from songwriter_policy import CREATIVE_SCHEMA, PATCH_SCHEMA

ASSETS = Path(__file__).resolve().parent / 'songwriter'
SAMPLING = {'temperature': 1.0, 'top_p': .95, 'top_k': 64, 'min_p': 0.0,
            'repeat_penalty': 1.0, 'dry_multiplier': 0.0, 'max_tokens': 3500}


def request_song(url, idea, track_id, generation_seed, audio_seed, output, variation=0, avoid=None,
                 assets=ASSETS, feedback='', template='bone_and_iron_v3', prompt_cache=None,
                 writer_version=1, patch=None):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    examples = json.loads((assets / 'examples.json').read_text())['songs']
    messages = [{'role': 'system', 'content': (assets / 'system_prompt.txt').read_text()}]
    for example in examples:
        messages += [{'role': 'user', 'content': 'Reference only. Learn this song\'s pacing, cues and JSON structure; its sung words are off-limits.'},
                     {'role': 'assistant', 'content': json.dumps(example, ensure_ascii=False)}]
    angles = ['Make a costly rescue and an intimate aftermath.',
              'Start just after a small ordinary pleasure is interrupted; make the final choice surprising.',
              'Use one mistaken sensory inference that she has to correct before the climax.',
              'Focus on a promise to somebody less powerful and the physical price of keeping it.',
              'Give the antagonist a clever trap that fails because she can change forms.',
              'Use a keepsake or domestic habit as a practical tool, not just a symbol.',
              'Make her pride cause a mistake, then show an earned repair rather than a generic victory.',
              'Let a private embarrassment turn into useful knowledge for saving somebody else.',
              'Give the conflict a narrow deadline; use specific sensory evidence and an unexpected final image.',
              'Make the quiet consequence matter as much as the fight; avoid a tidy invincible-hero ending.']
    task = {'concept': idea['concept'], 'creative_angle': angles[variation % len(angles)],
            'dominant_reference': template, 'id': track_id, 'seed': audio_seed,
            'cot': 'full', 'target_seconds': 360,
            'duration_validation': {'min_seconds': 250, 'max_seconds': 380},
            'length': 'Approximately 300–500 SUNG words, similar to the examples; directions do not count.',
            'newness': 'Distinct title, imagery, plot choices and chorus. Do not borrow example lyric phrases.',
            'must_include': 'End lyrics with [End]. Include (clear female phrase climbing into a soaring metal belt, ending in a short gritty yell) in the bridge or final chorus. Keep most verses brisk and intelligible; reserve sustained screams for the breakdown. Vary line lengths, syntax and rhyme instead of identical rhyming couplets throughout.',
            'avoid_prior_hooks': avoid or [], 'retry_feedback': feedback}
    if writer_version == 2:
        task = {k: v for k, v in task.items() if k not in
                {'id', 'seed', 'cot', 'target_seconds', 'duration_validation', 'avoid_prior_hooks'}}
        task.update(output='Return exactly title, style, lyrics as strings. The harness supplies all config metadata.',
                    situation=idea['situations'][variation % len(idea['situations'])],
                    prior_story_choices_to_differentiate=avoid or [])
        if patch is not None:
            task = {'mode': 'replace_only_selected_sung_lines', 'concept': idea['concept'],
                    'situation': idea['situations'][variation % len(idea['situations'])],
                    'song': patch['song'], 'selected_lines': patch['plan']['lines'],
                    'instructions': 'Replace ONLY these numbered lines with original sung words. Preserve their emotional meaning and cadence in context, use fresh imagery and wording. No new cues, no chorus rewrite. Return replacements, each with line and text. All other words remain fixed.'}
    messages.append({'role': 'user', 'content': BRIEF_HEADER + json.dumps(task, ensure_ascii=False)})
    payload = {'messages': messages, **SAMPLING, 'seed': generation_seed, 'stream': True,
               'chat_template_kwargs': {'enable_thinking': False},
               'response_format': {'type': 'json_object'}, 'cache_prompt': True, 'id_slot': 0}
    if writer_version == 2:
        schema = json.loads(json.dumps(PATCH_SCHEMA if patch else CREATIVE_SCHEMA))
        if patch:
            items = schema['properties']['replacements']
            items.update(minItems=len(patch['plan']['lines']), maxItems=len(patch['plan']['lines']))
            items['items']['properties']['line']['enum'] = [line['line'] for line in patch['plan']['lines']]
        payload['response_format'] = {'type': 'json_schema', 'json_schema': {
            'name': 'lyric_line_repair' if patch else 'song_creative_fields', 'strict': True,
            'schema': schema}}
    if patch:
        payload['max_tokens'] = 512
    atomic_json(output / 'request.json', payload)
    cache_info = prompt_cache.prepare(payload) if prompt_cache else None
    request = urllib.request.Request(url + '/v1/chat/completions', data=json.dumps(payload).encode(),
                                     headers={'Content-Type': 'application/json'})
    chunks, events = [], []
    started = time.monotonic()
    finish = None
    timings = {}
    with urllib.request.urlopen(request, timeout=1800) as response, (output / 'response.txt').open('w') as stream:
        for line in response:
            if not line.startswith(b'data:'):
                continue
            raw = line[5:].strip()
            if raw == b'[DONE]':
                break
            event = json.loads(raw)
            if event.get('timings'):
                timings = event['timings']
            events.append(event)
            if event.get('error'):
                raise RuntimeError(event['error'])
            for choice in event.get('choices', []):
                text = choice.get('delta', {}).get('content') or ''
                if text:
                    chunks.append(text); stream.write(text); stream.flush()
                    if len(chunks) % 100 == 0:
                        print(f'{track_id}: {len(chunks)} output chunks; {time.monotonic()-started:.0f}s', flush=True)
                if choice.get('finish_reason'):
                    finish = choice['finish_reason']
    atomic_json(output / 'response_events.json', events)
    atomic_json(output / 'generation.json', {'generation_seed': generation_seed, 'audio_seed': audio_seed,
        'sampling': {k:payload[k] for k in SAMPLING}, 'seconds': time.monotonic() - started, 'finish_reason': finish,
        'writer_version': writer_version, 'request_kind': 'line_repair' if patch else 'full_song',
        'idea_id': idea['id'], 'variation': variation, 'dominant_reference': template,
        'prompt_cache':cache_info, 'timings':timings})
    if timings:
        print(f'{track_id}: prefix cached {timings.get("cache_n", "?")} tokens; evaluated {timings.get("prompt_n", "?")} new tokens; prompt {timings.get("prompt_ms", 0)/1000:.1f}s',flush=True)
    if finish != 'stop':
        raise ValueError(f'Generation was not complete: finish_reason={finish!r}')
    return ''.join(chunks)
