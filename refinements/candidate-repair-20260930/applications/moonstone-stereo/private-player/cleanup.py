"""Coordinator cleanup from a preregistered receipt; no bus dependency.

Runtime signals use pidfds only. Importing or inspecting this module does not
open a pidfd, inspect processes, or emit signals.
"""
from pathlib import Path
import json,os,re,select,signal,stat
MPV=Path('/usr/bin/mpv')
PLUGIN_SHA256='4a9622b06dbc784e91a1c5f911c4e503980732ea2d633683b8bca4eb430e9fab'

def x_server(value):
    match=re.fullmatch(r'(?:unix/?)?:(\d+)(?:\.\d+)?',value or '')
    if not match:raise RuntimeError('Only an attested local X server is supported')
    return int(match.group(1))

def receipt(run,host_display):
    run=Path(run)
    if run.is_symlink() or not run.is_dir() or run.stat().st_uid!=os.getuid():raise RuntimeError('Cleanup run must be an owned regular directory')
    path=run/'owned-private-player-root.json'
    if path.is_symlink() or not path.is_file() or path.stat().st_uid!=os.getuid() or path.stat().st_nlink!=1:raise RuntimeError('Player cleanup receipt must be an owned unshared regular file')
    value=json.loads(path.read_text())
    if value.get('schema')!=1 or value.get('kind')!='owned-private-player' or value.get('uid')!=os.getuid():raise RuntimeError('Unknown player cleanup receipt identity')
    root=Path(value['private_root'])
    if not root.is_absolute() or root==Path('/tmp') or not root.resolve().is_relative_to(Path('/tmp')) or root.is_symlink():raise RuntimeError('Player cleanup root is outside the owned temporary namespace')
    if root.exists() and (not root.is_dir() or root.stat().st_uid!=os.getuid() or stat.S_IMODE(root.stat().st_mode)&0o077):raise RuntimeError('Existing player cleanup root is foreign or shared')
    directory=root/'player'
    required={'player_home':str(directory/'home'),'plugin_path':str(directory/'mpris.so'),'socket':str(directory/'mpv.sock'),'mpv_executable':str(MPV),'plugin_sha256':PLUGIN_SHA256}
    if any(value.get(key)!=expected for key,expected in required.items()):raise RuntimeError('Player cleanup path or plugin contract changed')
    expected_environment={'HOME':value['player_home'],'DISPLAY':value['display']}
    for key,part in [('XDG_CONFIG_HOME','config'),('XDG_DATA_HOME','data'),('XDG_CACHE_HOME','cache'),('XDG_STATE_HOME','state'),('XDG_RUNTIME_DIR','runtime')]:expected_environment[key]=str(Path(value['player_home'])/part)
    environment=value.get('environment')
    if (not isinstance(environment,dict) or set(environment)!=set(expected_environment)|{'DBUS_SESSION_BUS_ADDRESS'}
            or any(environment.get(key)!=expected for key,expected in expected_environment.items())
            or not isinstance(environment.get('DBUS_SESSION_BUS_ADDRESS'),str) or not environment['DBUS_SESSION_BUS_ADDRESS'].startswith('unix:') or ';' in environment['DBUS_SESSION_BUS_ADDRESS']):
        raise RuntimeError('Player cleanup environment attestation is incomplete')

    if x_server(value.get('display'))==x_server(host_display):raise RuntimeError('Refusing player cleanup on the host X server')
    pid,birth=value.get('pid'),value.get('birth')
    if (pid is None)!=(birth is None) or pid is not None and (type(pid) is not int or pid<=0 or not isinstance(birth,str) or not birth.isdigit()):raise RuntimeError('Malformed bound player PID/birth')
    return value

