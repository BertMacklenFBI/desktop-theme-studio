const Applet = imports.ui.applet;
const AppletManager = imports.ui.appletManager;
const PopupMenu = imports.ui.popupMenu;
const Slider = imports.ui.slider;
const Tooltips = imports.ui.tooltips;
const Main = imports.ui.main;
const Mainloop = imports.mainloop;
const GnomeSession = imports.misc.gnomeSession;
const Util = imports.misc.util;
const St = imports.gi.St;
const Clutter = imports.gi.Clutter;
const Gio = imports.gi.Gio;
const GLib = imports.gi.GLib;
const Pango = imports.gi.Pango;

class NothingIsland extends Applet.Applet {
    constructor(metadata, orientation, panelHeight, instanceId) {
        super(orientation, panelHeight, instanceId);
        this._path = metadata.path;
        this._alive = true;
        this._destroyed = false;
        this._actionBusy = false;
        this._snapshotBusy = false;
        this._processes = new Set();
        this._tips = [];
        this._sliderTimers = new Map();
        this._pollId = 0;
        this._cookie = null;
        this._coffeePending = false;
        this._session = null;
        this._state = null;
        this._coverPath = '';
        this._month = new Date();
        this.setAllowedLayout(Applet.AllowedLayout.HORIZONTAL);
        this.actor.add_style_class_name('ni-applet');
        this.actor.set_style('background-color:transparent; color:#d5a1aa; border-radius:18px; box-shadow:none; padding:0; margin:0;');
        this.set_applet_tooltip('Nothing Island · click for music and controls');
        this._compact = new St.BoxLayout({style_class:'ni-compact', y_align:Clutter.ActorAlign.CENTER});
        this._compactWeather = this._label('Weather —', 'ni-compact-side');
        this._compactWeather.set_width(130);
        this._compactWeather.clutter_text.set_ellipsize(Pango.EllipsizeMode.END);
        this._compactClock = this._label('--:--', 'ni-compact-clock');
        this._compactStatus = this._label('Connecting…', 'ni-compact-side');
        this._compactStatus.set_width(170);
        this._compactStatus.clutter_text.set_ellipsize(Pango.EllipsizeMode.END);
        this._compact.set_style('background-color:#211e29;');
        this._compactWeather.set_style('color:#d5a1aa;');
        this._compactClock.set_style('color:#d5a1aa;');
        this._compactStatus.set_style('color:#d5a1aa;');
        this._compact.add(this._compactWeather, {expand:false});
        this._compact.add(this._compactClock, {expand:true, x_fill:false, x_align:St.Align.MIDDLE});
        this._compact.add(this._compactStatus, {expand:false});
        this.actor.add_actor(this._compact);
        this.menuManager = new PopupMenu.PopupMenuManager(this);
        this.menu = new Applet.AppletPopupMenu(this, orientation);
        this.menu.setCustomStyleClass('ni-popup');
        this.menuManager.addMenu(this.menu);
        this._content = new St.BoxLayout({vertical:true, style_class:'ni-content'});
        this.menu.addActor(this._content);
        this._buildPopup();
        this.menu.connect('open-state-changed', () => {
            if (!this._alive) return;
            this._schedulePoll(0);
        });
        this.menu.actor.connect('key-press-event', (actor,event) => {
            if (event.get_key_symbol() === Clutter.KEY_Escape) {this.menu.close();return Clutter.EVENT_STOP;}
            return Clutter.EVENT_PROPAGATE;
        });
        GnomeSession.SessionManager((proxy,error) => {
            if (!this._alive) return;
            if (error) {this._setEnabled(this._coffeeButton,false);return;}
            this._session = proxy;
            this._setEnabled(this._coffeeButton,true);
        });
        this._recordSignal = 0;
        if (Main.screenRecorder && Main.screenRecorder.connect) {
            this._recordSignal = Main.screenRecorder.connect('recording', () => this._recordState());
            this._recordState();
        } else this._setEnabled(this._recordButton,false);
        this._clockId = Mainloop.timeout_add_seconds(1, () => {if (!this._alive) return false;this._clock();return true;});
        this._clock();
        this._schedulePoll(0);
    }
    _label(text,style='ni-text') {return new St.Label({text:String(text),style_class:style,y_align:Clutter.ActorAlign.CENTER});}
    _box(style='ni-row',vertical=false) {return new St.BoxLayout({style_class:style,vertical});}
    _button(text,icon,action,style='ni-button') {
        const b = new St.Button({style_class:style,reactive:true,can_focus:true,track_hover:true});
        const row = this._box('ni-button-inner');
        if (icon) {b._icon=new St.Icon({icon_name:icon,icon_type:St.IconType.SYMBOLIC,icon_size:17});row.add_actor(b._icon);}
        b._label = this._label(text,'ni-button-label');row.add_actor(b._label);b.set_child(row);b.label_actor=b._label;b.accessible_name=text;
        b.connect('clicked', () => {try {action();} catch(e) {this._error(e.message || String(e));}});
        return b;
    }
    _tip(actor,text) {const t=new Tooltips.Tooltip(actor,text);this._tips.push(t);return t;}
    _setEnabled(button,enabled) {
        button.reactive=!!enabled;button.can_focus=!!enabled;
        if (enabled) button.remove_style_pseudo_class('disabled');else button.add_style_pseudo_class('disabled');
    }
    _buildPopup() {
        const head=this._box('ni-header');
        const time=this._box('ni-time',true);this._bigClock=this._label('--:--','ni-display');this._date=this._label('','ni-muted');
        time.add_actor(this._bigClock);time.add_actor(this._date);head.add(time,{expand:true});
        this._close=this._button('×',null,()=>this.menu.close(),'ni-close');this._close.y_expand=false;this._close.y_align=Clutter.ActorAlign.CENTER;this._closeButton=this._close;this._close.accessible_name='Close island';this._tip(this._close,'Close island');head.add(this._close,{expand:false,y_fill:false,y_align:St.Align.MIDDLE});this._content.add_actor(head);
        const tabs=this._box('ni-tabs');
        for (const [id,title] of [['home','Overview'],['calendar','Calendar'],['settings','Controls']]) {
            const b=this._button(title,null,()=>this._page(id),'ni-tab');b._page=id;tabs.add(b,{expand:true});
        }
        this._tabs=tabs;this._content.add_actor(tabs);
        this._pages={home:this._box('ni-page',true),calendar:this._box('ni-page',true),settings:this._box('ni-page',true)};
        for (const p of Object.values(this._pages)) this._content.add_actor(p);
        const home=this._pages.home;
        const status=this._box('ni-status-row');
        this._wifiButton=this._button('Wi-Fi —','network-wireless-symbolic',()=>this._action('wifi'),'ni-status');
        this._btButton=this._button('Bluetooth —','bluetooth-symbolic',()=>this._action('bluetooth'),'ni-status');
        this._battery=this._label('Battery —','ni-status-label');
        const topVolume=this._box('ni-top-volume');
        topVolume.add_actor(new St.Icon({icon_name:'audio-volume-high-symbolic',icon_type:St.IconType.SYMBOLIC,icon_size:18}));
        this._inlineVolumeText=this._label('—','ni-caption');topVolume.add_actor(this._inlineVolumeText);
        this._inlineVolume=new Slider.Slider(0);this._inlineVolume.actor.add_style_class_name('ni-slider');this._inlineVolume.actor.add_style_class_name('ni-mini-slider');topVolume.add(this._inlineVolume.actor,{expand:true});
        this._wireSlider(this._inlineVolume,'Output volume',()=>{if(this._state&&this._state.capabilities.audio)this._action('volume',String(Math.round(this._inlineVolume._value*100)));});
        status.add(this._wifiButton,{expand:false});status.add(this._btButton,{expand:false});status.add(topVolume,{expand:true});home.add_actor(status);
        const media=this._box('ni-media',true);const mediaTop=this._box('ni-media-top');
        this._cover=new St.Bin({style_class:'ni-cover',width:68,height:68});this._cover.set_child(new St.Icon({icon_name:'audio-x-generic-symbolic',icon_type:St.IconType.SYMBOLIC,icon_size:38}));
        mediaTop.add_actor(this._cover);const details=this._box('ni-media-details',true);
        this._title=this._label('Nothing playing','ni-title');this._title.clutter_text.set_ellipsize(Pango.EllipsizeMode.END);this._title.set_width(220);
        this._artist=this._label('Open your existing music player','ni-muted');this._artist.clutter_text.set_ellipsize(Pango.EllipsizeMode.END);this._artist.set_width(220);
        details.add_actor(this._title);details.add_actor(this._artist);mediaTop.add(details,{expand:true});
        const transport=this._box('ni-transport');this._mediaButtons=[];
        for (const [title,icon,verb] of [['Previous','media-skip-backward-symbolic','previous'],['Play','media-playback-start-symbolic','toggle'],['Next','media-skip-forward-symbolic','next']]) {
            const button=this._button('',icon,()=>this._action('media',verb),'ni-transport-button');button.accessible_name=title;this._tip(button,title);transport.add_actor(button);this._mediaButtons.push(button);
        }
        mediaTop.add_actor(transport);media.add_actor(mediaTop);
        this._wave=new St.DrawingArea({style_class:'ni-wave',height:30,reactive:true});
        this._wave.connect('repaint',()=>this._paintWave());this._tip(this._wave,'Decorative track pattern, not live audio. Use the slider below to seek.');media.add_actor(this._wave);
        this._seek=new Slider.Slider(0);this._seek.actor.add_style_class_name('ni-slider');media.add_actor(this._seek.actor);
        this._wireSlider(this._seek,'Track position',()=>{if(this._state && this._state.media.duration>0)this._action('media','seek',String(Math.round(this._seek._value*100)));});
        this._position=this._label('0:00 / 0:00','ni-caption');media.add_actor(this._position);home.add_actor(media);
        const lower=this._box('ni-lower');
        this._recordButton=this._button('Record screen','media-record-symbolic',()=>this._record(),'ni-record-tile');
        lower.add(this._recordButton,{expand:true});
        const meters=this._box('ni-meters',true);this._cpu=this._label('CPU —','ni-metric');this._memory=this._label('Memory —','ni-metric');this._temp=this._label('Temperature —','ni-caption');this._cpuBar=new St.DrawingArea({style_class:'ni-meter-bar',height:4});this._memoryBar=new St.DrawingArea({style_class:'ni-meter-bar',height:4});this._cpuBar.connect('repaint',()=>this._paintMeter(this._cpuBar,this._state?this._state.cpu:null));this._memoryBar.connect('repaint',()=>this._paintMeter(this._memoryBar,this._state?this._state.memory:null));meters.add_actor(this._cpu);meters.add_actor(this._cpuBar);meters.add_actor(this._memory);meters.add_actor(this._memoryBar);meters.add_actor(this._temp);lower.add(meters,{expand:true});home.add_actor(lower);
        this._buildCalendar();
        const settings=this._pages.settings;
        const audio=this._box('ni-card',true);const volumeHead=this._box();this._volumeText=this._label('Output volume —','ni-title');volumeHead.add(this._volumeText,{expand:true});this._muteButton=this._button('Mute','audio-volume-muted-symbolic',()=>this._action('mute'));volumeHead.add_actor(this._muteButton);audio.add_actor(volumeHead);
        this._volume=new Slider.Slider(0);this._volume.actor.add_style_class_name('ni-slider');audio.add_actor(this._volume.actor);
        this._wireSlider(this._volume,'Output volume',()=>{if(this._state && this._state.capabilities.audio)this._action('volume',String(Math.round(this._volume._value*100)));});
        const steps=this._box();this._less=this._button('−5',null,()=>this._stepVolume(-5));this._more=this._button('+5',null,()=>this._stepVolume(5));steps.add(this._less,{expand:true});steps.add(this._more,{expand:true});audio.add_actor(steps);settings.add_actor(audio);
        const network=this._box('ni-card',true);this._networkName=this._label('Network unavailable','ni-title');network.add_actor(this._networkName);
        const links=this._box();links.add(this._button('Network settings','network-wireless-symbolic',()=>this._action('network-settings')),{expand:true});links.add(this._button('Bluetooth settings','bluetooth-symbolic',()=>this._action('bluetooth-settings')),{expand:true});network.add_actor(links);settings.add_actor(network);
        const quick=this._box('ni-quick');
        this._coffeeButton=this._button('Keep awake','display-brightness-symbolic',()=>this._coffee());this._setEnabled(this._coffeeButton,false);quick.add(this._coffeeButton,{expand:false});
        quick.add(new St.Bin(),{expand:true});
        this._launcherButton=this._button('Apps','view-app-grid-symbolic',()=>this._openExisting('menu@cinnamon.org'));
        this._notificationsButton=this._button('Alerts','preferences-system-notifications-symbolic',()=>this._openExisting('notifications@cinnamon.org'));
        const lock=this._button('','system-lock-screen-symbolic',()=>{this.menu.close();this._run(['/usr/bin/cinnamon-screensaver-command','--lock'],false,()=>{});});lock.accessible_name='Lock screen';this._tip(lock,'Lock screen');
        const power=this._button('','system-shutdown-symbolic',()=>{this.menu.close();Util.spawn(['/usr/bin/cinnamon-session-quit','--power-off']);});power.accessible_name='Power options';this._tip(power,'Power options — opens native confirmation dialog');
        for(const button of [this._launcherButton,this._notificationsButton,lock,power])quick.add_actor(button);this._content.add_actor(quick);
        this._statusErrorLabel=this._label('','ni-status-error');this._statusErrorLabel.clutter_text.set_line_wrap(true);this._statusErrorLabel.set_width(550);this._statusErrorLabel.hide();this._content.add_actor(this._statusErrorLabel);
        this._errorLabel=this._label('','ni-error');this._errorLabel.clutter_text.set_line_wrap(true);this._errorLabel.clutter_text.set_ellipsize(Pango.EllipsizeMode.END);this._errorLabel.set_width(550);this._errorLabel.hide();this._content.add_actor(this._errorLabel);
        this._page('home');
        for(const b of [...this._mediaButtons,this._wifiButton,this._btButton,this._muteButton,this._less,this._more])this._setEnabled(b,false);
    }
    _caption(button,text){button._label.set_text(text);button.accessible_name=text;}
    _wireSlider(slider,name,commit){
        slider.actor.can_focus=false;slider.actor.reactive=false;slider.actor.accessible_name=name;
        const clear=()=>{const id=this._sliderTimers.get(slider);if(id)Mainloop.source_remove(id);this._sliderTimers.delete(slider);};
        slider.connect('value-changed',()=>{if(slider._dragging)return;clear();const id=Mainloop.timeout_add(250,()=>{this._sliderTimers.delete(slider);if(this._alive&&slider.actor.reactive)commit();return false;});this._sliderTimers.set(slider,id);});
        slider.connect('drag-end',()=>{clear();if(this._alive&&slider.actor.reactive)commit();});
    }
    _page(name) {
        for(const [key,actor] of Object.entries(this._pages))actor.visible=key===name;
        for(const b of this._tabs.get_children()) {if(b._page===name)b.add_style_pseudo_class('checked');else b.remove_style_pseudo_class('checked');}
        if(name==='calendar')this._renderCalendar();
    }
    _buildCalendar() {
        const p=this._pages.calendar;const h=this._box('ni-calendar-header');
        h.add_actor(this._button('‹',null,()=>this._moveMonth(-1)));this._monthLabel=this._label('','ni-title');h.add(this._monthLabel,{expand:true,x_fill:false,x_align:St.Align.MIDDLE});h.add_actor(this._button('›',null,()=>this._moveMonth(1)));p.add_actor(h);
        this._calendarGrid=this._box('ni-calendar-grid',true);p.add_actor(this._calendarGrid);p.add_actor(this._button('Today',null,()=>{this._month=new Date();this._renderCalendar();}));this._renderCalendar();
    }
    _moveMonth(delta) {this._month=new Date(this._month.getFullYear(),this._month.getMonth()+delta,1);this._renderCalendar();}
    _renderCalendar() {
        this._calendarGrid.destroy_all_children();const year=this._month.getFullYear(),month=this._month.getMonth();
        this._monthLabel.set_text(this._month.toLocaleDateString(undefined,{month:'long',year:'numeric'}));
        const heads=this._box('ni-calendar-row');for(const name of ['Su','Mo','Tu','We','Th','Fr','Sa'])heads.add(this._label(name,'ni-day-heading'),{expand:true,x_fill:false,x_align:St.Align.MIDDLE});this._calendarGrid.add_actor(heads);
        const start=new Date(year,month,1).getDay(),count=new Date(year,month+1,0).getDate(),today=new Date();
        for(let week=0;week<6;week++){const row=this._box('ni-calendar-row');for(let col=0;col<7;col++){const day=week*7+col-start+1;const valid=day>0&&day<=count;const label=this._label(valid?String(day):'','ni-day');label.set_width(68);if(valid&&day===today.getDate()&&month===today.getMonth()&&year===today.getFullYear())label.add_style_class_name('ni-today');row.add(label,{expand:true,x_fill:false,x_align:St.Align.MIDDLE});}this._calendarGrid.add_actor(row);}
    }
    _clock() {const now=GLib.DateTime.new_now_local();const t=now.format('%H:%M');this._compactClock.set_text(t);this._bigClock.set_text(t);this._date.set_text(now.format('%A, %e %B'));}
    _statusMessage(message){if(!this._alive)return;this._statusErrorLabel.set_text(String(message).slice(0,240));this._statusErrorLabel.visible=!!message;}
    _error(message) {if(!this._alive)return;this._errorLabel.set_text(String(message).slice(0,220));this._errorLabel.show();}
    _schedulePoll(seconds) {if(!this._alive)return;if(this._pollId)Mainloop.source_remove(this._pollId);this._pollId=Mainloop.timeout_add(Math.max(50,seconds*1000),()=>{this._pollId=0;this._snapshot();return false;});}
    _snapshot() {
        if(!this._alive)return;
        if(this._snapshotBusy){this._schedulePoll(this.menu.isOpen?2:3);return;}
        this._snapshotBusy=true;
        this._run(['/usr/bin/python3',this._path+'/backend.py','snapshot'],true,(ok,out,err)=>{
            this._snapshotBusy=false;if(!this._alive)return;
            if(ok){try{const data=JSON.parse(out);this._render(data);}catch(e){this._statusMessage('Snapshot unavailable: '+e.message);}}
            else this._statusMessage('Status unavailable: '+err);
            this._schedulePoll(this.menu.isOpen?2:3);
        });
    }
    _run(argv,snapshot,done) {
        if(!this._alive)return;
        let proc;try{proc=Gio.Subprocess.new(argv,Gio.SubprocessFlags.STDOUT_PIPE|Gio.SubprocessFlags.STDERR_PIPE);}catch(e){done(false,'',e.message);return;}
        const job={proc,cancel:new Gio.Cancellable(),timeout:0};this._processes.add(job);
        job.timeout=Mainloop.timeout_add_seconds(10,()=>{job.timeout=0;try{proc.force_exit();}catch(e){}return false;});
        proc.communicate_utf8_async(null,job.cancel,(p,result)=>{
            if(job.timeout)Mainloop.source_remove(job.timeout);this._processes.delete(job);
            try{const [,out,err]=p.communicate_utf8_finish(result);if(this._alive)done(p.get_successful(),out,err.trim()||'Command did not complete');}
            catch(e){if(this._alive)done(false,'',e.message);}
        });
    }
    _action(...args) {
        if(this._actionBusy){this._error('A control action is still finishing. Please try again.');return;}
        this._actionBusy=true;
        this._run(['/usr/bin/python3',this._path+'/backend.py','action',...args],false,(ok,out,err)=>{
            this._actionBusy=false;
            if(!ok){try{this._error(JSON.parse(out).error||err);}catch(e){this._error(err);}}
            else{this._errorLabel.hide();this._schedulePoll(0);}
        });
    }
    _showCalendar(){this._page('calendar');this.menu.open();}
    _showControls(){this._page('settings');this.menu.open();}
    _stepVolume(delta){if(this._state && Number.isFinite(this._state.volume))this._action('volume',String(Math.max(0,Math.min(100,this._state.volume+delta))));}
    _render(s) {
        if(!s || !s.media || !s.capabilities)throw new Error('Incomplete backend schema');this._state=s;
        const failures=Array.isArray(s.errors)?s.errors:[];this._statusMessage(failures.length?'Some status is unavailable: '+failures.join('; '):'');
        const m=s.media,c=s.capabilities;
        this._compactWeather.set_text(s.weather || 'Weather —');
        this._compactStatus.set_text(m.player ? (m.playing?'▶ ':'Ⅱ ')+(m.title||'Media') : (s.volume===null?'Vol —':Math.round(s.volume)+'%')+' · '+(s.battery===null?'Battery —':Math.round(s.battery)+'%'));
        this._title.set_text(m.player?(m.title||'Untitled'):'Nothing playing');this._artist.set_text(m.player?(m.artist||'Unknown artist'):'Open your existing music player');
        this._mediaButtons[1].accessible_name=m.playing?'Pause':'Play';this._mediaButtons[1]._icon.icon_name=m.playing?'media-playback-pause-symbolic':'media-playback-start-symbolic';for(const b of this._mediaButtons)this._setEnabled(b,!!m.player);
        if(!this._seek._dragging)this._seek.setValue(Math.max(0,Math.min(1,(m.progress||0)/100)));this._seek.actor.reactive=!!m.player&&m.duration>0;this._seek.actor.can_focus=this._seek.actor.reactive;
        this._position.set_text((m.elapsed||'0:00')+' / '+(m.total||'0:00'));
        if(m.cover!==this._coverPath){this._coverPath=m.cover;let path=String(m.cover||'');try{if(path.startsWith('file://'))path=Gio.File.new_for_uri(path).get_path();if(path.startsWith('/')&&GLib.file_test(path,GLib.FileTest.IS_REGULAR)){this._cover.set_child(new St.Icon({gicon:new Gio.FileIcon({file:Gio.File.new_for_path(path)}),icon_size:68}));}else this._cover.set_child(new St.Icon({icon_name:'audio-x-generic-symbolic',icon_type:St.IconType.SYMBOLIC,icon_size:38}));}catch(e){this._cover.set_child(new St.Icon({icon_name:'audio-x-generic-symbolic',icon_size:38}));}}
        this._caption(this._wifiButton,s.wifi===null?'Wi-Fi —':s.wifi?'Wi-Fi on':'Wi-Fi off');this._caption(this._btButton,s.bluetooth===null?'Bluetooth —':s.bluetooth?'Bluetooth on':'Bluetooth off');this._setEnabled(this._wifiButton,c.wifi);this._setEnabled(this._btButton,c.bluetooth);
        this._battery.set_text(s.battery===null?'Battery —':Math.round(s.battery)+'%');this._compactWeather.set_text(s.weather||'Weather unavailable');
        this._cpu.set_text('CPU '+(s.cpu===null?'—':Math.round(s.cpu)+'%'));this._memory.set_text('RAM '+(s.memory===null?'—':Math.round(s.memory)+'%'));this._temp.set_text(s.temperature===null?'Temperature unavailable':Math.round(s.temperature)+'°C');this._temp.set_style(s.temperature!==null&&s.temperature>=85?'color:#e7a29c;':'');this._cpuBar.queue_repaint();this._memoryBar.queue_repaint();
        this._inlineVolumeText.set_text(s.volume===null?'—':Math.round(s.volume)+'%');if(!this._inlineVolume._dragging)this._inlineVolume.setValue((s.volume||0)/100);this._inlineVolume.actor.reactive=!!c.audio;this._inlineVolume.actor.can_focus=!!c.audio;this._volumeText.set_text('Output '+(s.volume===null?'—':Math.round(s.volume)+'%'));if(!this._volume._dragging)this._volume.setValue((s.volume||0)/100);this._volume.actor.reactive=!!c.audio;this._volume.actor.can_focus=!!c.audio;this._caption(this._muteButton,s.muted?'Unmute':'Mute');for(const b of [this._muteButton,this._less,this._more])this._setEnabled(b,c.audio);
        this._networkName.set_text(s.network||'No active network');
        this._setEnabled(this._launcherButton,!!this._existing('menu@cinnamon.org'));this._setEnabled(this._notificationsButton,!!this._existing('notifications@cinnamon.org'));
        this._wave.queue_repaint();this._recordState();
    }
    _paintMeter(actor,value){
        const cr=actor.get_context();const [width,height]=actor.get_surface_size();
        cr.setSourceRGBA(0.129412,0.117647,0.160784,1);cr.rectangle(0,0,width,height);cr.fill();
        if(Number.isFinite(value)){cr.setSourceRGBA(0.894118,0.713725,0.752941,1);cr.rectangle(0,0,width*Math.max(0,Math.min(100,value))/100,height);cr.fill();}
        cr.$dispose();
    }
    _paintWave(){
        const cr=this._wave.get_context();const [width,height]=this._wave.get_surface_size();
        const media=this._state?this._state.media:null;let seed=1;for(const c of (media?media.title:'Nothing'))seed=(seed*31+c.charCodeAt(0))&0x7fffffff;
        const bars=64,progress=media?Math.max(0,Math.min(1,(media.progress||0)/100)):0;
        for(let i=0;i<bars;i++){seed=(Math.imul(seed,1103515245)+12345)&0x7fffffff;const value=.2+.8*(seed/0x7fffffff)*Math.sin(Math.PI*(i+.5)/bars);const h=Math.max(3,value*height);if(i/bars<progress)cr.setSourceRGBA(0.894118,0.713725,0.752941,1);else cr.setSourceRGBA(0.129412,0.117647,0.160784,1);cr.rectangle(i*width/bars,(height-h)/2,Math.max(1,width/bars-3),h);cr.fill();}
        cr.$dispose();
    }
    _existing(uuid){const d=AppletManager.definitions.find(d=>(d.real_uuid||d.uuid)===uuid&&d.applet);return d?d.applet:null;}
    _openExisting(uuid){const a=this._existing(uuid);if(!a||!a.menu){this._error('This native applet is unavailable');return;}this.menu.close();a.menu.toggle();}
    _coffee() {
        if(!this._session||this._coffeePending)return;this._coffeePending=true;this._setEnabled(this._coffeeButton,false);
        if(this._cookie!==null){const cookie=this._cookie;this._session.UninhibitRemote(cookie,(result,error)=>{this._coffeePending=false;if(!error)this._cookie=null;if(this._alive){this._caption(this._coffeeButton,this._cookie===null?'Keep awake':'Awake on');this._setEnabled(this._coffeeButton,true);if(error)this._error(error.message);}});}
        else this._session.InhibitRemote('nothing-island@desktop-theme-studio',0,'User requested keep awake',8,(result,error)=>{this._coffeePending=false;if(!error){const cookie=Array.isArray(result)?result[0]:result;if(!this._alive){this._session.UninhibitRemote(cookie);return;}this._cookie=cookie;}if(this._alive){this._caption(this._coffeeButton,this._cookie===null?'Keep awake':'Awake on');this._setEnabled(this._coffeeButton,true);if(error)this._error(error.message);}});
    }
    _record(){if(!Main.screenRecorder){this._error('Cinnamon recording unavailable');return;}this.menu.close();Main.screenRecorder.toggle_recording();this._recordState();}
    _recordState(){if(this._recordButton&&Main.screenRecorder)this._caption(this._recordButton,Main.screenRecorder.recording?'Stop recording':'Record screen');}
    on_applet_clicked(){this.menu.toggle();}
    on_applet_removed_from_panel(){
        this._alive=false;this._destroyed=true;if(this._pollId)Mainloop.source_remove(this._pollId);if(this._clockId)Mainloop.source_remove(this._clockId);this._pollId=0;this._clockId=0;
        for(const job of this._processes){if(job.timeout)Mainloop.source_remove(job.timeout);job.timeout=0;job.cancel.cancel();try{job.proc.force_exit();}catch(e){}}
        for(const id of this._sliderTimers.values())Mainloop.source_remove(id);this._sliderTimers.clear();
        this._processes.clear();this._snapshotBusy=false;this._actionBusy=false;if(this._recordSignal&&Main.screenRecorder)Main.screenRecorder.disconnect(this._recordSignal);
        if(this._session&&this._cookie!==null){this._session.UninhibitRemote(this._cookie);this._cookie=null;}
        for(const t of this._tips)t.destroy();this._tips=[];if(this.menu)this.menu.destroy();
    }
}
function main(metadata,orientation,panelHeight,instanceId){return new NothingIsland(metadata,orientation,panelHeight,instanceId);}
