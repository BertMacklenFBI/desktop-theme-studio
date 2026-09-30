#!/usr/bin/python3
"""Pure private Eww argv/timeout checks; never invokes a daemon/client."""
from pathlib import Path
import importlib.util,json,os,subprocess,sys,tempfile
from unittest.mock import patch
sys.dont_write_bytecode=True

def imported(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

def main():
    source=Path(__file__).resolve().parent.parent/'bundle-version2/private';sys.path.insert(0,str(source))
    launcher=imported('client_flag_fixture_launcher',source/'launch.py');ui=imported('client_flag_fixture_ui',source/'ui.py');checks=[]
    with tempfile.TemporaryDirectory(prefix='dts-client-flag-test-') as temporary:
        root=Path(temporary);config=root/'config';config.mkdir();binary=root/'owned-eww-fixture';binary.write_text('Mock only; not executable');calls=[]
        def ran(argv,**kwargs):calls.append((argv,kwargs));return subprocess.CompletedProcess(argv,0)
        with patch.dict(os.environ,{'DTS_EWW_BINARY':str(binary)}),patch.object(launcher,'prepare',return_value=config),patch.object(launcher,'actual_player_binding',return_value={}),patch.object(subprocess,'run',side_effect=ran),patch.object(subprocess,'Popen',side_effect=AssertionError('Process start tripwire')):
            with patch.object(sys,'argv',['private/launch.py','start']):launcher.main()
            assert len(calls)==2 and calls[0][0]==[str(binary),'-c',str(config),'daemon']
            assert calls[1][0]==[str(binary),'-c',str(config),'--no-daemonize','open','clock']
            checks.append('explicit first daemon unchanged; post-start clock client suppresses auto-daemon fallback')
            for command,window,last in [('open','dashboard',['open','dashboard']),('close','dashboard',['close','dashboard']),('status',None,['active-windows']),('stop',None,['kill'])]:
                calls.clear()
                with patch.object(sys,'argv',['private/launch.py',command]+([window] if window else [])):launcher.main()
                assert len(calls)==1 and calls[0][0]==[str(binary),'-c',str(config),'--no-daemonize',*last]
                assert calls[0][1]['check'] is True and calls[0][1]['timeout']==10
            checks.append('open/close/status/stop clients all use no-daemonize and retain checked bounded timeout')
            with patch.object(ui,'root',return_value=root):
                for arguments in [['dashboard'],['close','dashboard'],['hide']]:
                    calls.clear()
                    with patch.object(sys,'argv',['private/ui.py',*arguments]):ui.main()
                    assert len(calls)==1 and calls[0][0][:4]==[str(binary),'-c',str(config),'--no-daemonize']
                    assert calls[0][1]['check'] is True and calls[0][1]['timeout']==10
            checks.append('private UI open/close/hide use the same client suppression flag')
            calls.clear()
            def timed_out(argv,**kwargs):calls.append((argv,kwargs));raise subprocess.TimeoutExpired(argv,10)
            with patch.object(subprocess,'run',side_effect=timed_out),patch.object(sys,'argv',['private/launch.py','open','dashboard']):
                try:launcher.main();raise AssertionError('Client timeout accepted')
                except subprocess.TimeoutExpired:pass
            assert len(calls)==1 and 'daemon' not in calls[0][0] and '--no-daemonize' in calls[0][0]
            checks.append('client timeout propagates without retry, daemon spawn or socket replacement')
    print(json.dumps({'schema':1,'status':'passed','scope':'Imported private launcher/UI with mocked run, fake non-executable binary and process tripwire; no actual client/daemon actions','checks':checks},indent=2))

if __name__=='__main__':main()