def identity(entry,value):
    bound=value.get('pid') is not None and int(entry.name)==value['pid']
    def mismatch(message):
        if bound:raise RuntimeError('Bound player '+message+'; no signals emitted')
        return None
    try:info=entry.stat()
    except FileNotFoundError:return None
    except OSError:
        if bound:raise
        return None
    if info.st_uid!=value['uid']:return mismatch('UID changed')
    try:executable=(entry/'exe').resolve(strict=True)
    except OSError as error:
        if not entry.exists():return None
        if bound:raise RuntimeError('Bound player executable is unreadable; cleanup unresolved') from error
        return None
    if executable!=MPV.resolve():return mismatch('executable changed')
    directory=Path(value['private_root'])/'player'
    required=[str(MPV),'--no-config','--load-scripts=no','--script='+value['plugin_path'],'--ao=null','--vo=null',
              '--vid=no','--audio-display=no','--force-window=no','--no-terminal','--idle=yes','--loop-playlist=inf',
              '--input-ipc-server='+value['socket'],str(directory/'owned-track-1.wav'),str(directory/'owned-track-2.wav')]
    try:arguments=[arg.decode() for arg in (entry/'cmdline').read_bytes().split(b'\0') if arg]
    except (OSError,ValueError) as error:
        if not entry.exists():return None
        if bound:raise RuntimeError('Bound player argv is unreadable; cleanup unresolved') from error
        return None
    if arguments!=required:return mismatch('argv changed')
    try:
        environment=dict(row.split(b'=',1) for row in (entry/'environ').read_bytes().split(b'\0') if b'=' in row)
        fields=(entry/'stat').read_text().rsplit(')',1)[1].split();birth=fields[19];pid=int(entry.name)
    except (OSError,ValueError,IndexError) as error:
        if not entry.exists():return None
        raise RuntimeError('Existing registered-root mpv evidence is unreadable; cleanup unresolved') from error
    if any(environment.get(key.encode())!=expected.encode() for key,expected in value['environment'].items()):return mismatch('private environment changed')
    if not birth.isdigit():raise RuntimeError('Owned mpv birth evidence is malformed')
    if bound and birth!=value['birth']:raise RuntimeError('Bound player PID birth changed; cleanup unresolved')
    if value.get('pid') is not None and not bound:return None
    return {'pid':pid,'birth':birth}

def wait_pidfd(fd,timeout):
    poll=select.poll();poll.register(fd,select.POLLIN);return bool(poll.poll(int(timeout*1000)))

def cleanup(run,host_display,*,proc_root=Path('/proc'),open_pidfd=None,send_pidfd=None,wait_fd=wait_pidfd,close_fd=os.close):
    value=receipt(run,host_display)
    open_pidfd=open_pidfd or getattr(os,'pidfd_open',None);send_pidfd=send_pidfd or getattr(signal,'pidfd_send_signal',None)
    if not open_pidfd or not send_pidfd:raise RuntimeError('Safe player cleanup requires pidfd support')
    candidates=[]
    entries=[Path(proc_root)/str(value['pid'])] if value.get('pid') is not None else Path(proc_root).iterdir()
    for entry in entries:
        if entry.name.isdigit():
            owned=identity(entry,value)
            if owned:candidates.append(owned)
    if len(candidates)>1:raise RuntimeError('Refusing ambiguous preregistered player children')
    result={'schema':1,'media_type':'actual','scope':'Preregistered exact owned HOME/DISPLAY/UID/mpv argv/PID birth; pidfd signals only; no bus dependency','matched':candidates,'actions':[],'remaining_owned_player':False}
    for owned in candidates:
        try:fd=open_pidfd(owned['pid'],0)
        except ProcessLookupError:continue
        try:
            if identity(Path(proc_root)/str(owned['pid']),value)!=owned:raise RuntimeError('Owned player identity changed around pidfd acquisition')
            send_pidfd(fd,signal.SIGTERM,None,0);result['actions'].append({'pid':owned['pid'],'birth':owned['birth'],'signal':'TERM','target':'pidfd'})
            if not wait_fd(fd,3):
                send_pidfd(fd,signal.SIGKILL,None,0);result['actions'].append({'pid':owned['pid'],'birth':owned['birth'],'signal':'KILL','target':'same pidfd'})
                if not wait_fd(fd,2):result['remaining_owned_player']=True
        finally:close_fd(fd)
    if result['remaining_owned_player']:raise RuntimeError('Owned player pidfd did not report exit')
    result['status']='cleaned';return result
