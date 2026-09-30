#!/usr/bin/env python3
"""Deterministic samples verify truthful low/high meter states, not fake animation."""
import importlib.util,json,tempfile,time,os
from pathlib import Path
spec=importlib.util.spec_from_file_location('audio_meter',Path(__file__).with_name('audio_meter.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
with tempfile.TemporaryDirectory(prefix='moonstone-audio-fixture-') as tmp:
    source=Path(tmp)/'cava-panel';now=time.time()
    def sample(text):
        source.write_text(json.dumps({'item':[{'type':'text','value':text}]}));os.utime(source,(now,now));return m.frame(source,now=now)
    idle=sample('▁'*14);assert idle['low']==0 and idle['high']==0 and idle['status']=='IDLE'
    low=sample('█'*7+'▁'*7);assert low['low']==100 and low['high']==0 and low['status']=='SIGNAL'
    high=sample('▁'*7+'█'*7);assert high['low']==0 and high['high']==100
    mid=sample('▄'*14);assert mid['low']==43 and mid['high']==43
    stale=m.frame(source,now=now+3);assert stale['status']=='UNAVAILABLE' and stale['low']==0
    bad=sample('not audio');assert bad['status']=='UNAVAILABLE'
    preview=m.frame('/nonexistent/source',preview=True);assert preview['source']=='preview-idle' and preview['low']==preview['high']==0
    for frame in [idle,low,high,mid,stale,bad,preview]:
        assert Path(frame['low_face']).is_file() and Path(frame['high_face']).is_file()
print('Audio fixtures pass: real spectrum low/high, zero idle, stale/invalid unavailable, deterministic preview, existing static assets.')
