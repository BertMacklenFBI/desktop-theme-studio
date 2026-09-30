#!/usr/bin/python3
"""Coordinator-run actual private player fixture. Staging never starts it."""
import argparse,array,hashlib,json,math,os,shutil,subprocess,time,wave
import sys
sys.dont_write_bytecode=True
from pathlib import Path
if __package__:
    from .common import PLUGIN_SHA256,MPV,binding,context,ipc,player_root,process_identity,write_json,safe_target
    from . import client
else:
    from common import PLUGIN_SHA256,MPV,binding,context,ipc,player_root,process_identity,write_json,safe_target
    import client

SOURCE=Path(__file__).resolve().parent

def make_track(path,frequency,duration=30):
    rate=22050;samples=array.array('h',(int(2000*math.sin(2*math.pi*frequency*i/rate)) for i in range(int(rate*duration))))
    if __import__('sys').byteorder!='little':samples.byteswap()
    with wave.open(str(path),'wb') as output:output.setparams((1,2,rate,0,'NONE','not compressed'));output.writeframes(samples.tobytes())

def prepare():
    root,environment=context();directory=player_root();directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    if (directory/'mpv.sock').exists() or (directory/'mpv.sock').is_symlink() or (directory/'binding.json').exists():raise RuntimeError('Refusing preparation over an existing socket or binding')
    for name in ['mpris.so','mpv-mpris-copyright','owned-track-1.wav','owned-track-2.wav','mpv.log','prepared.json','discovery.json','acceptance.json','cleanup.json','transport-actions.jsonl']:
        safe_target(directory/name)
    home=directory/'home'
    if home.is_symlink() or home.exists() and (not home.is_dir() or home.stat().st_uid!=os.getuid() or __import__('stat').S_IMODE(home.stat().st_mode)&0o077):raise RuntimeError('Player HOME is not owned and private')
    home.mkdir(mode=0o700,exist_ok=True)
    launch_environment={key:os.environ[key] for key in ['DISPLAY','DBUS_SESSION_BUS_ADDRESS','LANG','LC_ALL','TZ'] if key in os.environ}
    launch_environment.update(HOME=str(home.resolve()),PATH='/usr/bin:/bin')
    for key,relative in [('XDG_CONFIG_HOME','config'),('XDG_DATA_HOME','data'),('XDG_CACHE_HOME','cache'),('XDG_STATE_HOME','state'),('XDG_RUNTIME_DIR','runtime')]:
        target=home/relative
        if target.is_symlink() or target.exists() and (not target.is_dir() or target.stat().st_uid!=os.getuid() or __import__('stat').S_IMODE(target.stat().st_mode)&0o077):raise RuntimeError('Player XDG directory differs')
        target.mkdir(mode=0o700,exist_ok=True);launch_environment[key]=str(target.resolve())
    plugin=SOURCE/'vendor/mpris.so'
    if plugin.is_symlink() or hashlib.sha256(plugin.read_bytes()).hexdigest()!=PLUGIN_SHA256:raise RuntimeError('Pinned owned MPRIS plugin differs')
    shutil.copy2(plugin,safe_target(directory/'mpris.so'))
    shutil.copy2(SOURCE/'vendor/mpv-mpris-copyright',safe_target(directory/'mpv-mpris-copyright'))
    tracks=[]
    for i,hz in ((1,440),(2,523)):
        path=directory/('owned-track-'+str(i)+'.wav');make_track(path,hz);tracks.append(str(path.resolve()))
    result={'schema':1,'media_type':'actual','status':'prepared','runtime_acceptance':'unverified; no process started by prepare',
            'environment':environment,'plugin_sha256':PLUGIN_SHA256,'tracks':tracks,'track_sha256':{p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in tracks}}
    result['player_environment']=launch_environment;write_json(directory/'prepared.json',result);return result

