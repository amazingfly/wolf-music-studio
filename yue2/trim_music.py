"""Local YAMNet music/noise classification for autoTrim's detached passages.

The pinned ONNX conversion is from Google's YAMNet (Apache 2.0), exported by
Audio Magic. Only model files are downloaded; audio never leaves this machine.
"""
from pathlib import Path
import csv
import hashlib
import json
import math
import os
import tempfile
import urllib.request

import numpy as np
from scipy.signal import resample_poly
import soundfile as sf

REPO='audiomagic/yamnet-onnx'
REVISION='f25b741c2f0bdc6d7e6db24b5fddda23347dbafd'
HASHES={'yamnet.onnx':'d3835ffbbd4a1bb3e777f0ca217b5007907f5171dd5d17c4236b95b2af8f908e',
        'yamnet_class_map.csv':'cdf24d193e196d9e95912a2667051ae203e92a2ba09449218ccb40ef787c6df2'}


def fetch(directory,download=True):
    directory.mkdir(parents=True,exist_ok=True)
    for name,expected in HASHES.items():
        path=directory/name
        if not path.exists():
            if not download:raise FileNotFoundError(f'{path}: model missing; allow download once')
            with tempfile.NamedTemporaryFile(dir=directory,suffix='.tmp',delete=False) as h:
                tmp=Path(h.name)
                try:
                    with urllib.request.urlopen(f'https://huggingface.co/{REPO}/resolve/{REVISION}/{name}',timeout=60) as r:
                        for b in iter(lambda:r.read(1024*1024),b''):h.write(b)
                except BaseException:tmp.unlink(missing_ok=True);raise
            try:
                if hashlib.sha256(tmp.read_bytes()).hexdigest()!=expected:raise ValueError(f'Model checksum mismatch: {name}')
                # Publication refuses a concurrent replacement of the same model.
                try:os.link(tmp,path)
                except FileExistsError:pass
            finally:tmp.unlink(missing_ok=True)
        if hashlib.sha256(path.read_bytes()).hexdigest()!=expected:raise ValueError(f'Model checksum mismatch: {path}')
    return directory


class MusicDetector:
    def __init__(self,directory=None,download=True):
        import onnxruntime as ort
        directory=fetch(Path(directory) if directory else Path(__file__).resolve().parent/'models/trim-yamnet',download)
        options=ort.SessionOptions();options.intra_op_num_threads=2;options.inter_op_num_threads=1
        self.session=ort.InferenceSession(str(directory/'yamnet.onnx'),sess_options=options,providers=['CPUExecutionProvider'])
        with (directory/'yamnet_class_map.csv').open(encoding='utf-8',newline='') as label_file:
            self.labels=[r['display_name'] for r in csv.DictReader(label_file)]
        self.music=self.labels.index('Music')
        self.noise=[self.labels.index(n) for n in ['Silence','Noise','Static','White noise','Hum','Buzz']]
        self.identity={'model':'Google YAMNet, ONNX conversion','repository':REPO,'revision':REVISION,
                       'sha256':HASHES['yamnet.onnx'],'provider':'CPUExecutionProvider',
                       'music_threshold':.45,'frame_seconds':.96,'hop_seconds':.48}

    def classify(self,path,start,end):
        """Count actual supported Music windows, excluding noise/silence dominance.

        No loudness normalisation: faint noise must not be amplified into something
        the classifier interprets as music. Highest-energy channel avoids stereo
        cancellation. Chunked inference bounds memory for arbitrary long input.
        """
        if end<=start:raise ValueError('Empty classifier interval')
        entries=[];totals=np.zeros(len(self.labels));rows=0
        with sf.SoundFile(path) as source:
            first=max(0,math.floor(start*source.samplerate));last=min(source.frames,math.ceil(end*source.samplerate))
            position=first
            while position<last:
                source.seek(position)
                # 24 s is exactly 50 model hops. 0.96 s overlap supplies the
                # analysis context for the final starts in each chunk.
                b=source.read(min(last-position,round(24.96*source.samplerate)),dtype='float32',always_2d=True)
                channel=int(np.argmax(np.mean(b*b,axis=0)))
                div=math.gcd(source.samplerate,16000)
                x=resample_poly(b[:,channel],16000//div,source.samplerate//div).astype(np.float32)
                scores=self.session.run(['output_0'],{'waveform':x})[0]
                if scores.ndim!=2 or scores.shape[1]!=len(self.labels) or not np.isfinite(scores).all():
                    raise ValueError('Invalid YAMNet scores')
                for i,score in enumerate(scores):
                    t=position/source.samplerate+i*.48
                    if i*.48>=24 or t>=end:break
                    e=min(end,t+.96)
                    noise=float(np.max(score[self.noise]));music=float(score[self.music])
                    entries.append({'start_seconds':t,'end_seconds':e,'music_score':music,'noise_score':noise,
                                    'music':bool(music>=.45 and music>noise)})
                    totals+=score;rows+=1
                position+=round(24*source.samplerate)
        spans=[]
        for r in entries:
            if not r['music']:continue
            a,b=r['start_seconds'],r['end_seconds']
            if spans and a<=spans[-1][1]:spans[-1][1]=max(b,spans[-1][1])
            else:spans.append([a,b])
        supported=sum(b-a for a,b in spans)
        mean=totals/max(rows,1);top=np.argsort(mean)[-5:][::-1]
        return {'start_seconds':start,'end_seconds':end,'confirmed_music_seconds':supported,
                'music_coverage':min(1.,supported/(end-start)),'mean_music_score':float(mean[self.music]),
                'top_classes':[{'label':self.labels[i],'score':float(mean[i])} for i in top],
                'windows':entries}
