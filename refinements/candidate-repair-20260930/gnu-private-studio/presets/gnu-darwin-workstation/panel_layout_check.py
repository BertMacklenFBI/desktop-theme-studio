#!/usr/bin/python3
"""Non-GUI Workstation controller, allocation, restore and rendering contract fixtures."""
import copy, importlib.util, json, tempfile
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parent

def module(name):
    spec=importlib.util.spec_from_file_location(name, ROOT/(name+'.py')); obj=importlib.util.module_from_spec(spec); spec.loader.exec_module(obj); return obj
control=module('desktop_control'); isolate=module('isolate')
design=json.loads((ROOT/'design.json').read_text()); panel=json.loads((ROOT/'lineage.json').read_text())['desktop_control']['panel']
def require(value,msg):
    if not value: raise AssertionError(msg)
def rejects(call):
    try: call()
    except (RuntimeError,ValueError): return
    raise AssertionError('Expected rejection')
require(control.panel_problem(panel) is None,'schema')
require(not control.design_panel_problems(panel,design),'canonical design')
require(panel['panels_enabled']==['1:0:right','2:0:bottom'],'positions')
require(panel['panels_height']==['1:64','2:64'],'dimensions')
require(panel['enabled_applets'][1]=='panel2:left:0:menu@cinnamon.org:17','visible menu')
for ids in ([], ['../x.desktop'], ['x.desktop','x.desktop'], ['$(cmd).desktop'], ['x.desktop\0'], ['x']):
    require(not control.valid_launcher_ids(ids),'unsafe desktop IDs accepted')
for instance in panel['new_instances']:
    text, info=control.applet_settings_text(instance['uuid'],instance['settings'])
    require(info['max_instances'] != 1,'instance multiplicity')
    actual=json.loads(text)
    for key,value in instance['settings'].items(): require(actual[key]['value']==value,'schema serialization')
    if instance['uuid']=='panel-launchers@cinnamon.org':
        for ident in instance['settings']['launcherList']: require(control.Gio.DesktopAppInfo.new(ident) is not None,'missing installed launcher')
with patch.object(control,'applied_instance',return_value=(None,None)):
    reused, allocated=control.allocate_instances(panel,{},47)
    require(reused is None and [x['id'] for x in allocated]==[47,48,49],'consecutive allocation')
    rejects(lambda:control.allocate_instances(panel,{48:('other','')},47))
with patch.object(control,'applied_instance',side_effect=[(Path('/receipt'),47),(None,None),(None,None)]):
    rejects(lambda:control.allocate_instances(panel,{},50))
with patch.object(control,'applied_instance',side_effect=[(Path('/a'),47),(Path('/b'),48),(Path('/b'),49)]):
    rejects(lambda:control.allocate_instances(panel,{},50))
with patch.object(control,'applied_instance',side_effect=[(Path('/a'),47),(Path('/a'),48),(Path('/a'),49)]):
    reused, allocated=control.allocate_instances(panel,{},50)
    require(reused==Path('/a') and [x['id'] for x in allocated]==[47,48,49],'reuse both matching receipt')

# Exercise the real restore loop on two new settings files, mocking only GSettings/I/O journaling.
with tempfile.TemporaryDirectory(prefix='workstation-restore-') as tmp:
    files={}
    for item in allocated:
        path=Path(tmp)/(str(item['id'])+'.json');path.write_text('new instance')
        files[str(path)]={'before':{'kind':'absent'},'after':control.record(path)}
    keys=[k for step in control.RESTORE_STEPS for k in step]
    rows=[{'key':k,'schema':control.PANEL,'before':repr('original-'+k),'after':repr('new-'+k),'user':None} for k in keys]
    state={r['key']:r['after'] for r in rows}; written=[]
    data={'panel':{'settings':rows,'files':files,'snapshots':{},'insurance':{},'next_applet_id':{'after':50}}}
    class Settings:
        def get_int(self,key): return 50
    class GioFake:
        class Settings:
            @staticmethod
            def new(schema): return Settings()
            @staticmethod
            def sync(): pass
        @staticmethod
        def sync(): pass
    def set_row(row,which): state[row['key']]=row[which];written.append(row['key'])
    with patch.object(control,'Gio',GioFake),patch.object(control,'save'),patch.object(control,'live_value',side_effect=lambda row:state[row['key']]),patch.object(control,'same_setting',side_effect=lambda row,a,b:a==b),patch.object(control,'set_row',side_effect=set_row),patch.object(control,'settle',return_value=[]):
        conflicts=[];control.restore_panel(Path(tmp)/'receipt.json',data,conflicts,False)
    require(not conflicts and all(not Path(raw).exists() for raw in files),'both new files removed by restore')
    require(all(state[r['key']]==r['before'] for r in rows),'all settings restored')
    require(written.index('panel-launchers')>written.index('enabled-applets'),'legacy import key must restore after applet removal')
    require(data['panel']['next_applet_id']['at_restore']==50,'counter monotonic')

