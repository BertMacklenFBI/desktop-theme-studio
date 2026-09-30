const Applet=imports.ui.applet;
const St=imports.gi.St;
const GLib=imports.gi.GLib;
const ByteArray=imports.byteArray;
const Cairo=imports.cairo;
const UUID='gnu-darwin-workstation-monitor@desktop-theme-studio';
const Metrics=require('./metrics');
const Style=require('./style');
function read(path){try{let [ok,bytes]=GLib.file_get_contents(path);return ok?ByteArray.toString(bytes):null;}catch(e){return null;}}
function color(cr,hex){cr.setSourceRGB(parseInt(hex.slice(1,3),16)/255,parseInt(hex.slice(3,5),16)/255,parseInt(hex.slice(5,7),16)/255);}
class Monitor extends Applet.Applet {
    constructor(metadata,orientation,panelHeight,instanceId){
        super(orientation,panelHeight,instanceId);
        this.setAllowedLayout(Applet.AllowedLayout.HORIZONTAL);
        this.actor.set_style_class_name('gnu-workstation-monitor');
        this.actor.set_style('padding:0px; margin:0px; border:0px; spacing:0px; background-color:transparent;');
        this.actor.set_size(256*global.ui_scale,64*global.ui_scale);
        this.sampler=new Metrics.Sampler();this.sourceId=0;this.disposed=true;this.tiles=[];
        for(let key of ['CPU','MEM','RX','TX']){
            let area=new St.DrawingArea({style_class:'gnu-workstation-monitor-tile',width:64*global.ui_scale,height:64*global.ui_scale});
            area.connect('repaint',()=>this.paint(area,key));this.actor.add_child(area);this.tiles.push(area);
        }
        this.set_applet_tooltip('Period-style resource monitor — waiting for a sample');
    }
    on_applet_added_to_panel(){
        if(this.sourceId)return;
        this.disposed=false;this.sample();
        this.sourceId=GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT,1,()=>{
            if(this.disposed){this.sourceId=0;return GLib.SOURCE_REMOVE;}
            this.sample();return GLib.SOURCE_CONTINUE;
        });
    }
    on_applet_removed_from_panel(){
        this.disposed=true;
        if(this.sourceId){GLib.source_remove(this.sourceId);this.sourceId=0;}
    }
    sample(){
        if(this.disposed)return;
        let s=this.sampler.update({stat:read('/proc/stat'),meminfo:read('/proc/meminfo'),netdev:read('/proc/net/dev'),route:read('/proc/net/route'),ipv6route:read('/proc/net/ipv6_route')},GLib.get_monotonic_time()/1000000);
        let val=n=>n===null?'unavailable':n.toFixed(2),bytes=n=>n===null?'unavailable':n.toLocaleString()+' bytes';
        let rate=n=>n===null?'unavailable':(n/1024).toFixed(2)+' KiB/s ('+n.toFixed(2)+' B/s)';
        this.set_applet_tooltip('CPU '+val(s.cpuPercent)+'% (first eight /proc/stat counters; guest columns omitted)\nMemory '+val(s.memoryPercent)+'%: '+bytes(s.memoryUsedBytes)+' / '+bytes(s.memoryTotalBytes)+'; MemTotal − MemAvailable\nInterface '+(s.interfaceName||'unavailable')+'\nRX '+rate(s.rxBytesPerSec)+'; TX '+rate(s.txBytesPerSec)+'\nNetwork graph ceiling '+bytes(s.networkScale)+'/s (rolling54 seconds, minimum1KiB/s)\n'+s.reason);
        for(let tile of this.tiles)tile.queue_repaint();
    }
    getMetricsSnapshot(){return Object.assign(this.sampler.getSnapshot(),{disposed:this.disposed,sourceId:this.sourceId});}
    paint(area,key){
        let cr=area.get_context(),[w,h]=area.get_surface_size();cr.scale(w/64,h/64);
        const rect=(x,y,w,h,c)=>{color(cr,c);cr.rectangle(x,y,w,h);cr.fill();};
        rect(0,0,64,64,Style.colors.bezel);rect(0,0,64,1,Style.colors.light);rect(0,0,1,64,Style.colors.light);rect(63,0,1,64,Style.colors.edge);rect(0,63,64,1,Style.colors.edge);rect(4,4,56,56,Style.colors.inset);
        let s=this.sampler.getSnapshot(),field={CPU:'cpuPercent',MEM:'memoryPercent',RX:'rxBytesPerSec',TX:'txBytesPerSec'}[key],value=s[field]===undefined?null:s[field],warn=(key==='CPU'||key==='MEM')&&value!==null&&value>=90;
        let ink=warn?Style.colors.warning:Style.colors.foreground;
        color(cr,ink);cr.selectFontFace('Liberation Mono',Cairo.FontSlant.NORMAL,Cairo.FontWeight.BOLD);cr.setFontSize(9);cr.moveTo(7,15);cr.showText(key+(warn?'!':''));
        cr.selectFontFace('Liberation Mono',Cairo.FontSlant.NORMAL,Cairo.FontWeight.NORMAL);cr.setFontSize(10);cr.moveTo(7,29);cr.showText(value===null?'--':(key==='CPU'||key==='MEM')?Math.round(value)+'%':Metrics.formatRate(value));
        let hist=this.sampler.history[{CPU:'cpu',MEM:'memory',RX:'rx',TX:'tx'}[key]],max=(key==='CPU'||key==='MEM')?100:(s.networkScale||1024),trace=Style.colors[{CPU:'cpu',MEM:'memory',RX:'network',TX:'network'}[key]];
        for(let i=0;i<hist.length;i++){let height=Math.max(0,Math.min(22,Math.round(hist[i]/max*22)));if(height)rect(5+54-hist.length+i,57-height,1,height,trace);}
        cr.$dispose();
    }
}
function main(metadata,orientation,panelHeight,instanceId){return new Monitor(metadata,orientation,panelHeight,instanceId);}
