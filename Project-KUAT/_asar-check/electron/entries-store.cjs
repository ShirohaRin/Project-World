const {readFile,mkdir,writeFile,rename}=require('node:fs/promises');
const path=require('node:path');const {validate}=require('../src/entries-model.js');
function createEntryStore(file){let queue=Promise.resolve();async function load(){try{return validate(JSON.parse(await readFile(file,'utf8')));}catch(e){if(e.code==='ENOENT')return null;throw e;}}
 function save(data,expectedRevision){const work=queue.then(async()=>{validate(data);const current=await load();if((current?.storeRevision||0)!==expectedRevision)throw Error('其他窗口已修改词条，请重新打开程序后再编辑');const next={...data,storeRevision:expectedRevision+1};const json=JSON.stringify(next,null,2);if(Buffer.byteLength(json)>15000000)throw Error('词条库超过15MB');await mkdir(path.dirname(file),{recursive:true});await writeFile(file+'.tmp',json,'utf8');await rename(file+'.tmp',file);return next;});queue=work.catch(()=>{});return work;}
 return {load,save};}
module.exports={createEntryStore};
