// Render the canonical SVG without changing its proportions, color or transparency.
const {app,BrowserWindow}=require('electron');
const fs=require('node:fs');const path=require('node:path');
app.whenReady().then(async()=>{try{
 const svg=fs.readFileSync(path.join(__dirname,'../assets/kuat-brand-ring.svg'),'utf8');
 const win=new BrowserWindow({show:false,webPreferences:{contextIsolation:true,sandbox:true}});
 await win.loadURL('about:blank');
 const sizes=[16,24,32,48,64,128,256];
 const render=async(size)=>Buffer.from(await win.webContents.executeJavaScript(`(async()=>{const img=new Image();img.src=${JSON.stringify('data:image/svg+xml;base64,'+Buffer.from(svg).toString('base64'))};await img.decode();const c=document.createElement('canvas');c.width=c.height=${size};const h=${size}*0.96,w=h*64/96;c.getContext('2d').drawImage(img,(${size}-w)/2,(${size}-h)/2,w,h);return c.toDataURL('image/png').split(',')[1]})()`),'base64');
 fs.writeFileSync(path.join(__dirname,'../assets/kuat-icon-ring.png'),await render(512));
 const images=[];for(const size of sizes)images.push(await render(size));
 const header=Buffer.alloc(6+16*sizes.length);header.writeUInt16LE(1,2);header.writeUInt16LE(sizes.length,4);let offset=header.length;
 sizes.forEach((size,i)=>{const p=6+i*16;header[p]=size===256?0:size;header[p+1]=size===256?0:size;header.writeUInt16LE(1,p+4);header.writeUInt16LE(32,p+6);header.writeUInt32LE(images[i].length,p+8);header.writeUInt32LE(offset,p+12);offset+=images[i].length;});
 fs.writeFileSync(path.join(__dirname,'../assets/kuat-icon-ring.ico'),Buffer.concat([header,...images]));app.exit(0);
}catch(e){console.error(e);app.exit(1);}});
