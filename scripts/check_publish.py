#!/usr/bin/env python3
"""Check tracked source for credentials, runtime artifacts and accidental model/media commits."""
from pathlib import Path
import re
import subprocess
import sys
ROOT=Path(__file__).resolve().parent.parent
SECRET_PATTERNS={
    'GitHub token':re.compile(rb'gh[pousr]_[A-Za-z0-9]{25,}'),
    'Hugging Face token':re.compile(rb'hf_[A-Za-z0-9]{25,}'),
    'Google API key':re.compile(rb'AIza[A-Za-z0-9_-]{30,}'),
    'Google refresh token':re.compile(rb'1//[A-Za-z0-9_-]{30,}'),
    'private key':re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
}
BINARY={'.gguf','.safetensors','.ckpt','.pt','.pth','.pb','.bin','.onnx','.whl',
        '.flac','.ogg','.wav','.mp3','.mp4','.avi','.mkv','.webm','.mov','.sqlite3'}
RUNTIME={'queue','outputs','output','runs','local','.venv','models','llama.cpp','whisper.cpp'}

def inspect(path,relative):
    errors=[]
    if any(p in RUNTIME for p in relative.parts) and str(relative)!='yue2/outputs/trackTags.py':
        errors.append('runtime/model directory')
    if path.suffix.lower() in BINARY or path.name.endswith(('.log','.pid','.partial')):
        errors.append('runtime/model/media artifact')
    if path.stat().st_size>10*1024*1024:errors.append('file exceeds 10 MiB')
    raw=path.read_bytes()
    errors.extend(label for label,pattern in SECRET_PATTERNS.items() if pattern.search(raw))
    return errors


def main():
    result=subprocess.run(['git','ls-files','-z'],cwd=ROOT,check=True,capture_output=True)
    names=[Path(n.decode()) for n in result.stdout.split(b'\0') if n]
    failures=[]
    for name in names:
        path=ROOT/name
        if path.is_file():failures.extend((name,rule) for rule in inspect(path,name))
    for name,rule in failures:print(f'{name}: {rule}',file=sys.stderr)
    if failures:return 1
    print(f'Publication check passed: {len(names)} tracked source files, no forbidden artifacts or matching credential patterns')
    return 0
if __name__=='__main__':raise SystemExit(main())
