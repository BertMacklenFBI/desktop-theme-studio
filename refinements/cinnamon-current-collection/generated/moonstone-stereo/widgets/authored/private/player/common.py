"""Attested private session and exact owned mpv IPC boundaries."""
from pathlib import Path
import hashlib,json,os,pwd,re,socket,stat,struct,time
from urllib.parse import unquote

PLUGIN_SHA256='4a9622b06dbc784e91a1c5f911c4e503980732ea2d633683b8bca4eb430e9fab'
MPV=Path('/usr/bin/mpv')

def x_server(value):
    match=re.fullmatch(r'(?:unix/?)?:(\d+)(?:\.\d+)?',value or '')
    if not match:raise RuntimeError('Only an attested local X server is supported')
    return int(match.group(1))

def bus_endpoint(value):
    if not value or ';' in value or not value.startswith('unix:'):raise RuntimeError('One explicit Unix D-Bus endpoint is required')
    fields={}
    for part in value[5:].split(','):
        key,sep,text=part.partition('=')
        if not sep or key in fields:raise RuntimeError('Malformed or duplicate D-Bus address fields')
        fields[key]=unquote(text)
    kinds=[key for key in ('path','abstract') if key in fields]
    if len(kinds)!=1:raise RuntimeError('Unknown Unix D-Bus endpoint layout')
    kind=kinds[0];name=fields[kind]
    if kind=='path':
        path=Path(name)
        if not path.is_absolute():raise RuntimeError('Private bus path must be absolute')
        path=path.resolve(strict=True);info=path.stat()
        if not stat.S_ISSOCK(info.st_mode):raise RuntimeError('D-Bus path is not a Unix socket')
        return {'kind':kind,'name':str(path),'device':info.st_dev,'inode':info.st_ino}
    if not name or '\0' in name:raise RuntimeError('Malformed abstract bus name')
    return {'kind':kind,'name':name}

def proc_birth(pid):
    path=Path('/proc')/str(pid)
    if path.stat().st_uid!=os.getuid():raise RuntimeError('Private process UID differs')
    fields=(path/'stat').read_text().rsplit(')',1)[1].split()
    return fields[19]

def proc_environment(pid):
    birth=proc_birth(pid);path=Path('/proc')/str(pid)
    environment=dict(row.split(b'=',1) for row in (path/'environ').read_bytes().split(b'\0') if b'=' in row)
    return birth,environment

def peer_identity(connection):
    return struct.unpack('3i',connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,struct.calcsize('3i')))

def bus_attestation(endpoint,*,attest_environment=True):
    if endpoint['kind']=='path' and Path(endpoint['name']).stat().st_uid!=os.getuid():raise RuntimeError('Bus socket ownership differs')
    address=endpoint['name'] if endpoint['kind']=='path' else '\0'+endpoint['name']
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as connection:
        connection.settimeout(2);connection.connect(address);pid,uid,_=peer_identity(connection)
    if uid!=os.getuid():raise RuntimeError('Private bus peer UID differs')
    if attest_environment:birth,environment=proc_environment(pid)
    else:birth,environment=proc_birth(pid),{}
    return {'pid':pid,'uid':uid,'birth':birth},environment

def context():
    value=os.environ.get('DTS_PRIVATE_ROOT','');root=Path(value)
    if not value or not root.is_absolute() or root.resolve()==Path('/tmp') or not root.resolve().is_relative_to(Path('/tmp')):
        raise RuntimeError('Actual player needs an owned temporary DTS_PRIVATE_ROOT')
    if not root.is_dir() or root.is_symlink() or root.stat().st_uid!=os.getuid() or stat.S_IMODE(root.stat().st_mode)&0o077:
        raise RuntimeError('Private root must be an owned owner-only directory')
    display=x_server(os.environ.get('DISPLAY',''));host_display=x_server(os.environ.get('CURRENT_COLLECTION_HOST_DISPLAY',''))
    if display==host_display:raise RuntimeError('Actual player X server is the host server')
    bus=bus_endpoint(os.environ.get('DBUS_SESSION_BUS_ADDRESS',''));host_value=os.environ.get('CURRENT_COLLECTION_HOST_BUS')
    if host_value is None:raise RuntimeError('Host bus attestation is missing')
    host=bus_endpoint(host_value) if host_value else None
    if bus==host:raise RuntimeError('Actual player bus endpoint is the host endpoint')
    peer,bus_environment=bus_attestation(bus)
    if host is not None:
        host_peer,_=bus_attestation(host,attest_environment=False)
        if peer['pid']==host_peer['pid'] and peer['birth']==host_peer['birth']:raise RuntimeError('Private and host endpoints have the same bus daemon')
    receiver_home=Path(bus_environment.get(b'HOME',b'').decode())
    if (not receiver_home.is_absolute() or receiver_home.resolve()==Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
            or x_server(bus_environment.get(b'DISPLAY',b'').decode())!=display):
        raise RuntimeError('Private bus process does not attest the receiver HOME/display')
    home=Path(os.environ.get('HOME',''));player_home=root.resolve()/'player/home'
    if not home.is_absolute() or home.resolve() not in (receiver_home.resolve(),player_home):raise RuntimeError('Caller HOME is not the attested receiver or owned player HOME')
    for path in (home,receiver_home):
        if not path.is_dir() or path.is_symlink() or path.stat().st_uid!=os.getuid() or stat.S_IMODE(path.stat().st_mode)&0o077:
            raise RuntimeError('Private HOME ownership/permissions differ')
    return root,{'receiver_home':str(receiver_home.resolve()),'player_home':str(player_home),'display_server':display,'bus_endpoint':bus,'bus_peer':peer,'uid':os.getuid()}

