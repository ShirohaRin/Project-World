(function(root){
 const types=['人物','组织','地点','概念','事件','其他'];const statuses=['待核对','草稿','已确认'];
 function validate(data){
  if(!data||data.schemaVersion!==1||!Array.isArray(data.entries)||data.entries.length>5000)throw Error('词条文件格式不正确');
  const ids=new Set();for(const e of data.entries){
   if(!e||typeof e.id!=='string'||!e.id||ids.has(e.id))throw Error('词条 ID 重复或缺失');ids.add(e.id);
   for(const k of ['title','summary','body','notes','visibility'])if(typeof e[k]!=='string'||e[k].length>200000)throw Error('词条文本不合法');
   if(!e.title.trim()||e.title.length>120||!types.includes(e.type)||!statuses.includes(e.status))throw Error('词条名称、类型或状态不合法');
   for(const k of ['aliases','tags','related'])if(!Array.isArray(e[k])||!e[k].every(s=>typeof s==='string'))throw Error('词条列表字段不合法');
   if(!Array.isArray(e.sources)||!e.sources.every(s=>s&&typeof s.documentId==='string'&&typeof s.heading==='string'&&typeof s.role==='string'))throw Error('词条来源不合法');
  }return data;
 }
 function update(data,id,fields){const old=data.entries.find(e=>e.id===id);const at=new Date().toISOString();const entry={...(old||{id,sources:[],revision:0}),...fields,id,title:fields.title.trim(),sources:old?.sources||[],updatedAt:at,pendingSync:true,revision:(old?.revision||0)+1};const next={...data,entries:old?data.entries.map(e=>e.id===id?entry:e):[entry,...data.entries]};return validate(next);}
 function search(data,query,type='全部',status='全部'){const terms=query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);return data.entries.filter(e=>(type==='全部'||e.type===type)&&(status==='全部'||e.status===status)&&terms.every(q=>[e.title,...e.aliases,...e.tags,e.summary,e.body,e.notes].join('\n').toLocaleLowerCase().includes(q)));}
 function reconcile(stored,seed){validate(stored);const next=structuredClone(stored);for(const fresh of seed.entries.filter(e=>e.seedRevision)){const i=next.entries.findIndex(e=>e.id===fresh.id);if(i<0){next.entries.push(structuredClone(fresh));continue;}const old=next.entries[i];if(old.seedRevision===fresh.seedRevision)continue;if(!old.updatedAt&&!old.revision){next.entries[i]=structuredClone(fresh);}else{old.sources=[...old.sources,...fresh.sources.filter(s=>!old.sources.some(x=>x.documentId===s.documentId))];old.notes+='\n2026-10-04 作者已补充夸特结构与级别，见最新来源；本机编辑正文保留，需对照更新。';old.seedRevision=fresh.seedRevision;old.status='待核对';}}return validate(next);}
 const api={types,statuses,validate,update,search,reconcile};if(typeof module!=='undefined')module.exports=api;else root.EntryModel=api;
})(globalThis);
