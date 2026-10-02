"""Warm and retain the invariant songwriting prefix in llama.cpp's live slot."""
import hashlib
import json
from pathlib import Path
import time
import urllib.request
from track_registry import atomic_json

BRIEF_HEADER = 'Write one complete original song JSON for this brief:\n'


def post(url, route, value, timeout=1800):
    request = urllib.request.Request(url+route, data=json.dumps(value).encode(),
                                     headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        result = json.load(response)
    if result.get('error'):
        raise RuntimeError(result['error'])
    return result


class PromptCache:
    def __init__(self, url, directory, runtime_signature):
        self.url, self.directory, self.runtime_signature = url, Path(directory), runtime_signature
        self.directory.mkdir(parents=True,exist_ok=True)
        self.key = None
        self.info = None

    def signature(self, payload):
        if not payload['messages'][-1]['content'].startswith(BRIEF_HEADER):
            raise RuntimeError('Unexpected brief header: cannot safely identify shared prompt prefix')
        return {'runtime':self.runtime_signature, 'messages':payload['messages'][:-1],
                'brief_header':BRIEF_HEADER, 'chat_template_kwargs':payload.get('chat_template_kwargs',{}),
                'cache_mode':'resident_native_checkpoints_v2'}

    def prepare(self, payload):
        signature = self.signature(payload)
        key = hashlib.sha256(json.dumps(signature,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        if self.key != key:
            print('Warming shared system prompt + three examples once for this resident server',flush=True)
            started=time.monotonic()
            warm={**payload,'stream':False,'max_tokens':1,'seed':0,
                  'messages':payload['messages'][:-1]+[{'role':'user','content':BRIEF_HEADER+'{"concept":"prefix cache warmup only"}'}]}
            result=post(self.url,'/v1/chat/completions',warm)
            self.info={'key':key,'mode':'resident_native_checkpoints','signature':signature,
                       'warmup_timings':result.get('timings',{}),'warmup_seconds':time.monotonic()-started}
            atomic_json(self.directory/f'prefix_{key}.json',self.info)
            self.key=key
            print(f'Shared prompt cache warmed: {self.info["warmup_timings"].get("prompt_n", "?")} tokens; {time.monotonic()-started:.1f}s',flush=True)
        # Do not restore a serialized slot between drafts: Gemma4 SWA needs the
        # internal rewind checkpoints, which the slot restore API discards.
        # Keeping the live slot lets llama.cpp select its correct user-boundary
        # checkpoint, remove previous generated text, and evaluate the new brief.
        print('Reusing resident shared prompt cache in slot 0',flush=True)
        return {'key':key,'mode':'resident_native_checkpoints'}