def player_root():
    path=context()[0]/'player'
    if path.exists() and (path.is_symlink() or not path.is_dir() or path.stat().st_uid!=os.getuid() or stat.S_IMODE(path.stat().st_mode)&0o077):
        raise RuntimeError('Player directory must be owned, private and regular')
    return path

def safe_target(path):
    root=player_root().resolve()
    if path.parent.resolve()!=root or path.is_symlink():raise RuntimeError('Player destination escapes its owned regular directory')
    if path.exists() and (not path.is_file() or path.stat().st_uid!=os.getuid() or path.stat().st_nlink!=1):
        raise RuntimeError('Player destination is not an owned unshared regular file')
    return path

def write_json(path,value):
    safe_target(path);temporary=path.with_suffix('.tmp');safe_target(temporary);temporary.write_text(json.dumps(value,indent=2)+'\n');temporary.replace(path)

def process_identity(pid):
    if type(pid) is not int or pid<=0:raise RuntimeError('Invalid owned player PID')
    _,owned=context();home=Path(owned['player_home'])
    if home.is_symlink() or not home.is_dir() or home.stat().st_uid!=os.getuid() or stat.S_IMODE(home.stat().st_mode)&0o077:raise RuntimeError('Owned player HOME changed')
    p=Path('/proc')/str(pid)
    if p.stat().st_uid!=owned['uid'] or (p/'exe').resolve()!=MPV.resolve():raise RuntimeError('Player PID is not the owned mpv executable')
    birth,env=proc_environment(pid)
    if (env.get(b'HOME')!=owned['player_home'].encode() or x_server(env.get(b'DISPLAY',b'').decode())!=owned['display_server']
            or bus_endpoint(env.get(b'DBUS_SESSION_BUS_ADDRESS',b'').decode())!=owned['bus_endpoint']):
        raise RuntimeError('Player PID belongs to another session or HOME')
    for key,relative in [('XDG_CONFIG_HOME','config'),('XDG_DATA_HOME','data'),('XDG_CACHE_HOME','cache'),('XDG_STATE_HOME','state'),('XDG_RUNTIME_DIR','runtime')]:
        if env.get(key.encode())!=str(Path(owned['player_home'])/relative).encode():raise RuntimeError('Player XDG environment differs')
    return {'pid':pid,'birth':birth,'environment':owned}

def binding():
    value=json.loads(safe_target(player_root()/'binding.json').read_text())
    if value.get('schema')!=1 or value.get('media_type')!='actual':raise RuntimeError('Unknown actual player binding')
    if process_identity(value['pid'])!=value['identity']:raise RuntimeError('Owned player PID birth/session changed')
    root=player_root().resolve()
    if Path(value['socket']).resolve()!=root/'mpv.sock':raise RuntimeError('IPC socket escapes the owned player root')
    tracks=[str((root/('owned-track-'+str(i)+'.wav')).resolve()) for i in (1,2)]
    if value.get('tracks')!=tracks:raise RuntimeError('Owned player track inventory changed')
    return value

def ipc(path,command):
    owned=binding();target=Path(path)
    if target!=Path(owned['socket']) or target.is_symlink():raise RuntimeError('IPC path differs from the bound owned socket')
    info=target.lstat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid!=os.getuid():raise RuntimeError('IPC needs an owned regular Unix socket endpoint')
    request=int(time.monotonic_ns());deadline=time.monotonic()+3
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as connection:
        connection.settimeout(3);connection.connect(str(path));pid,uid,_=peer_identity(connection)
        if pid!=owned['pid'] or uid!=os.getuid() or process_identity(pid)!=owned['identity']:raise RuntimeError('IPC peer is not the exact bound owned mpv PID/birth')
        connection.sendall((json.dumps({'command':command,'request_id':request})+'\n').encode())
        stream=connection.makefile('rb')
        while time.monotonic()<deadline:
            line=stream.readline()
            if not line:raise RuntimeError('Owned mpv IPC closed before its response')
            row=json.loads(line)
            if row.get('request_id')!=request:continue
            if row.get('error')!='success':raise RuntimeError('Owned mpv IPC rejected command: '+str(row.get('error')))
            return row.get('data')
    raise RuntimeError('Owned mpv IPC response timed out')
