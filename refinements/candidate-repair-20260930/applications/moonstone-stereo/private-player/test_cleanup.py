#!/usr/bin/python3
"""Real owned fake proc files; pidfd/signal/poll are mocks only."""
from pathlib import Path
import json,os,sys,tempfile
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
import cleanup

def main():
    checks=[]
    with tempfile.TemporaryDirectory(prefix='dts-player-reaper-test-') as temporary:
        root=Path(temporary);run=root/'run';run.mkdir();proc=root/'proc';proc.mkdir();private=root/'private';private.mkdir(mode=0o700);directory=private/'player'
        value={'schema':1,'kind':'owned-private-player','private_root':str(private),'player_home':str(directory/'home'),'display':':9876','uid':os.getuid(),'mpv_executable':'/usr/bin/mpv','plugin_path':str(directory/'mpris.so'),'plugin_sha256':cleanup.PLUGIN_SHA256,'socket':str(directory/'mpv.sock'),'pid':None,'birth':None}
        value['environment']={'HOME':value['player_home'],'DISPLAY':value['display'],'DBUS_SESSION_BUS_ADDRESS':'unix:abstract=private-cleanup-test'}
        for key,part in [('XDG_CONFIG_HOME','config'),('XDG_DATA_HOME','data'),('XDG_CACHE_HOME','cache'),('XDG_STATE_HOME','state'),('XDG_RUNTIME_DIR','runtime')]:value['environment'][key]=str(Path(value['player_home'])/part)
        receipt=run/'owned-private-player-root.json';receipt.write_text(json.dumps(value))
        def add(pid,home=None,display=None,birth='1234',extra=None,environment_change=None):
            entry=proc/str(pid);entry.mkdir();(entry/'exe').symlink_to('/usr/bin/mpv');environment=dict(value['environment'],HOME=home or value['player_home'],DISPLAY=display or value['display']);environment.update(environment_change or {});(entry/'environ').write_bytes(b'\0'.join((key+'='+item).encode() for key,item in environment.items())+b'\0');args=['/usr/bin/mpv','--no-config','--load-scripts=no','--script='+value['plugin_path'],'--ao=null','--vo=null','--vid=no','--audio-display=no','--force-window=no','--no-terminal','--idle=yes','--loop-playlist=inf','--input-ipc-server='+value['socket'],str(directory/'owned-track-1.wav'),str(directory/'owned-track-2.wav')]+(extra or []);(entry/'cmdline').write_bytes(b'\0'.join(arg.encode() for arg in args)+b'\0');(entry/'stat').write_text(str(pid)+' (owned fixture) '+' '.join(['S']+['0']*18+[birth]));return entry
        owned=add(4321);add(4322,home='/foreign');add(4323,display=':0');add(4324,extra=['--ao=pulse']);add(4325,environment_change={'DBUS_SESSION_BUS_ADDRESS':'unix:abstract=foreign'});add(4326,environment_change={'XDG_RUNTIME_DIR':'/foreign/runtime'});events=[]
        def open_fd(pid,flags):events.append(['open',pid]);return 9999
        def send(fd,sig,*args):events.append(['signal',fd,int(sig)])
        def close(fd):events.append(['close',fd])
        result=cleanup.cleanup(run,':0',proc_root=proc,open_pidfd=open_fd,send_pidfd=send,wait_fd=lambda fd,timeout:True,close_fd=close);assert result['matched']==[{'pid':4321,'birth':'1234'}];assert [e for e in events if e[0]=='signal']==[['signal',9999,15]];checks.append('exact owned HOME/display/executable/argv only; TERM through pidfd')
        events.clear();polls=iter([False,True]);cleanup.cleanup(run,':0',proc_root=proc,open_pidfd=open_fd,send_pidfd=send,wait_fd=lambda fd,timeout:next(polls),close_fd=close);assert [e[2] for e in events if e[0]=='signal']==[15,9];checks.append('hung child KILL targets the same pidfd')
        events.clear()
        def reused(pid,flags):owned.joinpath('stat').write_text(str(pid)+' (new fixture) '+' '.join(['S']+['0']*18+['9999']));return 9999
        try:cleanup.cleanup(run,':0',proc_root=proc,open_pidfd=reused,send_pidfd=send,wait_fd=lambda fd,timeout:True,close_fd=close);raise AssertionError('Changed birth accepted')
        except RuntimeError:assert not any(e[0]=='signal' for e in events)
        checks.append('birth changed during pidfd acquisition refused without signals')
        try:cleanup.cleanup(run,':9876.0',proc_root=proc,open_pidfd=open_fd,send_pidfd=send,close_fd=close);raise AssertionError('Host alias cleanup accepted')
        except RuntimeError:pass
        checks.append('host X alias refused')
        read_bytes=Path.read_bytes
        def unreadable(path):
            if path.name=='environ' and path.parent.name=='4321':raise PermissionError('fake proc unreadable')
            return read_bytes(path)
        for bound in [False,True]:
            value['pid']=4321 if bound else None;value['birth']='9999' if bound else None;receipt.write_text(json.dumps(value));events.clear()
            with patch.object(Path,'read_bytes',new=unreadable):
                try:cleanup.cleanup(run,':0',proc_root=proc,open_pidfd=open_fd,send_pidfd=send,close_fd=close);raise AssertionError('Unreadable mpv evidence accepted')
                except RuntimeError:assert not any(event[0]=='signal' for event in events)
        checks.append('bound and preregistered unreadable same-UID mpv evidence fails closed')
        value['pid']=4325;value['birth']='1234';receipt.write_text(json.dumps(value));events.clear()
        try:cleanup.cleanup(run,':0',proc_root=proc,open_pidfd=open_fd,send_pidfd=send,close_fd=close);raise AssertionError('Bound foreign bus accepted')
        except RuntimeError:assert not any(event[0]=='signal' for event in events)
        checks.append('bound foreign bus/XDG environment refused; unbound foreign bus/XDG processes excluded')
        value['pid']=None;value['birth']=None;receipt.write_text(json.dumps(value))
        def unrelated_unreadable(path):
            if path.name=='environ' and path.parent.name=='4324':raise PermissionError('unrelated fake proc unreadable')
            return read_bytes(path)
        with patch.object(Path,'read_bytes',new=unrelated_unreadable):
            result=cleanup.cleanup(run,':0',proc_root=proc,open_pidfd=open_fd,send_pidfd=send,wait_fd=lambda fd,timeout:True,close_fd=close)
            assert result['matched']==[{'pid':4321,'birth':'9999'}]
        checks.append('unrelated mpv argv excluded before its unreadable environment')

        private.rmdir();result=cleanup.cleanup(run,':0',proc_root=proc,open_pidfd=open_fd,send_pidfd=send,wait_fd=lambda fd,timeout:True,close_fd=close);assert result['status']=='cleaned';checks.append('cleanup survives missing temp root and absent bus')
    print(json.dumps({'schema':1,'status':'passed','scope':'Owned fake proc files and mocked pidfd/signal/poll; no real process or socket start/signal','checks':checks},indent=2))
if __name__=='__main__':main()
