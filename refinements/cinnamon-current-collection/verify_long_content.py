#!/usr/bin/python3
"""Render long synthetic content in disposable Eww configurations only."""
import importlib.util
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import time
from Xlib import X,display
from Xlib.ext import xtest

ROOT=Path(__file__).resolve().parent
EWW_ROOT=Path('/home/bertmacklen/Documents/eww-graphite-brass')
def module(name,path):
    s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
def main():
    profiles=json.loads((ROOT/'profiles.json').read_text())['profiles']
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--profiles',nargs='+',choices=list(profiles));args=parser.parse_args()
    if args.profiles:profiles={slug:profiles[slug] for slug in args.profiles}
    sync=module('long_sync',EWW_ROOT/'config/scripts/theme-sync.py')
    mock=module('long_mock',ROOT/'runtime-widgets/fixtures/mock_backend.py')
    probe=module('long_probe',ROOT/'runtime-widgets/live-probe.py')
    fixture=json.loads((ROOT/'runtime-widgets/fixtures/long-content-state.json').read_text())
    run=ROOT/'verification/long-content'/time.strftime('%Y%m%d-%H%M%S');run.mkdir(parents=True)
    report={'scope':'synthetic content; disposable configurations; all UI actions disabled','run':str(run),'profiles':[]}
    x=display.Display();xr=x.screen().root;pointer=xr.query_pointer()
    active=xr.get_full_property(x.intern_atom('_NET_ACTIVE_WINDOW'),X.AnyPropertyType)
    initial_focus=int(active.value[0]) if active is not None and len(active.value) else None
    def stop(*args):raise RuntimeError('Long-content fixture interrupted')
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    try:
        for slug,p in profiles.items():
            out=run/slug;config=out/'config';config.mkdir(parents=True)
            state=mock.fixture_state();state.update(fixture['system']);state['media'].update(fixture['media'])
            state['theme_label']='SYNTHETIC CONTENT / '+p['name'];state['notes']=state['notes']*6
            state['media']['cover']=str(EWW_ROOT/'config/assets/music.svg')
            prefix='fixture-long-'+slug+'-'
            for name in ('eww.yuck','saimoom.yuck','carbon.yuck'):
                raw=(EWW_ROOT/'config'/name).read_text()
                if name=='eww.yuck':raw='(defvar s '+json.dumps(json.dumps(state))+')\n'+raw.split('\n',1)[1]
                raw=re.sub(r':on[a-z]+\s+("(?:[^"\\]|\\.)*"|[A-Za-z_-]+)',lambda m:m.group().split()[0]+' "true"',raw)
                raw=re.sub(r'\(defwindow\s+(\w+)',lambda m:'(defwindow '+prefix+m.group(1),raw)
                if ':on' in raw:
                    assert all(value=='"true"' for value in re.findall(r':on[a-z]+\s+("(?:[^"\\]|\\.)*"|[A-Za-z_-]+)',raw))
                assert 'deflisten' not in raw and 'defpoll' not in raw
                (config/name).write_text(raw)
            raw=(EWW_ROOT/'config/eww.scss').read_text();a=raw.index(sync.START);b=raw.index(sync.END,a)+len(sync.END)
            (config/'eww.scss').write_text(raw[:a]+sync.render_variables(sync.palettes()[p['theme']])+raw[b:])
            log=(out/'daemon.log').open('w');env=dict(os.environ);events=out/'events.jsonl'
            cmd=[str(EWW_ROOT/'bin/eww'),'-c',str(config),'--no-daemonize']
            daemon=subprocess.Popen([*cmd,'daemon'],stdout=log,stderr=log,env=env)
            record={'profile':slug,'screenshots':[],'status':'failed'}
            try:
                for _ in range(25):
                    if subprocess.run([*cmd,'ping'],capture_output=True,timeout=3).returncode==0:break
                    time.sleep(.1)
                else:raise RuntimeError('Owned Eww daemon did not become ready')
                for name in ('music','controls','notes'):
                    definition=prefix+name
                    subprocess.run([*cmd,'open',definition],check=True,capture_output=True,timeout=8);time.sleep(.45)
                    xid=probe.find_eww_window(definition,event_log=events,env=env)
                    record['screenshots'].append(probe.capture(xid,out/(name+'.png'),event_log=events,env=env))
                    if not record['screenshots'][-1]['ok']:raise RuntimeError('Owned widget capture failed')
                    subprocess.run([*cmd,'close',definition],check=True,capture_output=True,timeout=8)
                record['status']='passed'
            except Exception as exc:record['error']=str(exc)
            finally:
                subprocess.run([*cmd,'kill'],capture_output=True,timeout=8)
                try:daemon.wait(timeout=3)
                except subprocess.TimeoutExpired:daemon.terminate();daemon.wait(timeout=3)
                log.close();report['profiles'].append(record)
                (out/'report.json').write_text(json.dumps(record,indent=2)+'\n')
            print(slug+': '+record['status'],flush=True)
            if record['status']!='passed':break
    finally:
        if initial_focus:subprocess.run(['wmctrl','-ia',hex(initial_focus)],check=False,timeout=3)
        xtest.fake_input(x,X.MotionNotify,x=pointer.root_x,y=pointer.root_y);x.sync();x.close()
        report['status']='passed' if len(report['profiles'])==len(profiles) and all(r['status']=='passed' for r in report['profiles']) else 'failed'
        (run/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'status':report['status'],'report':str(run/'report.json')}))
    return 0 if report['status']=='passed' else 1

if __name__=='__main__':raise SystemExit(main())
