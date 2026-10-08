// Authentication only. Creative documents never enter this store.
module.exports=({fs,path,safeStorage,directory})=>{
 const file=()=>path.join(directory(),'LoginSession.bin');
 const secure=()=>safeStorage.isEncryptionAvailable()&&(!safeStorage.getSelectedStorageBackend||safeStorage.getSelectedStorageBackend()!=='basic_text');
 const clear=()=>fs.rm(file(),{force:true});
 return {
  clear,
  async save(session){
   if(!secure())throw Error('系统凭据加密不可用，无法保存登录态');
   await fs.mkdir(directory(),{recursive:true});
   const encrypted=safeStorage.encryptString(JSON.stringify(session));
   await fs.writeFile(file()+'.tmp',encrypted,{mode:0o600});
   await fs.rename(file()+'.tmp',file());
  },
  async load(){
   let bytes;try{bytes=await fs.readFile(file());}catch(e){if(e.code==='ENOENT')return null;throw e;}
   if(!secure())throw Error('系统凭据加密不可用，请稍后重试');
   let saved;try{saved=JSON.parse(safeStorage.decryptString(bytes));}catch{await clear();return null;}
   if(typeof saved?.access!=='string'||!saved.access||typeof saved.device!=='string'||!saved.device||!Number.isFinite(saved.expires)||saved.expires<=Date.now()/1000){await clear();return null;}
   return saved;
  }
 };
};
