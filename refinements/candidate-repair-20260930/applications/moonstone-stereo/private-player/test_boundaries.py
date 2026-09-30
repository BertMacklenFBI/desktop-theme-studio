#!/usr/bin/python3
"""Tripwired ownership and cleanup models; no real socket, signal or process."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import ast,io,json,os,stat,struct,subprocess,sys,tempfile
sys.path.insert(0,str(Path(__file__).resolve().parent))
import common,client,launch

def refuses(function):
    try:function();raise AssertionError('Expected ownership refusal')
    except RuntimeError:return

def main():
    checks=[]
    with tempfile.TemporaryDirectory(prefix='dts-player-boundary-') as temporary:
        root=Path(temporary);home=root/'receiver-home';home.mkdir(mode=0o700)
        environment=dict(os.environ,DTS_PRIVATE_ROOT=str(root),HOME=str(home),DISPLAY=':9876',DBUS_SESSION_BUS_ADDRESS='unix:abstract=private-test',CURRENT_COLLECTION_HOST_DISPLAY=':0',CURRENT_COLLECTION_HOST_BUS='unix:abstract=host-test',SECRET_MARKER='do-not-copy')
        def attest(endpoint,*,attest_environment=True):return {'pid':111 if endpoint['name']=='private-test' else 222,'uid':os.getuid(),'birth':'1234'},{b'HOME':str(home).encode(),b'DISPLAY':b':9876'}
        observed=[]
        class BusConnection:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def settimeout(self,*args):pass
            def connect(self,address):self.private=address=='\0private-test'
            def getsockopt(self,*args):return struct.pack('3i',111 if self.private else 222,os.getuid(),os.getgid())
        def proc_environment(pid):
            observed.append(['environment',pid])
            if pid==222:raise PermissionError('host bus environment unreadable')
            return '1234',{b'HOME':str(home).encode(),b'DISPLAY':b':9876'}
        def proc_birth(pid):observed.append(['birth',pid]);return '1234'
        with patch.dict(os.environ,environment,clear=True),patch.object(common.socket,'socket',return_value=BusConnection()),patch.object(common,'proc_environment',side_effect=proc_environment),patch.object(common,'proc_birth',side_effect=proc_birth):
            assert common.context()[0]==root
            assert observed==[['environment',111],['birth',222]]
            with patch.object(common,'peer_identity',return_value=(111,os.getuid(),os.getgid())):refuses(common.context)
            with patch.object(common,'proc_environment',side_effect=PermissionError('private environ unreadable')):
                try:common.context();raise AssertionError('Unreadable private environment accepted')
                except PermissionError:pass
            with patch.object(common,'proc_environment',return_value=('1234',{})):refuses(common.context)
        checks.append('unreadable host environment skipped; private HOME/display still mandatory; same peer/private failure refused')
        with patch.dict(os.environ,environment,clear=True),patch.object(common,'bus_attestation',side_effect=attest):
            assert common.context()[0]==root
            for change in [{'HOME':'/foreign/home'},{'DISPLAY':':0.0'},{'DBUS_SESSION_BUS_ADDRESS':'unix:abstract=host-test,guid=changed'}]:
                with patch.dict(os.environ,change):refuses(common.context)
            checks.append('arbitrary HOME and host X/bus aliases refused')
            foreign=root/'foreign';foreign.mkdir();(root/'player').symlink_to(foreign,target_is_directory=True);refuses(common.player_root);(root/'player').unlink();directory=root/'player';directory.mkdir(mode=0o700)
            (directory/'prepared.json').symlink_to(foreign/'destination');refuses(lambda:common.safe_target(directory/'prepared.json'));(directory/'prepared.json').unlink()
            checks.append('symlinked player directory and receipt destinations refused')
            (directory/'mpv.sock').write_text('unknown');refuses(launch.prepare);assert not (directory/'mpris.so').exists() and not (directory/'owned-track-1.wav').exists();(directory/'mpv.sock').unlink()
            checks.append('unknown preexisting socket refused before asset writes')
            prepared=launch.prepare();assert prepared['player_environment']['HOME']==str(directory/'home');assert 'SECRET_MARKER' not in prepared['player_environment'];assert (directory/'mpv-mpris-copyright').is_file()
            for key,part in [('XDG_CONFIG_HOME','config'),('XDG_DATA_HOME','data'),('XDG_CACHE_HOME','cache'),('XDG_STATE_HOME','state'),('XDG_RUNTIME_DIR','runtime')]:assert prepared['player_environment'][key]==str(directory/'home'/part)
            checks.append('owned player HOME/XDG, environment allowlist and runtime notice copy')
            identity={'pid':4321,'birth':'1234'};bound={'pid':4321,'identity':identity,'socket':str(directory/'mpv.sock')}
            for case in ['safe','regular-file','foreign-uid','foreign-pid','changed-birth','symlink']:
                sent=[]
                class FakePath:
                    def __init__(self,value):self.value=str(value)
                    def __eq__(self,other):return self.value==other.value
                    def is_symlink(self):return case=='symlink'
                    def lstat(self):return SimpleNamespace(st_mode=stat.S_IFREG if case=='regular-file' else stat.S_IFSOCK,st_uid=os.getuid()+1 if case=='foreign-uid' else os.getuid())
                class Connection:
                    def __enter__(self):return self
                    def __exit__(self,*args):pass
                    def settimeout(self,*args):pass
                    def connect(self,*args):pass
                    def getsockopt(self,*args):return struct.pack('3i',9999 if case=='foreign-pid' else 4321,os.getuid(),os.getgid())
                    def sendall(self,message):sent.append(json.loads(message))
                    def makefile(self,*args):return io.BytesIO((json.dumps({'request_id':sent[0]['request_id'],'error':'success','data':True})+'\n').encode())
                with patch.object(common,'binding',return_value=bound),patch.object(common,'Path',FakePath),patch.object(common.socket,'socket',return_value=Connection()),patch.object(common,'process_identity',return_value={'pid':4321,'birth':'changed'} if case=='changed-birth' else identity):
                    if case=='safe':assert common.ipc(bound['socket'],['get_property','pause']) is True and sent
                    else:refuses(lambda:common.ipc(bound['socket'],['get_property','pause']));assert sent==[]
            checks.append('IPC Unix socket type/UID/symlink and peer PID/birth tripwires')
            events=[]
            class Process:
                pid=4321
                def poll(self):return None
                def terminate(self):events.append('terminate')
                def wait(self,timeout=None):events.append('wait');return 0
                def kill(self):events.append('kill')
            clock=iter([0,20,21,22])
            with patch.object(launch,'prepare',return_value=prepared),patch.object(launch,'process_identity',return_value=identity),patch.object(client,'discover',side_effect=RuntimeError('mock startup timeout')),patch.object(launch.subprocess,'Popen',return_value=Process()),patch.object(launch.time,'monotonic',side_effect=lambda:next(clock)),patch.object(launch.time,'sleep'),patch.object(launch,'safe_target',side_effect=lambda p:p):
                refuses(launch.start)
            assert events==['terminate','wait'];checks.append('startup timeout terminates/waits owned fake Popen')
            events.clear()
            class UnreapedProcess(Process):
                def wait(self,timeout=None):events.append('wait:'+str(timeout));raise subprocess.TimeoutExpired('mock child',timeout)
            clock=iter([0,20,21,22])
            with patch.object(launch,'prepare',return_value=prepared),patch.object(launch,'process_identity',return_value=identity),patch.object(client,'discover',side_effect=RuntimeError('mock startup timeout')),patch.object(launch.subprocess,'Popen',return_value=UnreapedProcess()),patch.object(launch.time,'monotonic',side_effect=lambda:next(clock)),patch.object(launch.time,'sleep'),patch.object(launch,'safe_target',side_effect=lambda p:p):
                try:launch.start();raise AssertionError('Unreaped fake child accepted')
                except RuntimeError as error:assert 'cleanup incomplete' in str(error) and 'startup' in str(error)
            assert events==['terminate','wait:3','kill','wait:3'];checks.append('startup TERM/KILL waits both bounded; original failure and incomplete cleanup retained')

            class ProcPath:
                def __init__(self,value):self.value=str(value)
                def __truediv__(self,part):return ProcPath(self.value+'/'+str(part))
                def stat(self):return SimpleNamespace(st_uid=os.getuid())
            with patch.object(launch,'Path',ProcPath),patch.object(launch,'binding',return_value=bound),patch.object(launch,'ipc',return_value=None),patch.object(launch,'process_identity',return_value={'pid':4321,'birth':'changed'}),patch.object(launch.os,'kill',side_effect=AssertionError('Real signal tripwire')):
                refuses(launch.stop)
            checks.append('stop refuses changed birth without numeric PID signaling')
            for error in [RuntimeError('bus changed'),PermissionError('proc denied'),FileNotFoundError('bus socket absent')]:
                (directory/'binding.json').write_text('retained')
                with patch.object(launch,'Path',ProcPath),patch.object(launch,'binding',return_value=bound),patch.object(launch,'ipc',return_value=None),patch.object(launch,'process_identity',side_effect=error):
                    try:launch.stop();raise AssertionError('Attestation failure emitted cleanup')
                    except type(error):pass
                assert (directory/'binding.json').read_text()=='retained' and not (directory/'cleanup.json').exists()
            (directory/'binding.json').unlink();checks.append('existing PID attestation failures retain binding and emit no false cleanup')
            eww=Path(__file__).resolve().parent.parent/'bundle-version2/private/launch.py';tree=ast.parse(eww.read_text());prepare=next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='prepare');guard_events=[]
            def refuse_guard():guard_events.append('attestation');raise RuntimeError('mock private identity refusal')
            namespace={'attest_private_session':refuse_guard,'root':lambda:(_ for _ in ()).throw(AssertionError('Write before guard'))};exec(compile(ast.Module(body=[prepare],type_ignores=[]),str(eww),'exec'),namespace);refuses(namespace['prepare']);assert guard_events==['attestation'];checks.append('version2 Eww guard runs before preparation writes')
    print(json.dumps({'schema':1,'scope':'Mocked process/bus/socket/signal boundaries and owned temp files only; not actual media acceptance','status':'passed','checks':checks},indent=2))
if __name__=='__main__':main()
