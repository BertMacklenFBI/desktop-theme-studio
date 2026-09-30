#!/usr/bin/python3
"""Fresh application captures for a selected profile; no unrelated windows closed."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
from gi.repository import Gio
from Xlib import X,display

ROOT=Path(__file__).resolve().parent

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('profile');args=parser.parse_args()
    profile=json.loads((ROOT/'profiles.json').read_text())['profiles'][args.profile]
    if Gio.Settings.new('org.cinnamon.theme').get_string('name')!=profile['theme']:
        raise RuntimeError('Select the requested profile before fresh application checks')
    out=ROOT/'verification/applications'/args.profile/time.strftime('%Y%m%d-%H%M%S');out.mkdir(parents=True)
    report={'profile':args.profile,'scope':'fresh applications on the selected real desktop','run':str(out),'checks':[], 'skipped':[], 'screenshots':[]}
    x=display.Display();root=x.screen().root;active=root.get_full_property(x.intern_atom('_NET_ACTIVE_WINDOW'),X.AnyPropertyType)
    owned=[]
    def windows():
        ids=root.get_full_property(x.intern_atom('_NET_CLIENT_LIST'),X.AnyPropertyType)
        result=[]
        for wid in ids.value if ids else []:
            try:
                w=x.create_resource_object('window',int(wid));pid=w.get_full_property(x.intern_atom('_NET_WM_PID'),X.AnyPropertyType)
                title=w.get_full_property(x.intern_atom('_NET_WM_NAME'),x.intern_atom('UTF8_STRING'))
                result.append((int(wid),int(pid.value[0]) if pid else None,title.value.decode(errors='replace') if title else w.get_wm_name() or ''))
            except Exception:pass
        return result
    def wait_window(process,title=None,timeout=20,baseline=()):
        until=time.monotonic()+timeout
        while time.monotonic()<until:
            for wid,pid,name in windows():
                if wid not in baseline and ((title and title in name) or (not title and pid==process.pid)):return wid
            if process.poll() is not None:raise RuntimeError('Application exited before its window appeared')
            time.sleep(.2)
        raise RuntimeError('Application window timeout: '+str(title or process.pid))
    def capture(wid,name):
        subprocess.run(['wmctrl','-ia',hex(wid)],check=True,timeout=4);time.sleep(.7)
        current=root.get_full_property(x.intern_atom('_NET_ACTIVE_WINDOW'),X.AnyPropertyType)
        if not current or int(current.value[0])!=wid:raise RuntimeError('Test window did not receive focus')
        path=out/(name+'.png');subprocess.run(['scrot','-u','--overwrite',str(path)],check=True,timeout=8)
        report['screenshots'].append(str(path))
    def spawn(argv,name):
        log=(out/(name+'.log')).open('w');p=subprocess.Popen(argv,stdout=log,stderr=log,env={**os.environ,'GTK_THEME':profile['theme']})
        owned.append((p,log,None));return p
    def remember(p,wid):
        for index,(proc,log,old) in enumerate(owned):
            if proc is p:owned[index]=(proc,log,wid)
    def close(p,wid):
        if any(item[0]==wid for item in windows()):subprocess.run(['wmctrl','-ic',hex(wid)],check=False,timeout=5)
        try:p.wait(timeout=8)
        except subprocess.TimeoutExpired:
            # Only a process created by this verifier; never a pre-existing app.
            p.terminate();p.wait(timeout=5)
    def interrupted(signum,frame):raise RuntimeError('Application probe interrupted; restoring owned windows')
    signal.signal(signal.SIGTERM,interrupted)
    try:
        wrapper=re.search(r'^fastfetch\(\) \{.*?^\}',(Path.home()/'.bashrc').read_text(),re.M|re.S)
        if not wrapper:raise RuntimeError('Expected existing Fastfetch function')
        for terminal in ('gnome-terminal','kitty','konsole'):
            if not shutil.which(terminal):report['skipped'].append(terminal+' not installed');continue
            modes=('short','long') if terminal=='gnome-terminal' else ('short',)
            for mode in modes:
                ready=out/(terminal+'-'+mode+'.ready');script=out/(terminal+'-'+mode+'.bash')
                title='Ricing '+args.profile+' '+terminal+' '+mode+' '+out.name
                script.write_text('#!/bin/bash\nexport PATH="$HOME/.local/bin:$PATH"\n'+wrapper.group()+
                    '\nprintf "\\033]0;'+title+'\\007"\n'+
                    'fastfetch '+mode+'\nstatus=$?\nprintf "%s" "$status" > '+repr(str(ready))+'\nsleep 9\nexit "$status"\n')
                if terminal=='gnome-terminal':argv=[terminal,'--wait','--window','--title',title,'--geometry=124x38+120+100','--','/bin/bash',str(script)]
                elif terminal=='kitty':argv=[terminal,'--title',title,'-o','initial_window_width=140c','-o','initial_window_height=40c','/bin/bash',str(script)]
                else:argv=[terminal,'--separate','--hide-menubar','-e','/bin/bash',str(script)]
                baseline={item[0] for item in windows()}
                p=spawn(argv,terminal+'-'+mode);wid=wait_window(p,title=title,baseline=baseline);remember(p,wid)
                until=time.monotonic()+15
                while not ready.exists() and time.monotonic()<until:time.sleep(.2)
                if not ready.exists() or ready.read_text()!='0':raise RuntimeError(terminal+' Fastfetch '+mode+' failed')
                capture(wid,terminal+'-'+mode)
                report['checks'].append(terminal+' fresh window / Fastfetch '+mode)
                close(p,wid)
        # Parse real long output for required rows and the requested display text.
        modes=subprocess.run(['/usr/bin/python3',str(Path.home()/'.config/fastfetch/modes.py')],capture_output=True,text=True,check=True)
        config=out/'long.jsonc';config.write_text(modes.stdout)
        ff=subprocess.run([str(Path.home()/'.local/bin/fastfetch'),'--config',str(config),'--pipe','true'],capture_output=True,text=True,check=True,timeout=15)
        (out/'fastfetch-long.txt').write_text(ff.stdout)
        if '86.75-309' not in ff.stdout:raise RuntimeError('Fastfetch long IP display changed')
        report['checks'].append('Fastfetch long retains 86.75-309')
        if shutil.which('xed'):
            sample=out/'Ricing-appearance-check.py';sample.write_text('# '+profile['name']+' — read-only appearance sample\n\nfrom pathlib import Path\n\ndef desktop_theme(name: str) -> str:\n    """Keep controls readable and preserve music commands."""\n    return f"Theme: {name}"\n\nprint(desktop_theme("'+profile['name']+'"))\n')
            p=spawn(['xed','--standalone','--geometry=1000x650+160+100',str(sample)],'xed');wid=wait_window(p);remember(p,wid);time.sleep(1.2)
            capture(wid,'xed');report['checks'].append('Xed fresh syntax/document window')
            report['xed_scheme']=Gio.Settings.new('org.x.editor.preferences.editor').get_string('scheme');close(p,wid)
        else:report['skipped'].append('Xed not installed')
        report['status']='passed'
    except Exception as exc:report.update(status='failed',error=str(exc))
    finally:
        for p,log,wid in reversed(owned):
            try:
                if p.poll() is None and wid:close(p,wid)
                elif p.poll() is None:p.terminate();p.wait(timeout=5)
            except Exception as exc:report.setdefault('cleanup_errors',[]).append(str(exc))
            log.close()
        if active and active.value[0]:subprocess.run(['wmctrl','-ia',hex(int(active.value[0]))],check=False,timeout=5)
        x.close();(out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
        latest=ROOT/'verification/applications-latest'/f'{args.profile}.json';latest.parent.mkdir(exist_ok=True)
        latest.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2));return 0 if report['status']=='passed' and not report.get('cleanup_errors') else 1

if __name__=='__main__':raise SystemExit(main())
