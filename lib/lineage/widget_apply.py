#!/usr/bin/python3
"""Narrow journaled widget appearance transaction; previews unless --commit.
No applet reload, Cinnamon restart or audio mutations. gTile reload is external.
"""
from pathlib import Path
import argparse,contextlib,datetime,hashlib,json,os,re,subprocess,sys,tempfile
HERE=LINEAGE_PRESET/'desktop'
class Conflict(RuntimeError):pass
def sha(data):return hashlib.sha256(data).hexdigest()
def now():return datetime.datetime.now().astimezone().isoformat()
def read_json(p):return json.loads(Path(p).read_text())
def get(data,keys):
 for k in keys:data=data[k]
 return data
def put(data,keys,value):
 for k in keys[:-1]:data=data[k]
 data[keys[-1]]=value
def atomic(path,data,expected=None):
 path=Path(path);old=path.read_bytes() if path.exists() else None
 if expected is not None and (old is None or sha(old)!=expected):raise Conflict('File changed before write: '+str(path))
 mode=path.stat().st_mode & 0o777 if path.exists() else 0o600
 fd,tmp=tempfile.mkstemp(prefix='.'+path.name+'.',dir=str(path.parent))
 try:
  with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
  os.chmod(tmp,mode)
  if expected is not None and sha(path.read_bytes())!=expected:raise Conflict('Concurrent update: '+str(path))
  os.replace(tmp,path)
  directory_fd=os.open(str(path.parent),os.O_RDONLY | os.O_DIRECTORY)
  try:os.fsync(directory_fd)
  finally:os.close(directory_fd)
 finally:
  if os.path.exists(tmp):os.unlink(tmp)
