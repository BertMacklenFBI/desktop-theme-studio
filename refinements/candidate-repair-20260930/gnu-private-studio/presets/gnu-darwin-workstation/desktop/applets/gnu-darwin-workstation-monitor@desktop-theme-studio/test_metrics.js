const GLib=imports.gi.GLib,BA=imports.byteArray;
imports.searchPath.unshift(GLib.get_current_dir());const M=imports.metrics;let count=0;
function eq(a,b,m){count++;if(JSON.stringify(a)!==JSON.stringify(b))throw Error(m+': '+JSON.stringify(a));}
const stat=(u,i,g=0)=>`cpu ${u} 0 10 ${i} 0 0 0 0 ${g} 0\n`;
const mem='MemTotal: 1000 kB\nMemAvailable: 400 kB\n';
const dev=(r,t,n='eth0')=>`${n}: ${r} 0 0 0 0 0 0 0 ${t} 0 0 0 0 0 0 0\n`;
const route=(n='eth0',m=10,f='0003')=>`${n} 00000000 00000000 ${f} 0 0 ${m} 00000000 0 0 0\n`;
const raw=(u,i,r,t,n='eth0',g=0)=>({stat:stat(u,i,g),meminfo:mem,netdev:dev(r,t,n),route:route(n),ipv6route:''});
eq(M.parseCPU(stat(200,100,150)).total,310,'guest not doublecounted orsubtracted');
eq(M.parseCPU('cpu 1 2 bad 4 5 6 7 8'),null,'badcpu');
eq(M.parseMemory(mem),{percent:60,usedBytes:614400,totalBytes:1024000},'availabledefinition');
eq(M.parseMemory('MemTotal: 1000 kB\nMemFree: 300 kB\n'),null,'nofallback');
eq(M.parseMemory('MemTotal: 0 kB\nMemAvailable: 0 kB\n'),null,'zerototal');
eq(M.parseMemory('MemTotal: 2 kB\nMemAvailable: 3 kB\n'),null,'invalidavailable');
eq(M.parseMemory('MemTotal: 2 kB\nMemAvailable: 0 kB\n').percent,100,'fullmemory');
let ds=M.parseDevices(dev(100,200)+dev(5,7,'eth1'));
eq(M.selectInterface(route('eth0',20)+route('eth1',10),'',ds),'eth1','metric');
eq(M.selectInterface(route('eth1')+route('eth0'),'',ds),'eth0','tie');
eq(M.selectInterface(route('eth0',1,'0201'),'',ds),null,'reject');
eq(M.selectInterface(route('eth0',1,'0002'),'',ds),null,'down');
eq(M.selectInterface(route('lo'),'',{lo:{rx:0,tx:0}}),null,'loopback');
let v6='00000000000000000000000000000000 00 00000000000000000000000000000000 00 00000000000000000000000000000000 0000000a 0 0 00000003 eth1\n';
eq(M.selectInterface('',v6,ds),'eth1','ipv6');eq(M.selectInterface(route(),v6,ds),'eth0','ipv4preferred');
let s=new M.Sampler(),a=s.update(raw(200,100,100,200,'eth0',150),1);
eq([a.cpuPercent,a.rxBytesPerSec,a.memoryPercent],[null,null,60],'baseline');
a=s.update(raw(260,140,1124,2248,'eth0',210),2);eq([a.cpuPercent,a.rxBytesPerSec,a.txBytesPerSec],[60,1024,2048],'guestheavybusy60pct');
a=s.update(raw(260,240,1124,2248),3);eq([a.cpuPercent,a.rxBytesPerSec,a.txBytesPerSec],[0,0,0],'realzero');
a=s.update(raw(270,250,1,2),4);eq(a.rxBytesPerSec,null,'counterreset');
a=s.update(raw(280,260,1000,2000,'eth1'),5);eq([a.interfaceName,a.rxBytesPerSec,s.history.rx.length],['eth1',null,0],'ifacereset');
a=s.update(raw(300,280,2000,3000,'eth1'),12);eq([a.cpuPercent,a.rxBytesPerSec,a.memoryPercent],[null,null,60],'suspend');
a=s.update(raw(400,380,3000,4000,'eth1'),12);eq([a.cpuPercent,a.rxBytesPerSec],[null,null],'sametime');
a=s.update({stat:null,meminfo:null,netdev:null,route:null,ipv6route:null},13);eq([a.cpuPercent,a.memoryPercent,a.rxBytesPerSec,a.interfaceName],[null,null,null,null],'readfailure');
s.update(raw(10,10,100,100),14);a=s.update(raw(9,30,200,200),15);eq(a.cpuPercent,null,'cpuindividualreset');
for(let i=0;i<80;i++)s.update(raw(100+i,100+i,1000+i*100,1000+i*200),20+i);
eq([s.history.cpu.length,s.history.rx.length],[54,54],'historybounded');
a=s.getSnapshot();a.sequence=-8;eq(s.getSnapshot().sequence>0,true,'copiedsnapshot');
eq(M.formatRate(null),'--','missingformat');eq(M.formatRate(0),'0B/s','zeroformat');eq(M.formatRate(1024),'1.0K/s','binaryformat');eq(M.formatRate(1000*1024**3),'>999G','hugeformat');
eq(M.formatRate(1000),'1.0K/s','1000bytespromotes');
eq(M.formatRate(1023),'1.0K/s','1023bytespromotes');
eq(M.formatRate(9.999*1024),'10K/s','decimalboundaryfits');
eq(M.formatRate(1000*1024),'1.0M/s','1000KiBpromotes');
for(let rate of [999,999.49,999.5,1000,1023,1024,9.949*1024,9.999*1024,999.5*1024,1000*1024,1000*1024**2,999.5*1024**3])eq(M.formatRate(rate).length<=6,true,'sixchars '+rate);
// Actual applet class with local mocks: no GUI, timer or file mutation.
let timers=new Map(),next=1,now=1,removed=[];
class Actor{set_style_class_name(){}set_style(){}set_size(w,h){this.size=[w,h];}add_child(){}connect(){}queue_repaint(){}}
class Base{constructor(){this.actor=new Actor();}setAllowedLayout(){}set_applet_tooltip(){}}
let fakeGLib={PRIORITY_DEFAULT:0,SOURCE_REMOVE:false,SOURCE_CONTINUE:true,get_monotonic_time:()=>now*1e6,file_get_contents:p=>[true,BA.fromString({'/proc/stat':stat(100+now,200+now),'/proc/meminfo':mem,'/proc/net/dev':dev(1000+now,2000+now),'/proc/net/route':route(),'/proc/net/ipv6_route':''}[p])],timeout_add_seconds:(priority,interval,fn)=>{eq(interval,1,'1hz');let id=next++;timers.set(id,fn);return id;},source_remove:id=>{removed.push(id);timers.delete(id);}};
let fake={ui:{applet:{Applet:Base,AllowedLayout:{HORIZONTAL:0}}},gi:{St:{DrawingArea:Actor},GLib:fakeGLib},byteArray:BA,cairo:{}};
let [,bytes]=GLib.file_get_contents('applet.js');let Klass=new Function('imports','require','global',BA.toString(bytes)+'\nreturn Monitor;')(fake,n=>n==='./metrics'?M:{colors:{}},{ui_scale:1});
let app=new Klass({},0,64,55);eq(app.actor.size,[256,64],'256x64');eq(app.getMetricsSnapshot().sourceId,0,'constructornotimer');app.on_applet_added_to_panel();let id=app.sourceId,cb=timers.get(id);eq(app.getMetricsSnapshot().sequence,1,'addedbaseline');app.on_applet_added_to_panel();eq(timers.size,1,'idempotentadded');now=2;cb();eq(app.getMetricsSnapshot().sequence,2,'timeradvances');app.on_applet_removed_from_panel();eq([app.disposed,app.sourceId,timers.size],[true,0,0],'removedcancel');now=3;cb();eq(app.getMetricsSnapshot().sequence,2,'retainedcbfrozen');app.sample();eq(app.getMetricsSnapshot().sequence,2,'disposednoop');eq(removed,[id],'exactcancel');
let nonfinite=new M.Sampler();a=nonfinite.update(raw(1,1,1,1),Infinity);eq([a.timestamp,a.cpuPercent,a.rxBytesPerSec],[null,null,null],'nonfinite time');
a=nonfinite.update(raw(2,2,2,2),1);eq(a.cpuPercent,null,'nonfinite recovery baseline');
// Cairo raster surface exercises real paint APIs without opening a display/window.
const Cairo=imports.cairo;fake.cairo=Cairo;
Klass=new Function('imports','require','global',BA.toString(bytes)+'\nreturn Monitor;')(fake,n=>n==='./metrics'?M:imports.style,{ui_scale:1});
app=new Klass({},0,64,56);let surface=new Cairo.ImageSurface(Cairo.Format.ARGB32,64,64);
for(let key of ['CPU','MEM','RX','TX']){app.paint({get_context:()=>new Cairo.Context(surface),get_surface_size:()=>[64,64]},key);eq(true,true,'Cairo paint '+key);}
print(JSON.stringify({pass:true,assertions:count,scope:'pure parser/delta and actual applet lifecycle mocked; no GUI or realtimer'},null,2));
