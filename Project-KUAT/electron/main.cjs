const {app,BrowserWindow,ipcMain,safeStorage}=require('electron');
const fs=require('node:fs/promises');const path=require('node:path');const crypto=require('node:crypto');const {Readable}=require('node:stream');const {pipeline}=require('node:stream/promises');const {createWriteStream}=require('node:fs');const {spawn}=require('node:child_process');
const {isNewer,validateManifest}=require('../src/update-model.js');
const BASE='https://shiroha-rin.world/kuat-api';const UPDATE_MANIFEST_URL=process.env.KUAT_UPDATE_MANIFEST_URL||BASE+'/update/latest.json';const UPDATE_HOSTS=new Set(['shiroha-rin.world',new URL(UPDATE_MANIFEST_URL).host]);let access='',device=crypto.randomUUID(),win,pendingUpdatePath='';const setupFile=process.argv.find(x=>x.startsWith('--kuat-setup-file='))?.slice(18);
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
async function fetchUpdateManifest(){
 const response=await fetch(UPDATE_MANIFEST_URL,{headers:{Accept:'application/json'},signal:AbortSignal.timeout(20000),cache:'no-store'});
 if(!response.ok)throw Error('更新服务暂不可用');
 const manifest=validateManifest(await response.json());
 if(!UPDATE_HOSTS.has(new URL(manifest.downloadUrl).host))throw Error('更新地址不在受信任的服务域名内');
 return manifest;
}
handle('app:update-version',()=>({version:app.getVersion()}));
handle('app:update-check',async()=>{const currentVersion=app.getVersion();const manifest=await fetchUpdateManifest();return {currentVersion,available:isNewer(manifest.version,currentVersion),...manifest};});
handle('app:update-download',async(manifest)=>{
 if(!manifest||!isNewer(manifest.version,app.getVersion()))throw Error('没有可下载的新版本');
 const checked=validateManifest(manifest);const url=new URL(checked.downloadUrl);
 if(!UPDATE_HOSTS.has(url.host))throw Error('更新地址不在受信任的服务域名内');
 const response=await fetch(url,{headers:{Accept:'application/octet-stream'},signal:AbortSignal.timeout(10*60*1000),cache:'no-store'});
 if(!response.ok||!response.body)throw Error('更新文件下载失败');
 const target=path.join(app.getPath('temp'),`KUAT-update-${checked.version}-${Date.now()}.exe`);
 await pipeline(Readable.fromWeb(response.body),createWriteStream(target));
 const actual=crypto.createHash('sha256').update(await fs.readFile(target)).digest('hex');
 if(actual!==checked.sha256){await fs.rm(target,{force:true});throw Error('更新文件校验失败，已删除不完整文件');}
 pendingUpdatePath=target;return {ok:true,version:checked.version,sizeBytes:checked.sizeBytes};
});
handle('app:update-install',async()=>{
 if(!pendingUpdatePath)throw Error('请先下载更新');
 await fs.access(pendingUpdatePath);
 const installer=pendingUpdatePath;pendingUpdatePath='';
 spawn(installer,[],{detached:true,stdio:'ignore',windowsHide:false}).unref();
 setTimeout(()=>app.quit(),150);
 return {ok:true};
});
app.whenReady().then(()=>{win=new BrowserWindow({icon:path.join(__dirname,'../assets/kuat-icon-ring.png'),width:1480,height:980,minWidth:960,minHeight:680,title:'K.U.A.T · Project World',backgroundColor:'#f5f6f8',webPreferences:{preload:path.join(__dirname,'preload.cjs'),partition:'kuat-cloud-memory',contextIsolation:true,nodeIntegration:false,sandbox:true}});win.setMenuBarVisibility(false);win.loadFile(path.join(__dirname,'../index.html'));win.webContents.setWindowOpenHandler(()=>({action:'deny'}));win.webContents.on('will-navigate',(e,url)=>{const entry=require('node:url').pathToFileURL(path.join(__dirname,'../index.html')).href;if(url!==entry)e.preventDefault();});});
app.on('window-all-closed',()=>{access='';app.quit();});
