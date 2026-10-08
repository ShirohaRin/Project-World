const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');const path=require('node:path');const vm=require('node:vm');const crypto=require('node:crypto');
const project=path.resolve(__dirname,'..');const context={window:{}};vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(project,'src/archive-data.js'),'utf8'),context);
vm.runInContext(fs.readFileSync(path.join(project,'src/archive.js'),'utf8')+'\nthis.renderer=ArchiveView;',context);
test('all 27 selected source files are byte-for-byte preserved with correct hashes',()=>{
 const docs=context.window.IDEAArchive.documents;assert.equal(docs.length,27);assert.equal(new Set(docs.map(d=>d.id)).size,27);
 for(const d of docs){const source=fs.readFileSync(path.join(project,'..',d.source));const copy=fs.readFileSync(path.join(project,'Archive/Sources',d.source));assert.deepEqual(copy,source,d.source);assert.equal(crypto.createHash('sha256').update(copy).digest('hex'),d.sha256);assert.equal(d.syncEligible,false);assert.ok(d.body.length>0);}
 assert.equal(docs.filter(d=>d.restricted).length,1);
});
test('document renderer cannot execute embedded HTML or Markdown links',()=>{
 const result=context.renderer.markdown('# Heading\n<img src=x onerror=alert(1)>\n[click](javascript:alert(1))\n```\n<script>alert(1)</script>\n```');
 assert.ok(result.includes('<h2>Heading</h2>'));assert.ok(!result.includes('<img'));assert.ok(!result.includes('<script'));assert.ok(!result.includes('<a '));assert.ok(result.includes('&lt;script&gt;'));
});