def save(path,state):atomic(path,(json.dumps(state,indent=2)+'\n').encode())
@contextlib.contextmanager
def lock(path):
 fd=os.open(str(path),os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
 try:
  os.write(fd,str(os.getpid()).encode());os.close(fd);yield
 finally:path.unlink(missing_ok=True)
def preconditions(entries):
 if not any(x['uuid']=='sound150@claudiux' and x['setting_key'].startswith('color') for x in entries):return {'sound150':'not changed'}
 p=next(x['path'] for x in entries if x['uuid']=='sound150@claudiux');d=read_json(p)
 import gi
 from gi.repository import Gio
 amplified=Gio.Settings.new('org.cinnamon.desktop.sound').get_boolean('allow-amplified-volume')
 maximum=float(d['maxVolume']['value']);shown=float(str(d['volume']['value']).rstrip('%'))
 result=subprocess.run(['pactl','get-sink-volume','@DEFAULT_SINK@'],capture_output=True,text=True,check=True)
 volumes=[float(x) for x in re.findall(r'(\d+(?:\.\d+)?)%',result.stdout)]
 if not volumes:raise Conflict('Cannot verify current PulseAudio sink volume before sound150 color callback.')
 if maximum>100 and not amplified:raise Conflict('sound150 color callback would enable amplified volume; preserved audio state requires stopping.')
 if shown>maximum or max(volumes)>maximum:raise Conflict('sound150 callback could clamp volume; stop without changing colors.')
 if not d['showalbum']['value'] and d['keepAlbumArtIcon']['value']:raise Conflict('sound150 callback would change album-art behavior.')
 return {'allow_amplified_volume':amplified,'max_volume':maximum,'serialized_volume':shown,'actual_default_sink_channel_percent':volumes,'showalbum':d['showalbum']['value'],'keepAlbumArtIcon':d['keepAlbumArtIcon']['value']}
def validate(plan):
 for e in plan['entries']:
  if e['keys']!=[e['setting_key'],'value']:raise Conflict('Unexpected key path')
  if e['setting_key'] in ['volume','maxVolume','refresh-ms','desklet-size']:raise Conflict('Behavioral field in palette plan')
  if not (str(e['after']).startswith('#') and re.fullmatch('#[0-9a-fA-F]{6}',e['after'])):raise Conflict('Non-color target')
  value=get(read_json(e['path']),e['keys'])
  if value not in [e['before'],e['after']]:raise Conflict('Color drift: '+e['path']+':'+e['setting_key'])
 for f in plan.get('file_actions',[]):
  if sha(Path(f['path']).read_bytes()) not in [f['before_sha256'],f['after_sha256']]:raise Conflict('Stylesheet drift: '+f['path'])
def refresh(entries,guard):
 # Cinnamon remoteUpdate reads the changed file; call once per spice after all
 # its keys have landed, avoiding repeated callbacks and preserving instance IDs.
 results=[];seen=set()
 for e in entries:
  ident=(e['uuid'],e['instance'])
  if ident in seen:continue
  seen.add(ident)
  if e['uuid']=='sound150@claudiux':preconditions(entries)
  try:
   p=subprocess.run(['gdbus','call','--session','--dest','org.Cinnamon','--object-path','/org/Cinnamon','--method','org.Cinnamon.updateSetting',e['uuid'],str(e['instance']),e['setting_key'],json.dumps(e['after'])],capture_output=True,text=True,timeout=8)
   results.append({'uuid':e['uuid'],'instance':e['instance'],'success':p.returncode==0,'output':(p.stdout+p.stderr).strip()[:500]})
  except (OSError,subprocess.TimeoutExpired) as err:results.append({'uuid':e['uuid'],'instance':e['instance'],'success':False,'output':str(err)})
 return results
def apply(plan,journal,commit):
 validate(plan)
 changed=[e for e in plan['entries'] if get(read_json(e['path']),e['keys'])!=e['after']]
 files=[f for f in plan.get('file_actions',[]) if sha(Path(f['path']).read_bytes())!=f['after_sha256']]
 guard=preconditions(changed)
 if not commit:return {'preview':True,'color_values':len(changed),'stylesheets':len(files),'audio_preconditions':guard,'would_refresh':sorted(set(e['uuid'] for e in changed))}
 if journal.exists() and read_json(journal).get('status')!='restored':raise Conflict('Existing transaction journal; check/restore it before a new apply: '+str(journal))
 state={'version':1,'created_at':now(),'status':'applying','entries':[],'files':[],'audio_preconditions':guard,'refresh':[],'reload_required':[]};save(journal,state)
 try:
  for path in dict.fromkeys(e['path'] for e in changed):
   group=[dict(e) for e in changed if e['path']==path]
   # Check callback preconditions again immediately before sound settings write.
   if group[0]['uuid']=='sound150@claudiux':preconditions(group)
   raw=Path(path).read_bytes();data=json.loads(raw)
   for e in group:
    value=get(data,e['keys'])
    if value!=e['before']:raise Conflict('Color changed since preview: '+path+':'+e['setting_key'])
    e['before']=value;e['phase']='pending';state['entries'].append(e)
    put(data,e['keys'],e['after'])
   save(journal,state) # All original color keys durably journaled BEFORE write.
   atomic(path,(json.dumps(data,indent=2)+'\n').encode(),sha(raw))
   for e in state['entries']:
    if e['path']==path:e['phase']='written'
   save(journal,state)
  for f in files:
   record={k:f[k] for k in ['path','before_text','after_text','before_sha256','after_sha256']};record['phase']='pending';state['files'].append(record);save(journal,state)
   atomic(f['path'],f['after_text'].encode(),f['before_sha256']);record['phase']='written';state['reload_required'].append(f['uuid']);save(journal,state)
  state['refresh']=refresh(changed,guard);state['status']='applied';state['completed_at']=now();save(journal,state)
 except Exception as err:
  state['status']='interrupted';state['error']=str(err);save(journal,state);raise
 return {'applied':True,'journal':str(journal),'color_values':len(changed),'stylesheets':len(files),'refresh':state['refresh'],'reload_required':state['reload_required'],'note':'No applet reload or stylesheet reload performed.'}
def restore(journal,commit):
 state=read_json(journal)
 if state.get('status')=='restored':return {'restored':True,'already_restored':True}
 entries=state['entries'];conflicts=[]
 for e in entries:
  current=get(read_json(e['path']),e['keys'])
  if current not in [e['before'],e['after']]:conflicts.append(e['path']+':'+e['setting_key'])
 for f in state['files']:
  if sha(Path(f['path']).read_bytes()) not in [f['before_sha256'],f['after_sha256']]:conflicts.append(f['path'])
 if conflicts:raise Conflict('Restore conflicts; no changes: '+', '.join(conflicts))
 reverting=[e for e in entries if get(read_json(e['path']),e['keys'])!=e['before']]
 guard=preconditions(reverting)
 if not commit:return {'preview':True,'restore_color_values':len(reverting),'stylesheet_records':len(state['files']),'audio_preconditions':guard}
 state['status']='restoring';save(journal,state)
 for path in dict.fromkeys(e['path'] for e in reverting):
  group=[e for e in reverting if e['path']==path]
  if group[0]['uuid']=='sound150@claudiux':preconditions(group)
  raw=Path(path).read_bytes();data=json.loads(raw)
  for e in group:
   if get(data,e['keys'])!=e['after']:raise Conflict('Concurrent restore color change: '+path+':'+e['setting_key'])
   put(data,e['keys'],e['before'])
  # The current document, including current audio volume/unknown fields, is kept.
  atomic(path,(json.dumps(data,indent=2)+'\n').encode(),sha(raw))
  for e in group:e['phase']='restored'
  save(journal,state)
 for f in state['files']:
  current=sha(Path(f['path']).read_bytes())
  if current==f['after_sha256']:atomic(f['path'],f['before_text'].encode(),current)
  f['phase']='restored';save(journal,state)
 reversed_entries=[dict(e,after=e['before']) for e in reverting]
 state['restore_refresh']=refresh(reversed_entries,guard);state['status']='restored';state['restored_at']=now();save(journal,state)
 return {'restored':True,'color_values':len(reverting),'refresh':state['restore_refresh'],'reload_required':state.get('reload_required',[])}
def check(plan,journal):
 target='after';entries=plan['entries'];files=plan.get('file_actions',[])
 if journal.exists():
  state=read_json(journal)
  if state.get('status')=='restored':target='before';entries=state['entries'];files=state['files']
 values=[{'path':e['path'],'key':e['setting_key'],'expected':e[target],'actual':get(read_json(e['path']),e['keys'])} for e in entries]
 hashes=[{'path':f['path'],'expected':f[target+'_sha256'],'actual':sha(Path(f['path']).read_bytes())} for f in files]
 return {'ok':all(v['expected']==v['actual'] for v in values+hashes),'target':target,'values':values,'stylesheets':hashes,'visual_verified':False}
def main():
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('action',choices=['apply','check','restore']);ap.add_argument('--commit',action='store_true');ap.add_argument('--plan',type=Path,default=HERE/'widget-plan.json');ap.add_argument('--journal',type=Path,default=HERE/'widget-state.json');args=ap.parse_args()
 plan=read_json(args.plan)
 try:
  if args.action=='check':result=check(plan,args.journal)
  elif args.commit:
   with lock(args.journal.with_suffix('.lock')):result=apply(plan,args.journal,True) if args.action=='apply' else restore(args.journal,True)
  else:result=apply(plan,args.journal,False) if args.action=='apply' else restore(args.journal,False)
  print(json.dumps(result,indent=2));return 0 if result.get('ok',True) else 1
 except Exception as err:print(json.dumps({'ok':False,'error':str(err),'journal':str(args.journal)},indent=2));return 2
if __name__=='__main__':sys.exit(main())
