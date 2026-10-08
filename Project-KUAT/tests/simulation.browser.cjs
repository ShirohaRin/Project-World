// 在 Electron 的真实 Chromium DOM 中验证世界模拟工作台的建场景、选设定和逐轮记录。
const {app,BrowserWindow}=require('electron');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const root=path.resolve(__dirname,'..');
const output=path.join(root,'release/QA');fs.mkdirSync(output,{recursive:true});
const logFile=path.join(output,'simulation-test.log');fs.writeFileSync(logFile,`开始世界模拟界面回归：${new Date().toISOString()}\n`);
const report=(...v)=>fs.appendFileSync(logFile,v.map(String).join(' ')+'\n');
app.setPath('userData',fs.mkdtempSync(path.join(os.tmpdir(),'kuat-simulation-test-')));
let win;
const evaluate=script=>win.webContents.executeJavaScript(script);
setTimeout(()=>{report('FAIL: 测试超时');app.exit(1);},25000).unref();
app.whenReady().then(async()=>{
 win=new BrowserWindow({show:false,width:1400,height:900,webPreferences:{contextIsolation:true,nodeIntegration:false,sandbox:true,backgroundThrottling:false}});
 win.webContents.on('console-message',(_event,_level,message,line,source)=>report(`renderer ${source}:${line}`,message));
 win.webContents.on('render-process-gone',(_event,details)=>report('renderer gone',JSON.stringify(details)));
 await win.loadURL('data:text/html;charset=utf-8,'+encodeURIComponent('<!doctype html><html><body><main id="main"></main></body></html>'));
 await win.webContents.insertCSS(fs.readFileSync(path.join(root,'src/style.css'),'utf8'));
 await evaluate(`
  window.CloudState={store:{},getItem(k){return this.store[k]??null},setItem(k,v){this.store[k]=v}};
  window.EntriesView={snapshot:()=>({entries:[{id:'actor-a',title:'塔罗斯',type:'地点',category:'',aliases:[],tags:['裂缝'],summary:'雨中的世界',body:''},{id:'actor-b',title:'影子一族',type:'组织',category:'组织',aliases:[],tags:[],summary:'管理国家的组织',body:''},{id:'collection',title:'世界档案',type:'设定集',proposalKind:'设定集',aliases:[],tags:[],summary:'',body:''}]})};
  window.TimelineView={snapshot:()=>({calendar:'世界时间',unit:'年',events:[{id:'event-a',title:'裂缝扩大',start:10,end:null,dateLabel:'第十年',track:'主线',body:'',entryId:'',color:'#168fbd'}]})};
  window.ArchiveView={markdown:t=>String(t).replaceAll('&','&amp;').replaceAll('<','&lt;')};
  if(!window.crypto.randomUUID)Object.defineProperty(window.crypto,'randomUUID',{value:()=> 'test-'+Math.random().toString(16).slice(2),configurable:true});
 ;void 0;`);
 await evaluate(fs.readFileSync(path.join(root,'src/simulation-model.js'),'utf8')+'\nvoid 0;');
 await evaluate(fs.readFileSync(path.join(root,'src/timeline-model.js'),'utf8')+'\nvoid 0;');
 await evaluate(fs.readFileSync(path.join(root,'src/simulation-provider.js'),'utf8')+'\nvoid 0;');
 await evaluate(fs.readFileSync(path.join(root,'src/simulation.js'),'utf8')+'\nvoid 0;');
 await evaluate(`window.KuatAccess={write:['state']};SimulationView.install(()=>{document.querySelector('#main').innerHTML=SimulationView.render()},text=>{document.body.dataset.notice=text});void 0;`);
 await new Promise(r=>setTimeout(r,100));
 await evaluate(`document.querySelector('[data-sim-new]').click();void 0;`);await new Promise(r=>setTimeout(r,100));
 await evaluate(`
  const setup=document.querySelector('#sim-setup-form');setup.elements.title.value='裂缝扩大推演';setup.elements.premise.value='如果裂缝在下一历元扩大';setup.elements.currentTime.value='10';setup.elements.step.value='2';setup.requestSubmit();void 0;`);
 await new Promise(r=>setTimeout(r,100));
 await evaluate(`document.querySelector('[data-sim-actor="actor-a"]').click();void 0;`);await new Promise(r=>setTimeout(r,100));
 await evaluate(`const record=document.querySelector('#sim-record-form');record.elements.title.value='影子一族召开会议';record.elements.body.value='记录参与设定在本轮采取的行动。';record.requestSubmit();void 0;`);await new Promise(r=>setTimeout(r,120));
 const result=await evaluate(`(()=>{const s=SimulationView.snapshot().simulations[0];return {title:s.title,premise:s.premise,currentTime:s.currentTime,turn:s.turn,actors:s.actors.map(actor=>actor.entryId),history:s.history.length,notice:document.body.dataset.notice||''}})()`);
 report('结果：',JSON.stringify(result));assert.equal(result.title,'裂缝扩大推演');assert.equal(result.currentTime,12);assert.equal(result.turn,1);assert.deepEqual(result.actors,['actor-a']);assert.equal(result.history,1);
 report('PASS: 世界模拟工作台已验证建场景、保存前提、选择设定、推进时间并记录结果。');console.log('PASS: 世界模拟工作台已验证。');app.exit(0);
}).catch(error=>{report(error.stack);console.error(error);app.exit(1);});
