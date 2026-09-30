#!/usr/bin/env python3
"""Cinnamon/X11 adapters for the Saimoom and CarbonMonoxide widget port."""
import datetime as dt
import json
import math
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess as sp
import sys
import time
import threading
import urllib.request
from urllib.parse import urlencode
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / 'state'
NOTES = ROOT / 'Notes.txt'
TIMER = STATE / 'timer.json'
SOCK = Path.home() / '.cache/mpv-vis.sock'
DEFAULT_COVER = str(ROOT / 'config/assets/music.svg')

def run(*args):
    try:
        p = sp.run(args, capture_output=True, text=True, timeout=2)
        return p.stdout.strip() if p.returncode == 0 else ''
    except (OSError, sp.TimeoutExpired):
        return ''

def read(path, default=''):
    try: return Path(path).read_text().strip()
    except OSError: return default

def load(path, default):
    try: return json.loads(Path(path).read_text())
    except (OSError, ValueError): return default

def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data))
    tmp.replace(path)

def ipc(command):
    if not SOCK.exists(): return None
    try:
        with socket.socket(socket.AF_UNIX) as s:
            s.settimeout(.25)
            s.connect(str(SOCK))
            s.sendall((json.dumps({'command': command, 'request_id': 91})+'\n').encode())
            data = b''
            while len(data) < 262144:
                chunk = s.recv(65536)
                if not chunk: return None
                data += chunk
                while b'\n' in data:
                    line, data = data.split(b'\n', 1)
                    res = json.loads(line)
                    if res.get('request_id') == 91:
                        return res.get('data') if res.get('error') == 'success' else None
    except (OSError, ValueError): pass
    return None

def media():
    base = dict(title='Nothing playing', artist='Open your music player', album='', cover=DEFAULT_COVER,
                playing=False, progress=0, duration=0, position=0, elapsed='0:00', total='0:00', player='')
    path = ipc(['get_property', 'path'])
    if path:
        meta = ipc(['get_property','metadata']) or {}
        base.update(title=meta.get('title') or meta.get('TITLE') or Path(path).stem,
                    artist=meta.get('artist') or meta.get('ARTIST') or 'mpv',
                    album=meta.get('album') or meta.get('ALBUM') or '',
                    playing=not ipc(['get_property','pause']),
                    position=ipc(['get_property','time-pos']) or 0,
                    duration=ipc(['get_property','duration']) or 0, player='mpv-ipc')
        for name in ('cover.jpg','folder.jpg','cover.png','Folder.jpg','folder.png'):
            art = Path(path).parent / name
            if art.is_file(): base['cover'] = str(art); break
    else:
        players = run('playerctl','-l').splitlines()
        players.sort(key=lambda p: (not p.startswith('mpv'), p.startswith('firefox')))
        if players:
            p = next((p for p in players if run('playerctl','-p',p,'status') == 'Playing'),players[0])
            fmt = '{{title}}\x1f{{artist}}\x1f{{album}}\x1f{{mpris:length}}\x1f{{mpris:artUrl}}'
            fields = run('playerctl','-p',p,'metadata','--format',fmt).split('\x1f')
            if len(fields) == 5:
                title,artist,album,length,art = fields
                base.update(title=title or 'Untitled',artist=artist or p,album=album,player=p,
                            playing=run('playerctl','-p',p,'status') == 'Playing')
                try: base['duration'] = float(length)/1000000
                except ValueError: pass
                try: base['position'] = float(run('playerctl','-p',p,'position'))
                except ValueError: pass
                if art.startswith('file://'):
                    local = Path(unquote(urlparse(art).path))
                    if local.is_file(): base['cover'] = str(local)
    base['progress'] = min(100,max(0,base['position']/base['duration']*100)) if base['duration'] else 0
    for source,dest in [('position','elapsed'),('duration','total')]:
        seconds = max(0,int(base[source])); base[dest] = f'{seconds//60}:{seconds%60:02}'
    return base

def timer_state():
    t = load(TIMER,{'minutes':25,'end':0})
    end = t.get('end',0)
    if end and end <= time.time():
        t['end'] = 0; save(TIMER,t)
        run('notify-send','Focus timer','Your focus session is complete.')
        end = 0
    sec = max(0,math.ceil(end-time.time())) if end else int(t['minutes'])*60
    return {'text':f'{sec//60:02}:{sec%60:02}', 'running':bool(end),'minutes':t['minutes']}