def start():
    directory=player_root()
    if (directory/'binding.json').exists():binding();raise RuntimeError('An owned private player is already bound')
    prepared=prepare();socket=directory/'mpv.sock'
    if socket.exists():raise RuntimeError('Refusing an unbound preexisting private IPC socket')
    argv=[str(MPV),'--no-config','--load-scripts=no','--script='+str(directory/'mpris.so'),'--ao=null','--vo=null',
          '--vid=no','--audio-display=no','--force-window=no','--no-terminal','--idle=yes','--loop-playlist=inf',
          '--input-ipc-server='+str(socket),*prepared['tracks']]
    with safe_target(directory/'mpv.log').open('w') as log:process=subprocess.Popen(argv,env=prepared['player_environment'],stdout=log,stderr=log)
    try:
        deadline=time.monotonic()+10;last=None
        while True:
            if process.poll() is not None:raise RuntimeError('Owned mpv exited during startup; inspect mpv.log')
            try:
                identity=process_identity(process.pid);endpoint=client.discover(process.pid)
                if socket.exists():break
            except Exception as error:last=error
            if time.monotonic()>=deadline:raise RuntimeError('Owned MPRIS startup timed out: '+str(last))
            time.sleep(.1)
        value={'schema':1,'media_type':'actual','pid':process.pid,'identity':identity,'mpris':endpoint,'socket':str(socket.resolve()),
               'tracks':prepared['tracks'],'plugin_sha256':PLUGIN_SHA256,'argv':argv}
        write_json(directory/'binding.json',value)
        deadline=time.monotonic()+8
        while True:
            try:observed=client.snapshot();break
            except Exception:
                if time.monotonic()>=deadline:raise
                time.sleep(.1)
        write_json(directory/'discovery.json',observed);return observed
    except BaseException as startup_error:
        if process.poll() is None:process.terminate()
        try:process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            try:process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                raise RuntimeError('Owned player startup failed: '+str(startup_error)+'; cleanup incomplete after bounded TERM/KILL waits') from startup_error
        (directory/'binding.json').unlink(missing_ok=True);raise

def stop():
    value=binding();before=value['identity'];ipc(value['socket'],['quit']);deadline=time.monotonic()+3
    while time.monotonic()<deadline:
        process_path=Path('/proc')/str(value['pid'])
        try:process_path.stat()
        except FileNotFoundError:break
        identity=process_identity(value['pid'])
        if identity!=before:raise RuntimeError('Refusing stop after owned PID birth changed')
        time.sleep(.1)
    else:
        raise RuntimeError('Owned player did not exit; numeric PID escalation refused; coordinator must reap its owned child')
    result={'schema':1,'media_type':'actual','status':'stopped','pid':value['pid'],'birth':before['birth'],'remaining_owned_player':False}
    write_json(player_root()/'cleanup.json',result);(player_root()/'binding.json').unlink();return result

def verify():
    receipts=[client.action('pause'),client.action('play'),client.action('seek',35),client.action('next'),client.action('previous')]
    capture_pause=client.action('pause');observed=client.snapshot()
    if observed['paused'] is not True or observed['playback_status']!='Paused':
        raise RuntimeError('Owned player capture state is not paused in matching MPRIS/IPC readback')
    result={'schema':1,'media_type':'actual','status':'passed','scope':'Owned player MPRIS discovery and endpoint transport with mpv IPC readback; physical Eww UI clicks remain separate',
            'discovery':observed,'actions':receipts,'final_state':'paused',
            'capture_stability':{'status':'passed','media_type':'actual','scope':'Actual MPRIS pause and matching mpv IPC readback after all original transport actions',
                                 'pause':capture_pause,'readback':observed}}
    write_json(player_root()/'acceptance.json',result);return result

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('command',choices=['prepare','start','status','action','verify','stop']);parser.add_argument('action',nargs='?');parser.add_argument('argument',nargs='?');args=parser.parse_args()
    if args.command=='action':result=client.action(args.action,args.argument)
    else:result={'prepare':prepare,'start':start,'status':client.snapshot,'verify':verify,'stop':stop}[args.command]()
    print(json.dumps(result,indent=2))
if __name__=='__main__':main()