require(control.design_appearance()==isolate.APPEARANCE,'appearance parity')
require(isolate.APPEARANCE['button_layout']=='minimize:close','square button order')
orient='panel1:center:1:nothing-island@desktop-theme-studio:42:orient'
require(control.parse_live([orient])[42][1]==orient,'orient preserved')
rejects(lambda:control.parse_live([orient+':bad']))
fixture=[dict(id=1,pos=3,x=1536,y=0,width=64,height=1136,monitor_width=1600,monitor_height=1200,zone_heights=[960,0,0],launcher_tiles=[dict(width=64,height=64) for _ in range(isolate.LAUNCHER_COUNT)]),dict(id=2,pos=1,x=0,y=1136,width=1600,height=64,monitor_width=1600,monitor_height=1200,zone_heights=[64,0,64],launcher_tiles=[])]
require(not isolate.taskbar_problems(fixture),'valid Workstation geometry')
for change in (lambda x:x.pop(),lambda x:x[0].update(height=1000),lambda x:x[0].update(pos=0),lambda x:x[1].update(height=65),lambda x:x[0].update(monitor_height=1000),lambda x:x[0]['launcher_tiles'][0].update(width=63),lambda x:x[1].update(zone_heights=[65,0,0])):
    bad=copy.deepcopy(fixture);change(bad);require(isolate.taskbar_problems(bad),'bad geometry accepted')
ready=dict(isOpen=True,visible=True,mapped=True,opacity=255,width=400,height=600)
require(isolate.popup_ready(ready),'popup')
require(not isolate.popup_ready({**ready,'isOpen':False}),'closed menu')
maximized=dict(maximized=3,visible=True,mapped=True,opacity=255,frame=[0,0,1536,936],workarea=[0,0,1536,936])
require(isolate.maximized_ready(maximized),'maximize')
require(not isolate.maximized_ready({**maximized,'frame':[0,0,1600,1000]}),'incorrect maximize')
with tempfile.TemporaryDirectory(prefix='workstation-gate-') as tmp:
    root=Path(tmp)
    for name in ('design.json','lineage.json','isolate.py','theme.py','enhance.py','desktop/workspace-picker.css','desktop/workspace_adapter.py','desktop/logo_adapter.py','applications/runtime/composed.py'):
        (root/name).parent.mkdir(parents=True,exist_ok=True)
        (root/name).write_bytes((ROOT/name).read_bytes())
    (root/'applications/generated').mkdir(parents=True)
    (root/'applications/adapter.py').write_bytes((ROOT/'applications/adapter.py').read_bytes())
    (root/'applications/generated/fixture.conf').write_text('first')
    with patch.object(control,'ROOT',root),patch.object(control,'LINEAGE_FILE',root/'lineage.json'):
        original=control.taskbar_fingerprint(); altered=copy.deepcopy(design);altered['typography']['ui_size_pt']+=1
        (root/'design.json').write_text(json.dumps(altered));changed=control.taskbar_fingerprint()
        require(original['design.json#full']!=changed['design.json#full'] and original['design.json#appearance']!=changed['design.json#appearance'],'appearance sourcegate')
        (root/'applications/adapter.py').write_text('changed adapter')
        (root/'applications/generated/fixture.conf').write_text('changed config')
        app_changed=control.taskbar_fingerprint()
        require(changed['applications/adapter.py']!=app_changed['applications/adapter.py'] and changed['applications/generated']!=app_changed['applications/generated'],'application sourcegate')
# The monitor uses the real asset transaction, restoring absent and pre-existing destinations.
for existing in (False,True):
    with tempfile.TemporaryDirectory(prefix='monitor-asset-') as tmp:
        root=Path(tmp); source=root/'source'; source.mkdir(); (source/'applet.js').write_text('new monitor')
        destination=root/'private-data/applets/monitor'; destination.parent.mkdir(parents=True)
        if existing:
            destination.mkdir(); (destination/'applet.js').write_text('original monitor')
        original=control.tree_record(destination); receipt=root/'state/desktop.json'
        with patch.object(control,'asset_locations',return_value=[(source,destination)]):
            assets=control.prepare_assets(receipt,'fixture')
        data={'guard_armed':True,'assets':assets}
        control.swap_assets(receipt,data)
        require(control.tree_record(destination)==control.tree_record(source),'monitor asset install')
        control.restore_asset(receipt,data,assets[0])
        require(control.tree_record(destination)==original,'monitor asset restore')

