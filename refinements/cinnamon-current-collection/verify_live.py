#!/usr/bin/python3
"""Exercise the current collection's selected Cinnamon shell; restore UI state."""
import argparse
import json
from pathlib import Path
import subprocess
import signal
import time
from gi.repository import Gio,GLib
from Xlib import X,XK,display
from Xlib.ext import xtest

ROOT=Path(__file__).resolve().parent
BUS=Gio.bus_get_sync(Gio.BusType.SESSION,None)

def evaluate(source):
    ok,value=BUS.call_sync('org.Cinnamon','/org/Cinnamon','org.Cinnamon','Eval',GLib.Variant('(s)',(source,)),
                          GLib.VariantType('(bs)'),Gio.DBusCallFlags.NONE,6000,None).unpack()
    if not ok:raise RuntimeError(value)
    while isinstance(value,str):
        try:value=json.loads(value)
        except ValueError:break
    return value

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('profile');args=parser.parse_args()
    profile=json.loads((ROOT/'profiles.json').read_text())['profiles'][args.profile]
    out=ROOT/'verification/live'/args.profile/time.strftime('%Y%m%d-%H%M%S');out.mkdir(parents=True)
    x=display.Display();root=x.screen().root;pointer=root.query_pointer()
    show=root.get_full_property(x.intern_atom('_NET_SHOWING_DESKTOP'),X.AnyPropertyType)
    previous_show=bool(show and show.value[0]);active=root.get_full_property(x.intern_atom('_NET_ACTIVE_WINDOW'),X.AnyPropertyType)
    report={'profile':args.profile,'run':str(out),'scope':'real selected Cinnamon desktop','checks':[], 'screenshots':[]}
    opened=[]
    def check(name,value):
        if not value:raise RuntimeError(name)
        report['checks'].append(name)
    def shot(name):
        path=out/(name+'.png');subprocess.run(['scrot','--overwrite',str(path)],check=True,timeout=8)
        report['screenshots'].append(str(path))
    def click(px,py):
        xtest.fake_input(x,X.MotionNotify,x=int(px),y=int(py));x.sync();time.sleep(.08)
        xtest.fake_input(x,X.ButtonPress,1);x.sync();time.sleep(.08)
        xtest.fake_input(x,X.ButtonRelease,1);x.sync();time.sleep(.35)
    def applet(name):return 'imports.ui.appletManager.filterDefinitionsByUUID('+json.dumps(name)+')[0].applet'
    def interrupted(signum,frame):raise RuntimeError('Shell probe interrupted; restoring popup state')
    signal.signal(signal.SIGTERM,interrupted)
    try:
        check('current Cinnamon theme selected',Gio.Settings.new('org.cinnamon.theme').get_string('name')==profile['theme'])
        check('current GTK theme selected',Gio.Settings.new('org.cinnamon.desktop.interface').get_string('gtk-theme')==profile['theme'])
        check('current icon theme selected',Gio.Settings.new('org.cinnamon.desktop.interface').get_string('icon-theme')==profile['icons'])
        check('current cursor theme selected',Gio.Settings.new('org.cinnamon.desktop.interface').get_string('cursor-theme')==profile['cursor'])
        subprocess.run(['wmctrl','-k','on'],check=True,timeout=5);time.sleep(.7);shot('desktop')
        for name in ('menu@cinnamon.org','notifications@cinnamon.org','sound@cinnamon.org'):
            menu=applet(name)+'.menu';previous=evaluate(menu+'.isOpen');opened.append((menu,previous))
            evaluate(menu+'.open();true');time.sleep(.5);check(name+' opens',evaluate(menu+'.isOpen'))
            shot(name.partition('@')[0]);evaluate(menu+'.close();true');check(name+' closes',not evaluate(menu+'.isOpen'))
        island=applet('nothing-island@desktop-theme-studio');menu=island+'.menu'
        opened.append((menu,evaluate(menu+'.isOpen')))
        evaluate(menu+'.open();true');time.sleep(.8);shot('island')
        report['island_state']=evaluate('JSON.stringify('+island+'._state)')
        geometry=evaluate('JSON.stringify((function(){let a='+island+'._closeButton;return {p:a.get_transformed_position(),s:a.get_transformed_size()};})())')
        click(geometry['p'][0]+geometry['s'][0]/2,geometry['p'][1]+geometry['s'][1]/2)
        check('Island actual top-right ×',not evaluate(menu+'.isOpen'))
        evaluate(menu+'.open();'+island+'._closeButton.grab_key_focus();true');time.sleep(.2)
        key=x.keysym_to_keycode(XK.string_to_keysym('Escape'));xtest.fake_input(x,X.KeyPress,key);xtest.fake_input(x,X.KeyRelease,key);x.sync();time.sleep(.3)
        check('Island actual Escape',not evaluate(menu+'.isOpen'))
        evaluate(menu+'.open();true');time.sleep(.3);click(30,x.screen().height_in_pixels-100)
        check('Island outside click',not evaluate(menu+'.isOpen'))
        report['display']={'width':x.screen().width_in_pixels,'height':x.screen().height_in_pixels}
        report['status']='passed'
    except Exception as exc:report.update(status='failed',error=str(exc))
    finally:
        for menu,was_open in reversed(opened):
            try:evaluate(menu+('.open();true' if was_open else '.close();true'))
            except Exception:pass
        subprocess.run(['wmctrl','-k','on' if previous_show else 'off'],check=False,timeout=5)
        if active and active.value[0] and not previous_show:
            subprocess.run(['wmctrl','-ia',hex(int(active.value[0]))],check=False,timeout=5)
        xtest.fake_input(x,X.MotionNotify,x=pointer.root_x,y=pointer.root_y);x.sync();x.close()
        (out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
        latest=ROOT/'verification/live-latest'/f'{args.profile}.json';latest.parent.mkdir(exist_ok=True)
        latest.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
    return 0 if report['status']=='passed' else 1

if __name__=='__main__':raise SystemExit(main())
