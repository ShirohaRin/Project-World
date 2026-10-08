import { packager } from '../Environments/Node/node_modules/@electron/packager/dist/index.js';
import { createRequire } from 'node:module';
const require=createRequire(import.meta.url);
const {version}=require('../Environments/Node/node_modules/electron/package.json');
const result=await packager({dir:new URL('.',import.meta.url).pathname.replace(/^\/(\w:)/,'$1'),name:'KUAT',icon:new URL('./assets/kuat-icon-ring.ico',import.meta.url).pathname.replace(/^\/(\w:)/,'$1'),platform:'win32',arch:'x64',electronVersion:version,electronZipDir:new URL('../Environments/Cache/Electron/',import.meta.url).pathname.replace(/^\/(\w:)/,'$1'),out:new URL(process.env.KUAT_BUILD_OUTPUT || './release/Current/',import.meta.url).pathname.replace(/^\/(\w:)/,'$1'),overwrite:true,prune:false,ignore:[/^\/Archive($|\/)/,/^\/Design($|\/)/,/^\/server($|\/)/,/^\/scripts($|\/)/,/^\/src\/(archive-data|entries-data)\.js$/,/^\/release($|\/)/,/^\/Latest($|\/)/,/^\/tests($|\/)/,/^\/\.npm-cache($|\/)/,/^\/node_modules($|\/)/],win32metadata:{CompanyName:'Project World',ProductName:'K.U.A.T',FileDescription:'Project World 创作工作台'}});
console.log(result.join('\n'));
import { cp, rm, mkdir } from 'node:fs/promises';
const packaged=new URL('./release/Current/KUAT-win32-x64/',import.meta.url).pathname.replace(/^\/(\w:)/,'$1');
const latest=new URL('./Latest/',import.meta.url).pathname.replace(/^\/(\w:)/,'$1');
await rm(latest,{recursive:true,force:true});await mkdir(latest,{recursive:true});await cp(packaged,latest,{recursive:true});
console.log('Stable entry:', latest+'KUAT.exe');




