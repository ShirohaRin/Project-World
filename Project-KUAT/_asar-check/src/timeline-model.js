(function(root){
 const initial={schemaVersion:1,storeRevision:0,calendar:'世界时间',unit:'年',events:[],pendingSync:false};
 function validate(d){
  if(!d||d.schemaVersion!==1||!Array.isArray(d.events)||d.events.length>5000)throw Error('时间线格式不正确');
  for(const k of ['calendar','unit'])if(typeof d[k]!=='string'||!d[k].trim()||d[k].length>60)throw Error('历法名称和单位不能为空，最多60字');
  if(!Number.isInteger(d.storeRevision)||d.storeRevision<0)throw Error('时间线版本无效');
  const ids=new Set();for(const e of d.events){
   if(!e||typeof e.id!=='string'||!e.id||ids.has(e.id))throw Error('事件编号无效');ids.add(e.id);
   for(const k of ['title','track','dateLabel','body','entryId','color'])if(typeof e[k]!=='string')throw Error('事件字段缺失');
   if(!e.title.trim()||e.title.length>120||!e.track.trim()||e.track.length>60||e.dateLabel.length>120||e.body.length>200000||e.entryId.length>200||!/^#[0-9a-f]{6}$/i.test(e.color))throw Error('事件内容不合法');
   if(e.importance!==undefined&&!['核心','重要','普通'].includes(e.importance))throw Error('事件重要程度无效');
   if(e.level!==undefined&&(!Number.isInteger(e.level)||e.level<1||e.level>5))throw Error('事件级别应为1到5');
   for(const k of ['start','end'])if(e[k]!==null&&(!Number.isFinite(e[k])||Math.abs(e[k])>1e9))throw Error('时间坐标应为 -10亿 到 10亿之间的数值');
   if(e.end!==null&&(e.start===null||e.end<e.start))throw Error('结束时间不能早于开始时间，且必须填写开始时间');
  }return d;
 }
 function update(d,event){const next={...d,events:d.events.some(e=>e.id===event.id)?d.events.map(e=>e.id===event.id?event:e):[...d.events,event],pendingSync:true,updatedAt:new Date().toISOString()};return validate(next);}
 function ordered(events){return [...events].sort((a,b)=>(a.start??Infinity)-(b.start??Infinity)||a.title.localeCompare(b.title,'zh-CN'));}
 function extent(events){const dated=events.filter(e=>e.start!==null);if(!dated.length)return {min:0,max:10};let min=Math.min(...dated.map(e=>e.start)),max=Math.max(...dated.map(e=>e.end??e.start));const pad=Math.max((max-min)*.08,1);return {min:min-pad,max:max+pad};}
 function rank(e){return ['核心','重要','普通'].indexOf(e.importance??'普通')*5+(e.level??3)-1;}
 function visibleAtScale(events,position,gap=220){
  const sorted=ordered(events.filter(e=>e.start!==null));
  for(let cutoff=14;cutoff>=0;cutoff--){const candidates=sorted.filter(e=>rank(e)<=cutoff),ends=[-Infinity,-Infinity];let fits=true;
   for(let i=0;i<candidates.length;i++){const x=position(candidates[i]),side=i%2;if(x<ends[side]){fits=false;break;}ends[side]=x+gap;}
   if(fits)return candidates.map(e=>e.id);
  }return [];
 }
 const api={initial,validate,update,ordered,extent,rank,visibleAtScale};if(typeof module!=='undefined')module.exports=api;else root.TimelineModel=api;
})(globalThis);
