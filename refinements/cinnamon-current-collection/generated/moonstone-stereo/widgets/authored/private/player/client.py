"""Exact owned MPRIS endpoint actions with actual mpv IPC readback."""
import json,math,os,time
from pathlib import Path
from urllib.parse import unquote,urlparse
if __package__:
    from .common import binding,context,ipc,player_root,process_identity,safe_target
else:
    from common import binding,context,ipc,player_root,process_identity,safe_target

PLAYER='org.mpris.MediaPlayer2.Player'
OBJECT='/org/mpris/MediaPlayer2'

def connect():
    context()
    import dbus
    return dbus.bus.BusConnection(os.environ['DBUS_SESSION_BUS_ADDRESS'])

def owner_pid(bus,owner):
    import dbus
    daemon=dbus.Interface(bus.get_object('org.freedesktop.DBus','/org/freedesktop/DBus'),'org.freedesktop.DBus')
    return int(daemon.GetConnectionUnixProcessID(owner,timeout=3))

def discover(pid,bus=None):
    bus=bus or connect();found=[]
    for name in bus.list_names():
        name=str(name)
        if not name.startswith('org.mpris.MediaPlayer2.'):continue
        owner=str(bus.get_name_owner(name))
        if owner_pid(bus,owner)==pid:found.append({'name':name,'owner':owner,'owner_pid':pid})
    if not found:raise RuntimeError('No MPRIS endpoint belongs to the owned mpv PID')
    return sorted(found,key=lambda row:('.instance' not in row['name'],row['name']))[0]

def endpoint():
    value=binding();bus=connect();name=value['mpris']['name'];owner=str(bus.get_name_owner(name))
    if owner!=value['mpris']['owner'] or owner_pid(bus,owner)!=value['pid']:raise RuntimeError('Bound MPRIS name owner/PID changed')
    import dbus
    obj=bus.get_object(owner,OBJECT,follow_name_owner_changes=False)
    return value,dbus.Interface(obj,PLAYER),dbus.Interface(obj,'org.freedesktop.DBus.Properties')

def snapshot():
    value,_,properties=endpoint();props=properties.GetAll(PLAYER,timeout=3)
    current={key:ipc(value['socket'],['get_property',key]) for key in ('pause','time-pos','duration','path','playlist-pos')}
    path=str(Path(current['path']).resolve()) if current['path'] else ''
    if path not in value['tracks']:raise RuntimeError('Actual player decoded a file outside the owned track inventory')
    position=float(current['time-pos'] or 0);duration=float(current['duration'] or 0)
    if not math.isfinite(position) or not math.isfinite(duration) or duration<=0:raise RuntimeError('Actual player duration/position is unavailable')
    metadata=props.get('Metadata',{});track_id=str(metadata.get('mpris:trackid',''))
    if not track_id.startswith('/'):raise RuntimeError('Actual MPRIS track ID is unavailable')
    uri=urlparse(str(metadata.get('xesam:url','')))
    if uri.scheme!='file' or uri.netloc not in ('','localhost') or str(Path(unquote(uri.path)).resolve())!=path:
        raise RuntimeError('Actual MPRIS metadata does not match the owned decoded file')
    return {'schema':1,'media_type':'actual','status':'observed','pid':value['pid'],'birth':value['identity']['birth'],
            'mpris':value['mpris'],'playback_status':str(props['PlaybackStatus']),'track_id':track_id,
            'title':str(metadata.get('xesam:title') or Path(path).name),'path':path,'position':position,
            'duration':duration,'paused':bool(current['pause']),'playlist_pos':int(current['playlist-pos']),
            'mpris_position_us':int(properties.Get(PLAYER,'Position',timeout=3))}

def action(command,argument=None):
    before=snapshot();value,player,_=endpoint();expected=None;target=None
    if command in ('toggle','play','pause'):
        getattr(player,{'toggle':'PlayPause','play':'Play','pause':'Pause'}[command])(timeout=3)
        expected=not before['paused'] if command=='toggle' else command=='pause'
    elif command in ('next','previous'):
        getattr(player,'Next' if command=='next' else 'Previous')(timeout=3)
    elif command=='seek':
        percent=float(argument)
        if not math.isfinite(percent) or not 0<=percent<=100:raise RuntimeError('Private seek percent is outside0..100')
        target=min(before['duration']-.2,before['duration']*percent/100)
        import dbus
        player.SetPosition(dbus.ObjectPath(before['track_id']),dbus.Int64(int(target*1_000_000)),timeout=3)
    else:raise RuntimeError('Unknown actual private player action')
    deadline=time.monotonic()+4
    while True:
        try:after=snapshot()
        except (RuntimeError,OSError):
            if time.monotonic()>=deadline:raise
            time.sleep(.1);continue
        status_agrees=after['playback_status']==('Paused' if after['paused'] else 'Playing')
        changed=(after['paused']==expected if expected is not None else abs(after['position']-target)<1.5 if target is not None else after['path']!=before['path'])
        if changed and status_agrees:break
        if time.monotonic()>=deadline:raise RuntimeError('Owned MPRIS action did not match actual mpv IPC readback')
        time.sleep(.1)
    receipt={'schema':1,'media_type':'actual','action':command,'argument':argument,'transport':'exact owned MPRIS endpoint; actual mpv IPC readback','before':before,'after':after,'status':'passed'}
    with safe_target(player_root()/'transport-actions.jsonl').open('a') as stream:stream.write(json.dumps(receipt)+'\n')
    return receipt

def actual_media():
    s=snapshot();position=s['position'];duration=s['duration']
    def clock(seconds):return str(int(seconds)//60)+':'+str(int(seconds)%60).zfill(2)
    return {'title':s['title'],'artist':'Owned private mpv fixture','album':'Actual MPRIS/IPC transport',
            'cover':str(context()[0]/'config/assets/music.svg'),'playing':not s['paused'],'progress':position/duration*100,
            'duration':duration,'position':position,'elapsed':clock(position),'total':clock(duration),
            'player':s['mpris']['name'],'media_type':'actual','owned_pid':s['pid']}
