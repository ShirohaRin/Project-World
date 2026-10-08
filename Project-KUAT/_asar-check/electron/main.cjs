const {app,BrowserWindow,ipcMain,safeStorage}=require('electron');
const fs=require('node:fs/promises');const path=require('node:path');const crypto=require('node:crypto');
const BASE='https://shiroha-rin.world/kuat-api';let access='',device=crypto.randomUUID(),win;const setupFile=process.argv.find(x=>x.startsWith('--kuat-setup-file='))?.slice(18);
const sessionStore=require('./session-store.cjs')({fs,path,safeStorage,directory:()=>app.getPath('userData')});
async function request(route,method='GET',body,retry=true){
 const response=await fetch(BASE+route,{method,headers:{'Content-Type':'application/json','Authorization':'Bearer '+access,'X-Device-ID':device},body:body===undefined?undefined:JSON.stringify(body),signal:AbortSignal.timeout(20000),cache:'no-store'});
 const value=await response.json();
 if(!response.ok){const error=Error(value.error||'云端请求失败');error.status=response.status;throw error;}return value;
}
function handle(name,fn){ipcMain.handle(name,(event,...args)=>{if(!win||event.sender!==win.webContents||event.senderFrame!==win.webContents.mainFrame)throw Error('不受信任的调用');return fn(...args);});}
handle('cloud:login',async(username,password)=>{const s=await request('/login','POST',{username,password},false);access=s.access_token;try{await sessionStore.save({access,device,expires:s.expires_at});}catch(e){access='';throw e;}return {ok:true};});
handle('cloud:resume',async()=>{const saved=await sessionStore.load();if(!saved)return false;access=saved.access;device=saved.device;try{await request('/workspace');return true;}catch(e){if(e.status===401){access='';await sessionStore.clear();return false;}throw e;}});
handle('cloud:logout',async()=>{await request('/logout','POST',{});await sessionStore.clear();access='';return {ok:true};});
handle('cloud:profile',()=>request('/profile'));handle('cloud:update-profile',(nickname,signature,avatar)=>request('/profile','PUT',{nickname,signature,avatar}));
handle('cloud:load',()=>request('/workspace'));
handle('cloud:save',(name,data,revision)=>{if(!['world','entries','timeline','state','archive'].includes(name))throw Error('无效档案');return request('/documents/'+name,'PUT',{data,revision});});
handle('cloud:setup-available',async()=>!!setupFile&&await fs.access(setupFile).then(()=>true,()=>false));
handle('cloud:setup',async(username,password)=>{if(!setupFile)throw Error('此设备没有管理员初始化授权');const code=(await fs.readFile(setupFile,'utf8')).trim();await request('/setup','POST',{code,username,password},false);await fs.unlink(setupFile);return {ok:true};});
handle('cloud:accounts',()=>request('/accounts'));
handle('cloud:issue',(username,password,role)=>request('/accounts','POST',{username,password,role}));
handle('cloud:update-account',(id,active,password,rank)=>request('/accounts','PUT',{id,active,password,rank}));
handle('cloud:password',async(old,password)=>{await request('/password','POST',{old,password});await sessionStore.clear();access='';return {ok:true};});
app.whenReady().then(()=>{win=new BrowserWindow({icon:path.join(__dirname,'../assets/kuat-icon-ring.png'),width:1480,height:980,minWidth:960,minHeight:680,title:'K.U.A.T · Project World',backgroundColor:'#f5f6f8',webPreferences:{preload:path.join(__dirname,'preload.cjs'),partition:'kuat-cloud-memory',contextIsolation:true,nodeIntegration:false,sandbox:true}});win.setMenuBarVisibility(false);win.loadFile(path.join(__dirname,'../index.html'));win.webContents.setWindowOpenHandler(()=>({action:'deny'}));win.webContents.on('will-navigate',(e,url)=>{const entry=require('node:url').pathToFileURL(path.join(__dirname,'../index.html')).href;if(url!==entry)e.preventDefault();});});
app.on('window-all-closed',()=>{access='';app.quit();});