def weather_loop():
    while True:
        cfg=load(ROOT/'weather.json',{})
        try:
            params=dict(latitude=cfg['latitude'],longitude=cfg['longitude'],
                current='temperature_2m,weather_code',temperature_unit=cfg.get('units','celsius'))
            url='https://api.open-meteo.com/v1/forecast?'+urlencode(params)
            req=urllib.request.Request(url,headers={'User-Agent':'Graphite-Eww/1.0'})
            with urllib.request.urlopen(req,timeout=12) as response: data=json.load(response)
            current=data['current']; code=current['weather_code']
            labels={0:'Clear',1:'Mostly clear',2:'Partly cloudy',3:'Overcast',45:'Fog',48:'Fog',
                51:'Drizzle',53:'Drizzle',55:'Drizzle',61:'Rain',63:'Rain',65:'Heavy rain',
                71:'Snow',73:'Snow',75:'Heavy snow',80:'Showers',81:'Showers',82:'Heavy showers',
                95:'Thunderstorms',96:'Thunderstorms',99:'Thunderstorms'}
            save(STATE/'weather.json',dict(location=cfg.get('location_name','Weather'),
                temperature=current['temperature_2m'],unit=data['current_units']['temperature_2m'],
                label=labels.get(code,'Mixed conditions'),fetched_at=time.time()))
        except (OSError,ValueError,KeyError): pass
        time.sleep(1800)

class Collector:
    def __init__(self): self.previous = None; self.slow = {}; self.tick = 0
    def collect(self):
        now=dt.datetime.now()
        s=dict(clock=now.strftime('%I:%M').lstrip('0'),meridiem=now.strftime('%p'),
               date=now.strftime('%A, %B %-d'),year=now.year,day=now.day,
               month=now.month,hostname=socket.gethostname(),timer=timer_state())
        if self.tick%3 == 0:
            vals=list(map(int,read('/proc/stat').splitlines()[0].split()[1:9]))
            total,idle=sum(vals),vals[3]+vals[4]
            cpu=0
            if self.previous:
                delta=total-self.previous[0]
                if delta: cpu=100*(1-(idle-self.previous[1])/delta)
            self.previous=(total,idle)
            mem={k:int(v.split()[0]) for k,v in (l.split(':',1) for l in read('/proc/meminfo').splitlines())}
            used=mem['MemTotal']-mem['MemAvailable']
            disk=shutil.disk_usage(Path.home())
            bats=[p for p in Path('/sys/class/power_supply').glob('*') if read(p/'type')=='Battery']
            b=bats[0] if bats else None
            self.slow.update(cpu=round(cpu),memory=round(used/mem['MemTotal']*100),
                memory_text=f'{used/1048576:.1f} / {mem["MemTotal"]/1048576:.1f} GiB',
                disk=round(disk.used/disk.total*100),disk_text=f'{disk.free/2**30:.0f} GiB free',
                battery=int(read(b/'capacity','0')) if b else 0,
                battery_status=read(b/'status','Unknown') if b else 'No battery',
                battery_present=bool(b),uptime=f'{int(float(read("/proc/uptime","0 0").split()[0]))//3600}h uptime')
            for key,kind in [('volume','sink'),('mic','source')]:
                out=run('pactl',f'get-{kind}-volume',f'@DEFAULT_{kind.upper()}@')
                match=re.search(r'(\d+)%',out)
                self.slow[key]=min(100,int(match[1])) if match else 0
            self.slow['muted']=run('pactl','get-sink-mute','@DEFAULT_SINK@').endswith('yes')
            backlights=list(Path('/sys/class/backlight').glob('*'))
            bl=backlights[0] if backlights else None
            self.slow['brightness']=round(int(read(bl/'brightness','0'))/max(1,int(read(bl/'max_brightness','1')))*100) if bl else 0
            pct=run('busctl','--user','call','org.cinnamon.SettingsDaemon.Power','/org/cinnamon/SettingsDaemon/Power','org.cinnamon.SettingsDaemon.Power.Screen','GetPercentage')
            if re.fullmatch(r'u \d+',pct): self.slow['brightness']=min(100,int(pct.split()[1]))
            self.slow['brightness_present']=bool(bl)
            self.slow['notifications']=run('gsettings','get','org.cinnamon.desktop.notifications','display-notifications')=='true'
            theme = load(STATE / 'theme.json', {})
            self.slow['theme_label'] = theme.get('label', 'GRAPHITE / BRASS')
            self.slow['theme_short'] = theme.get('short', 'GB')
        if self.tick%15 == 0:
            con=run('nmcli','-t','-f','NAME','connection','show','--active').splitlines()
            self.slow['network']=next((c for c in con if c not in ('lo','docker0')),'Disconnected')
            self.slow['wifi']=run('nmcli','radio','wifi')=='enabled'
            w=load(STATE/'weather.json',load(Path.home()/'.local/share/mint-dashboard/weather-cache.json',{}))
            age=time.time()-w.get('fetched_at',0)
            self.slow['weather']=f'{w.get("temperature", "–")}{w.get("unit", "")} · {w.get("label", "")}' if w and age<21600 else 'Weather unavailable'
            self.slow['weather_place']=f'{w.get("location", "No cached forecast")}'+(' · outdated cache' if age>21600 and w else ' · cached' if age>3600 and w else '')
        s.update(self.slow)
        s['media']=media()
        s['notes']=read(NOTES,'No notes yet.')[:4000]
        desks=[]
        for line in run('wmctrl','-d').splitlines():
            f=line.split(None,9)
            if len(f)>=2: desks.append({'id':int(f[0]),'name':str(int(f[0])+1),'active':f[1]=='*'})
        s['workspaces']=desks
        self.tick+=1
        return s

