(function(root){
const initial={schemaVersion:1,name:'Project World',subtitle:'创造、构建、模拟、完善一个完整的架空世界，并映射到现实世界。',background:'Project World，世界构建项目。\n\n世界观、故事、科研、软件平台与网站的建设，都行进在这一长期目标之下。我们在这里整理世界的背景、连接设定，并逐步构建一个可以持续生长的世界。',concept:'元素，构成世界的基本组成，物质是元素的波动表象。',tags:['科幻','魔幻','架空世界','星际'],updatedAt:null,changes:[],pendingSync:false};
function validate(value){if(!value||value.schemaVersion!==1||typeof value.name!=='string'||!value.name.trim()||!Array.isArray(value.tags)||!value.tags.every(x=>typeof x==='string')||!Array.isArray(value.changes))throw Error('档案格式不正确');for(const k of ['subtitle','background','concept'])if(typeof value[k]!=='string')throw Error('缺少文本字段');return value;}
function update(old,fields){return validate({...old,...fields,name:fields.name.trim(),updatedAt:new Date().toISOString(),pendingSync:true,changes:[{at:new Date().toISOString(),text:'更新了世界概览'} ,...old.changes].slice(0,30)});}
const api={initial,validate,update}; if(typeof module!=='undefined')module.exports=api;else root.WorldModel=api;
})(globalThis);
