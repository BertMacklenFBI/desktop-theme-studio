/* Pure sampling core. No GI, files, timers, side effects or window dependencies. */
function counter(s) { let n=Number(s); return Number.isSafeInteger(n)&&n>=0?n:null; }
var parseCPU = function(text) {
    if (typeof text!=='string') return null;
    let line=text.split('\n').find(l=>/^cpu\s/.test(l));
    if (!line) return null;
    let a=line.trim().split(/\s+/).slice(1).map(counter);
    if(a.length<8 || a.slice(0,8).some(x=>x===null))return null;
    // user/nice include guest time: omit guest columns, never subtract them.
    let total=a.slice(0,8).reduce((x,y)=>x+y,0);
    return Number.isSafeInteger(total)?{total,idle:a[3]+a[4],fields:a.slice(0,8)}:null;
};
var parseMemory = function(text) {
    if(typeof text!=='string')return null;
    let get=k=>{let m=text.match(new RegExp('^'+k+':\\s+(\\d+)\\s+kB\\s*$','m'));return m?counter(m[1]):null;};
    let total=get('MemTotal'),available=get('MemAvailable');
    if(total===null || total<=0 || available===null || available>total)return null;
    return {percent:100*(total-available)/total,usedBytes:(total-available)*1024,totalBytes:total*1024};
};
var parseDevices = function(text) {
    let result={}; if(typeof text!=='string')return result;
    for(let line of text.split('\n')) {
        let pos=line.indexOf(':'); if(pos<0)continue;
        let name=line.slice(0,pos).trim(),a=line.slice(pos+1).trim().split(/\s+/);
        let rx=counter(a[0]),tx=counter(a[8]);
        if(name && a.length>=16 && rx!==null && tx!==null)result[name]={rx,tx};
    }
    return result;
};
var selectInterface = function(ipv4,ipv6,devices) {
    let candidates=[];
    if(typeof ipv4==='string')for(let l of ipv4.split('\n')) {
        let a=l.trim().split(/\s+/);if(a.length<8)continue;
        let flags=parseInt(a[3],16),metric=counter(a[6]);
        if(a[1]==='00000000' && a[7]==='00000000' && (flags&1) && !(flags&0x200) && metric!==null && a[0]!=='lo' && devices[a[0]])candidates.push({name:a[0],metric});
    }
    if(!candidates.length && typeof ipv6==='string')for(let l of ipv6.split('\n')) {
        let a=l.trim().split(/\s+/);if(a.length<10)continue;
        let flags=parseInt(a[8],16),metric=parseInt(a[5],16),name=a[9];
        if(/^0{32}$/.test(a[0]) && a[1]==='00' && a[3]==='00' && (flags&1) && !(flags&0x200) && Number.isSafeInteger(metric) && name!=='lo' && devices[name])candidates.push({name,metric});
    }
    candidates.sort((a,b)=>a.metric-b.metric || (a.name<b.name?-1:a.name>b.name?1:0));
    return candidates.length?candidates[0].name:null;
};
var formatRate = function(rate) {
    if(rate===null || !Number.isFinite(rate))return '--';
    if(rate===0)return '0B/s';
    let suffix=['B/s','K/s','M/s','G/s'],i=0,n=rate;
    // Promote before a 4-digit integer can exceed the six-character tile field.
    while(n>=999.5 && i<3){n/=1024;i++;}
    if(i===3 && n>=999.5)return '>999G';
    let roundedTenth=Math.round(n*10)/10;
    return (i>0&&roundedTenth<10?roundedTenth.toFixed(1):Math.round(n).toString())+suffix[i];
};
var Sampler = class Sampler {
    constructor(){this.sequence=0;this.previousCPU=null;this.previousNet=null;this.lastTime=null;this.interfaceName=null;this.history={cpu:[],memory:[],rx:[],tx:[]};this.snapshot={sequence:0,timestamp:null,cpuPercent:null,memoryPercent:null,rxBytesPerSec:null,txBytesPerSec:null,interfaceName:null};}
    update(input,time) {
        let cpu=parseCPU(input.stat),mem=parseMemory(input.meminfo),devices=parseDevices(input.netdev);
        let iface=selectInterface(input.route,input.ipv6route,devices),dt=this.lastTime===null?null:time-this.lastTime;
        let validTime=Number.isFinite(time)&&dt!==null&&dt>0&&dt<=5;
        let cpuPercent=null,rx=null,tx=null;
        if(cpu && this.previousCPU && validTime){
            let total=cpu.total-this.previousCPU.total,idle=cpu.idle-this.previousCPU.idle;
            let reset=cpu.fields.some((n,i)=>n<this.previousCPU.fields[i]);
            if(!reset && total>0 && idle>=0 && idle<=total)cpuPercent=100*(total-idle)/total;
        }
        if(iface!==this.interfaceName){this.history.rx=[];this.history.tx=[];this.previousNet=null;}
        if(iface && this.previousNet && validTime){
            let n=devices[iface],r=n.rx-this.previousNet.rx,t=n.tx-this.previousNet.tx;
            if(r>=0 && t>=0){rx=r/dt;tx=t/dt;}
        }
        this.previousCPU=cpu;this.previousNet=iface?devices[iface]:null;this.interfaceName=iface;this.lastTime=Number.isFinite(time)?time:null;
        this.snapshot={sequence:++this.sequence,timestamp:Number.isFinite(time)?time:null,cpuPercent,memoryPercent:mem?mem.percent:null,rxBytesPerSec:rx,txBytesPerSec:tx,interfaceName:iface,memoryUsedBytes:mem?mem.usedBytes:null,memoryTotalBytes:mem?mem.totalBytes:null};
        for(let [key,value] of [['cpu',cpuPercent],['memory',mem?mem.percent:null],['rx',rx],['tx',tx]]){
            if(value===null)this.history[key]=[];
            else{this.history[key].push(value);if(this.history[key].length>54)this.history[key].shift();}
        }
        this.snapshot.networkScale=Math.max(1024,...this.history.rx,...this.history.tx);
        this.snapshot.reason=!cpu?'CPU source unavailable':!validTime?'CPU/network warming up or timing reset':!iface?'No usable default-route interface':rx===null?'Network baseline/counter reset':'Live /proc sample';
        return this.getSnapshot();
    }
    getSnapshot(){return Object.assign({},this.snapshot);}
};
