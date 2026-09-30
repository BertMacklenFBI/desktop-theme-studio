#!/usr/bin/python3
"""Pure temporary/mock checks. Never starts mpv, Eww, D-Bus or a server."""
from pathlib import Path
import importlib.util,json,os,sys,tempfile,wave
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
import common,client,launch

def main():
    results=[]
    with tempfile.TemporaryDirectory(prefix='dts-actual-player-unit-') as temporary:
        root=Path(temporary);home=root/'home';home.mkdir(mode=0o700);env=dict(os.environ,DTS_PRIVATE_ROOT=str(root),HOME=str(home),DISPLAY=':9876',DBUS_SESSION_BUS_ADDRESS='unix:abstract=private-fixture',CURRENT_COLLECTION_HOST_DISPLAY=':0',CURRENT_COLLECTION_HOST_BUS='unix:abstract=host-fixture')
        def attestation(endpoint,*,attest_environment=True):
            private=endpoint['name']=='private-fixture'
            return {'pid':111 if private else 222,'uid':os.getuid(),'birth':'1' if private else '2'},{b'HOME':str(home).encode(),b'DISPLAY':b':9876'}
        with patch.dict(os.environ,env,clear=True),patch.object(common,'bus_attestation',side_effect=attestation):
            assert common.context()[0]==root
            for mutation in [{'DISPLAY':':0'},{'DBUS_SESSION_BUS_ADDRESS':'unix:abstract=host-fixture,guid=alias'},{'DTS_PRIVATE_ROOT':'/tmp'},{'DTS_PRIVATE_ROOT':'/home/foreign'}]:
                with patch.dict(os.environ,mutation):
                    try:common.context();raise AssertionError('Unknown context accepted')
                    except RuntimeError:pass
            results.append('private root/home/display/bus guards')
            target=root/'owned.wav';launch.make_track(target,440,.05)
            with wave.open(str(target)) as sample:assert sample.getnchannels()==1 and sample.getsampwidth()==2 and sample.getnframes()==1102
            results.append('owned PCM WAV generation')
            class Bus:
                def list_names(self):return ['org.mpris.MediaPlayer2.foreign','org.mpris.MediaPlayer2.mpv','org.mpris.MediaPlayer2.mpv.instance4321']
                def get_name_owner(self,name):return ':foreign' if name.endswith('foreign') else ':owned'
            with patch.object(client,'owner_pid',side_effect=lambda bus,owner:999 if owner==':foreign' else 4321):assert client.discover(4321,Bus())['name']=='org.mpris.MediaPlayer2.mpv.instance4321'
            results.append('MPRIS discovery selects exact owned PID')
            tracks=[str(root/('owned-track-'+str(i)+'.wav')) for i in (1,2)];state={'pause':False,'time-pos':2.0,'duration':30.0,'path':tracks[0],'playlist-pos':0};value={'schema':1,'media_type':'actual','pid':4321,'identity':{'birth':'1234'},'mpris':{'name':'org.mpris.MediaPlayer2.mpv','owner':':owned','owner_pid':4321},'tracks':tracks,'socket':str(root/'mpv.sock')};calls=[]
            class Properties:
                def GetAll(self,interface,timeout=3):return {'PlaybackStatus':'Paused' if state['pause'] else 'Playing','Metadata':{'mpris:trackid':'/owned/track'+str(state['playlist-pos']),'xesam:title':'Owned '+str(state['playlist-pos']),'xesam:url':Path(state['path']).as_uri()}}
                def Get(self,interface,property,timeout=3):return int(state['time-pos']*1_000_000)
            class Player:
                def PlayPause(self,timeout=3):calls.append('PlayPause');state['pause']=not state['pause']
                def Play(self,timeout=3):calls.append('Play');state['pause']=False
                def Pause(self,timeout=3):calls.append('Pause');state['pause']=True
                def Next(self,timeout=3):calls.append('Next');state['playlist-pos']=1;state['path']=tracks[1];state['time-pos']=0
                def Previous(self,timeout=3):calls.append('Previous');state['playlist-pos']=0;state['path']=tracks[0];state['time-pos']=0
                def SetPosition(self,track,position,timeout=3):calls.append('SetPosition');state['time-pos']=int(position)/1_000_000
            with patch.object(client,'endpoint',return_value=(value,Player(),Properties())),patch.object(client,'ipc',side_effect=lambda path,command:state[command[1]]),patch.object(client,'player_root',return_value=root),patch.object(client,'safe_target',side_effect=lambda p:p):
                for action,argument in [('pause',None),('play',None),('toggle',None),('seek',35),('next',None),('previous',None)]:assert client.action(action,argument)['status']=='passed'
                assert calls==['Pause','Play','PlayPause','SetPosition','Next','Previous']
                assert client.actual_media()['media_type']=='actual'
                for invalid in [float('nan'),-1,101]:
                    try:client.action('seek',invalid);raise AssertionError('Unsafe seek accepted')
                    except RuntimeError:pass
                state['path']='/foreign/media.wav'
                try:client.snapshot();raise AssertionError('Foreign decoded file accepted')
                except RuntimeError:pass
            results.append('media endpoint dispatch and independent IPC readback model')
            results.append('nonfinite/outside seek and foreign decoded file refusal')
            with patch.object(client,'binding',return_value=value),patch.object(client,'connect',return_value=Bus()),patch.object(client,'owner_pid',return_value=999):
                try:client.endpoint();raise AssertionError('Changed MPRIS PID accepted')
                except RuntimeError:pass
            results.append('changed MPRIS owner PID refusal')
            verification_calls=[];written=[];paused_snapshot={'paused':True,'playback_status':'Paused','media_type':'actual'}
            def verified_action(command,argument=None):
                verification_calls.append((command,argument));return {'action':command,'argument':argument,'status':'passed','media_type':'actual'}
            with patch.object(client,'action',side_effect=verified_action),patch.object(client,'snapshot',return_value=paused_snapshot),patch.object(launch,'player_root',return_value=root),patch.object(launch,'write_json',side_effect=lambda path,value:written.append(value)):
                verified=launch.verify()
            assert verification_calls==[('pause',None),('play',None),('seek',35),('next',None),('previous',None),('pause',None)]
            assert [receipt['action'] for receipt in verified['actions']]==['pause','play','seek','next','previous']
            assert verified['final_state']=='paused' and verified['capture_stability']['pause']['action']=='pause' and verified['capture_stability']['readback']==paused_snapshot and written==[verified]
            results.append('verify preserves five action receipts then attests actual paused capture state')
            for invalid in [{'paused':False,'playback_status':'Playing'},{'paused':True,'playback_status':'Playing'}]:
                written.clear()
                with patch.object(client,'action',side_effect=verified_action),patch.object(client,'snapshot',return_value=invalid),patch.object(launch,'player_root',return_value=root),patch.object(launch,'write_json',side_effect=lambda path,value:written.append(value)):
                    try:launch.verify();raise AssertionError('Unpaused or disagreeing capture accepted')
                    except RuntimeError:pass
                assert written==[]
            results.append('unpaused or disagreeing final MPRIS/IPC capture state emits no acceptance')
    print(json.dumps({'schema':1,'scope':'Pure temp files and mocked endpoint/IPC; no actual media or GUI starts; not native player acceptance','status':'passed','checks':results},indent=2))
if __name__=='__main__':main()
