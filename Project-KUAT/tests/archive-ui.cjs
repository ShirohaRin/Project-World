const {app,BrowserWindow,ipcMain}=require('electron');const fs=require('fs');const path=require('path');
app.setPath('userData',path.resolve(__dirname,'../release/ArchiveTestProfile'));
ipcMain.handle('entries:load',()=>null);
ipcMain.handle('world:load',()=>null);ipcMain.handle('world:save',()=>true);
app.whenReady().then(async()=>{try{
 const win=new BrowserWindow({width:1480,height:980,show:false,webPreferences:{preload:path.resolve(__dirname,'../electron/preload.cjs'),contextIsolation:true,sandbox:true}});
 await win.loadFile(path.resolve(__dirname,'../index.html'));
 const result=await win.webContents.executeJavaScript(`(()=>{
 const check=(b,m)=>{if(!b)throw Error(m)};document.querySelector('[data-page="archive"]').click();
 check(document.querySelectorAll('.archive-card').length===27,'27 cards');
 const search=q=>{let i=document.querySelector('#archive-search');i.value=q;i.dispatchEvent(new Event('input',{bubbles:true}));};
 search('西斯塔');check(document.querySelectorAll('.archive-card').length>0,'full text search');
 search('不存在的检索词xyz');check(document.querySelectorAll('.archive-card').length===0,'empty search');
 search('WORLD');document.querySelector('[data-document]').click();check(!document.querySelector('.archive-prose'),'author folded');
 document.querySelector('#archive-author').click();check(document.querySelector('.archive-prose'),'author revealed');
 document.querySelector('#archive-author').click();check(!document.querySelector('.archive-prose'),'author refolded');
 search('');const select=document.querySelector('#archive-group');select.value='人物与组织';select.dispatchEvent(new Event('change',{bubbles:true}));check(document.querySelectorAll('.archive-card').length===12,'category filter');
 document.querySelector('[data-document]').click();check(document.querySelector('.archive-prose h2'),'reader');
 document.querySelector('#archive-raw').click();check(document.querySelector('.archive-raw'),'raw view');document.querySelector('#archive-raw').click();
 return 'Archive search, filter, reader, raw view and author folding passed';})()`);
 await new Promise(r=>setTimeout(r,400));fs.writeFileSync(path.resolve(__dirname,'../Design/archive-implemented.png'),(await win.webContents.capturePage()).toPNG());
 fs.writeFileSync(path.resolve(__dirname,'../release/archive-test-result.txt'),result);console.log(result);app.exit(0);
 }catch(e){fs.writeFileSync(path.resolve(__dirname,'../release/archive-test-result.txt'),String(e.stack));console.error(e);app.exit(1);}});