def action(args):
    op=args[0]
    if op in ('volume','mic'):
        value=max(0,min(100,round(float(args[1])))); kind='sink' if op=='volume' else 'source'
        run('pactl',f'set-{kind}-volume',f'@DEFAULT_{kind.upper()}@',f'{value}%')
    elif op=='mute': run('pactl','set-sink-mute','@DEFAULT_SINK@','toggle')
    elif op=='mic-mute': run('pactl','set-source-mute','@DEFAULT_SOURCE@','toggle')
    elif op=='brightness':
        value=max(5,min(100,round(float(args[1]))))
        run('busctl','--user','call','org.cinnamon.SettingsDaemon.Power','/org/cinnamon/SettingsDaemon/Power',
            'org.cinnamon.SettingsDaemon.Power.Screen','SetPercentage','u',str(value))
    elif op=='workspace': run('wmctrl','-s',str(int(args[1])))
    elif op=='wifi': run('nmcli','radio','wifi','off' if run('nmcli','radio','wifi')=='enabled' else 'on')
    elif op=='dnd':
        val=run('gsettings','get','org.cinnamon.desktop.notifications','display-notifications')
        run('gsettings','set','org.cinnamon.desktop.notifications','display-notifications','false' if val=='true' else 'true')
    elif op=='media':
        verb=args[1]
        m=media()
        if m['player']=='mpv-ipc':
            commands={'toggle':['cycle','pause'],'next':['playlist-next'],'previous':['playlist-prev']}
            if verb=='seek' and m['duration']: ipc(['seek',float(args[2]),'absolute-percent'])
            elif verb in commands: ipc(commands[verb])
        elif m['player']:
            if verb=='seek' and m['duration']: run('playerctl','-p',m['player'],'position',str(m['duration']*float(args[2])/100))
            elif verb in ('toggle','next','previous'): run('playerctl','-p',m['player'],'play-pause' if verb=='toggle' else verb)
    elif op=='timer':
        t=load(TIMER,{'minutes':25,'end':0})
        if args[1]=='toggle': t['end']=0 if t.get('end',0)>time.time() else time.time()+t['minutes']*60
        elif args[1]=='reset': t['end']=0
        else: t['minutes']=max(5,min(180,t['minutes']+int(args[1]))); t['end']=0
        save(TIMER,t)
    else: raise SystemExit('Unknown action')

if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='action': action(sys.argv[2:])
    else:
        if load(ROOT/'weather.json',{}).get('online_enabled',False) and not (len(sys.argv)>1 and sys.argv[1]=='once'):
            threading.Thread(target=weather_loop,daemon=True).start()
        collector=Collector()
        while True:
            try: print(json.dumps(collector.collect(),ensure_ascii=False),flush=True)
            except BrokenPipeError: break
            if len(sys.argv)>1 and sys.argv[1]=='once': break
            time.sleep(2)