# Recovery must remove the panel instance before removing monitor code, and retain code on defer.
for restored in (True,False):
    with tempfile.TemporaryDirectory(prefix='monitor-recovery-order-') as tmp:
        order=[]; path=Path(tmp)/'receipt.json'
        data={'status':'pending','files':{},'settings':[],'assets':[{'destination':str(Path(tmp)/control.MONITOR_UUID)}],
              'panel':{'phase':'applied'},'opacity_active':False}
        def panel_restore(path,data,conflicts,automatic):
            order.append('panel');data['panel']['phase']='restored' if restored else 'restore-deferred';data['panel']['restore_verified']=restored
        with patch.object(control,'load',return_value=data),patch.object(control,'save'),patch.object(control,'asset_restore_problem',return_value=None),patch.object(control,'panel_conflicts',return_value=[]),patch.object(control,'restore_panel',side_effect=panel_restore),patch.object(control,'restore_asset',side_effect=lambda *args:order.append('asset')),patch.object(control,'run'),patch.object(control,'bus'),patch.object(control.Gio.Settings,'sync'):
            result=control.restore(path,automatic=True)
        require(order==(['panel','asset'] if restored else ['panel']),'unsafe applet source removal order')
        require(result['status']==('restored' if restored else 'recovery-required'),'deferred monitor source status')

# Automatic enabled-applets write failure must retain applet code; retry may recover despite history.
with tempfile.TemporaryDirectory(prefix='monitor-write-failure-') as tmp:
    root=Path(tmp); receipt=root/'receipt.json'; destination=root/control.MONITOR_UUID
    keys=[k for step in control.RESTORE_STEPS for k in step]
    rows=[{'key':k,'schema':control.PANEL,'before':repr('original-'+k),'after':repr('new-'+k),'user':None} for k in keys]
    state={r['key']:r['after'] for r in rows}; removed=[]; fail=[True]
    data={'status':'pending','files':{},'settings':[],'assets':[{'destination':str(destination)}],
          'panel':{'phase':'applied','settings':rows,'files':{},'snapshots':{},'insurance':{},'next_applet_id':{'after':50}},'opacity_active':False}
    def write_setting(row,which):
        if fail[0] and row['key']=='enabled-applets': raise RuntimeError('injected enabled-applets write failure')
        state[row['key']]=row[which]
    with patch.object(control,'load',return_value=data),patch.object(control,'save'),patch.object(control,'asset_restore_problem',return_value=None),patch.object(control,'panel_conflicts',return_value=[]),patch.object(control,'restore_asset',side_effect=lambda *args:removed.append('asset')),patch.object(control,'run'),patch.object(control,'bus'),patch.object(control,'Gio',GioFake),patch.object(control,'live_value',side_effect=lambda row:state[row['key']]),patch.object(control,'same_setting',side_effect=lambda row,a,b:a==b),patch.object(control,'set_row',side_effect=write_setting),patch.object(control,'settle',return_value=[]):
        failed=control.restore(receipt,automatic=True)
        require(failed['status']=='recovery-required' and not removed and not data['panel']['restore_verified'],'failed applet write removed its source')
        fail[0]=False
        recovered=control.restore(receipt,automatic=True)
        require(recovered['status']=='restored' and removed==['asset'] and data['panel']['restore_verified'],'retry failed to recover')
        require(data['panel']['restore_errors']==[] and data['panel']['restore_error_history'],'historical errors incorrectly block retry or disappear')

sample=dict(sequence=1,timestamp=1,cpuPercent=10,memoryPercent=20,rxBytesPerSec=0,txBytesPerSec=0,disposed=False,sourceId=7)
next_sample={**sample,'sequence':2,'timestamp':2}
require(isolate.monitor_samples_valid(sample,next_sample),'advancing monitor')
for bad in ({**next_sample,'sequence':1},{**next_sample,'cpuPercent':101},{**next_sample,'rxBytesPerSec':-1},{**next_sample,'sourceId':0}):
    require(not isolate.monitor_samples_valid(sample,bad),'invalid monitor sample')
disposed={**next_sample,'disposed':True,'sourceId':0}
require(isolate.monitor_cleanup_valid(disposed,disposed),'clean timer removal')
require(not isolate.monitor_cleanup_valid(disposed,{**disposed,'sequence':3}),'timer kept sampling')
require(any(source == control.MONITOR_SOURCE for source,dest in control.asset_locations()),'monitor asset missing')

print('GNU-Darwin Workstation fixtures PASS: three-instance allocation/reuse/collisions/restore, launcher schema, geometry, popup, maximized and source gate.')
