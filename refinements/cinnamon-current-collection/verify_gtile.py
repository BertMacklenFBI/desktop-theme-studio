#!/usr/bin/python3
"""Inspect gTile through its configured shortcut with an owned GTK window."""
import argparse
import json
from pathlib import Path
import signal
import subprocess
import time
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('GdkX11', '3.0')
from gi.repository import Gtk, Gdk, GdkX11, Gio
from Xlib import X, XK, display
from Xlib.ext import xtest
from verify_live import evaluate

ROOT = Path(__file__).resolve().parent
GRID_STATE = '''JSON.stringify((function(){let found=[];function walk(a){
 if(a.get_style_class_name && (a.get_style_class_name()||'').split(' ').includes('grid-panel')){
  let c=a.get_theme_node().get_background_color();found.push({visible:a.visible,mapped:a.mapped,
  opacity:a.opacity,scale:a.scale_y,position:a.get_transformed_position(),size:a.get_transformed_size(),
  background:[c.red,c.green,c.blue,c.alpha]});}
 if(a.get_children) for(let ch of a.get_children())walk(ch);
 }walk(global.stage);return found;})())'''
BUTTON_STATE = '''JSON.stringify((function(){let found=[];function cls(a,s){return a.get_style_class_name && (a.get_style_class_name()||'').split(' ').includes(s);}
 function buttons(a){if(cls(a,'settings-button') && a.mapped){let labels=[];function texts(b){if(cls(b,'settings-label')){let c=b.get_theme_node().get_foreground_color();labels.push({text:b.get_text?b.get_text():null,color:[c.red,c.green,c.blue,c.alpha]});}if(b.get_children)for(let ch of b.get_children())texts(ch);}texts(a);let c=a.get_theme_node().get_background_color();found.push({pseudo:a.get_style_pseudo_class?a.get_style_pseudo_class():'',p:a.get_transformed_position(),s:a.get_transformed_size(),background:[c.red,c.green,c.blue,c.alpha],labels:labels});}if(a.get_children)for(let ch of a.get_children())buttons(ch);}
 function walk(a){if(cls(a,'grid-panel') && a.mapped)buttons(a);else if(a.get_children)for(let ch of a.get_children())walk(ch);}walk(global.stage);return found;})())'''

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('profile');args=parser.parse_args()
    profile=json.loads((ROOT/'profiles.json').read_text())['profiles'][args.profile]
    out=ROOT/'verification/gtile'/args.profile/time.strftime('%Y%m%d-%H%M%S');out.mkdir(parents=True)
    report={'profile':args.profile,'run':str(out),'checks':[]}
    x=display.Display();root=x.screen().root;pointer=root.query_pointer()
    focus=root.get_full_property(x.intern_atom('_NET_ACTIVE_WINDOW'),X.AnyPropertyType)
    original=int(focus.value[0]) if focus is not None and len(focus.value) else None
    window=None;opened=False
    def pump(seconds):
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            while Gtk.events_pending():Gtk.main_iteration_do(False)
            time.sleep(.012)
    def key(name,kind):xtest.fake_input(x,kind,x.keysym_to_keycode(XK.string_to_keysym(name)));x.sync()
    def escape():key('Escape',X.KeyPress);key('Escape',X.KeyRelease);pump(.45)
    def visible(items):return [v for v in items if v['visible'] and v['mapped'] and v['opacity']>0 and v['scale']>0]
    def check(name,condition):
        if not condition:raise RuntimeError(name)
        report['checks'].append(name)
    def stop(signum,frame):raise RuntimeError('gTile probe interrupted')
    signal.signal(signal.SIGTERM,stop)
    try:
        check('selected profile',Gio.Settings.new('org.cinnamon.theme').get_string('name')==profile['theme'])
        check('gTile enabled','gTile@shuairan' in Gio.Settings.new('org.cinnamon').get_strv('enabled-extensions'))
        config=json.loads((Path.home()/'.config/cinnamon/spices/gTile@shuairan/gTile@shuairan.json').read_text())
        check('reviewed Super+g shortcut',config['hotkey']['value']=='<Super>g')
        check('no preexisting gTile popup',not visible(evaluate(GRID_STATE)))
        window=Gtk.Window(title='Current Collection gTile verification — '+profile['name'])
        window.set_default_size(900,640);window.set_border_width(24)
        window.add(Gtk.Label(label='Theme verification: gTile opens above this owned window.'))
        window.show_all();window.present();pump(.6)
        xid=window.get_window().get_xid()
        subprocess.run(['wmctrl','-ia',hex(xid)],check=True,timeout=3);pump(.2)
        active=root.get_full_property(x.intern_atom('_NET_ACTIVE_WINDOW'),X.AnyPropertyType)
        check('owned window focused',active is not None and int(active.value[0])==xid)
        geometry=window.get_window().get_geometry()
        opened=True
        key('Super_L',X.KeyPress);key('g',X.KeyPress);key('g',X.KeyRelease);key('Super_L',X.KeyRelease);pump(.7)
        grids=visible(evaluate(GRID_STATE));report['grid_state']=grids
        check('actual gTile grid opened',bool(grids))
        expected=[int(profile['palette']['surface'][i:i+2],16) for i in (1,3,5)]
        check('rendered gTile surface matches profile',all(v['background'][:3]==expected for v in grids))
        buttons=evaluate(BUTTON_STATE);check('labeled grid controls available',bool(buttons))
        target=next(b for b in buttons if b['labels']);px=int(target['p'][0]+target['s'][0]/2);py=int(target['p'][1]+target['s'][1]/2)
        # A prior probe can leave the pointer at the same grid coordinates.
        # Cross the actor boundary so Cinnamon receives a real enter event.
        xtest.fake_input(x,X.MotionNotify,x=8,y=100);x.sync();pump(.18)
        xtest.fake_input(x,X.MotionNotify,x=px,y=py);x.sync();pump(.45)
        hovered=next(b for b in evaluate(BUTTON_STATE) if b['p']==target['p']);report['hovered_control']=hovered
        report['pointer']={'x':root.query_pointer().root_x,'y':root.query_pointer().root_y,'target':[px,py]}
        shot=out/'gtile.png';subprocess.run(['scrot','--overwrite',str(shot)],check=True,timeout=5)
        report['screenshot']=str(shot)
        expected_text=[int(profile['palette']['selection_foreground'][i:i+2],16) for i in (1,3,5)]
        expected_bg=[int(profile['palette']['selection'][i:i+2],16) for i in (1,3,5)]
        check('hovered control uses profile selection background',hovered['background'][:3]==expected_bg)
        check('hovered label uses readable selection text',all(v['color'][:3]==expected_text for v in hovered['labels']))
        active_controls=[b for b in evaluate(BUTTON_STATE) if 'activate' in (b.get('pseudo') or '').split()]
        if active_controls:
            target=active_controls[0]
            xtest.fake_input(x,X.MotionNotify,x=8,y=100);x.sync();pump(.12)
            xtest.fake_input(x,X.MotionNotify,x=int(target['p'][0]+target['s'][0]/2),y=int(target['p'][1]+target['s'][1]/2));x.sync();pump(.4)
            hovered=next(b for b in evaluate(BUTTON_STATE) if b['p']==target['p'])
            report['active_hovered_control']=hovered
            check('active hovered control uses profile background',hovered['background'][:3]==expected_bg)
            subprocess.run(['scrot','--overwrite',str(out/'gtile-active-hover.png')],check=True,timeout=5)
        else:report['active_hovered_control']='No active toggle available; user toggle settings preserved'
        escape();check('actual Escape closes grid',not visible(evaluate(GRID_STATE)));opened=False
        check('owned window was not tiled',window.get_window().get_geometry()==geometry)
        report['status']='passed'
    except Exception as exc:report.update(status='failed',error=str(exc))
    finally:
        try:
            if opened:escape()
            if window:window.destroy();pump(.1)
            if original:subprocess.run(['wmctrl','-ia',hex(original)],check=True,timeout=3)
            xtest.fake_input(x,X.MotionNotify,x=pointer.root_x,y=pointer.root_y);x.sync()
        except Exception as exc:report.setdefault('cleanup_errors',[]).append(str(exc));report['status']='failed'
        x.close();(out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2));return 0 if report['status']=='passed' else 1

if __name__=='__main__':raise SystemExit(main())
