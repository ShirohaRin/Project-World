/* Source text is data: no HTML, images, commands or external links are executed. */
const ArchiveView = (() => {
 const state={query:'',group:'全部',selected:null,author:false,raw:false};
 const issues=[
  ['版本差异','海德纳调查的揭露时机','旧事件记录记载学院篇第六章已揭露；设计讨论要求三十章以后。两套记录并存，暂不生成唯一剧情时间线。'],
  ['已澄清','夸特集团的下辖集团','2026-10-04 作者补充列举 13 个集团，并明确核心／地方结构及级别。旧大纲的 11 个仅保留作历史记录。'],
  ['称谓待核对','人物姓名与代词','崔卿棱／崔卿菱、希纳／希娜、伊迪亚／伊迪娅存在不同写法；墨凌有一处使用“她”，而补充明确星言四人均为男生。'],
  ['叙事层次','人物知道的事与作者知道的事','事件追踪中的“未揭示”不等于作者尚未设定。例如星雨身份、塔斯尤尔地点，已有作者设定；不能把推测直接升级为事实。'],
  ['讨论记录','提案不等于定稿','WORLD 包含多轮否决、暂缓与待裁决提案。设计讨论同时包含作者答复和助手建议；源文件 complete 仅为原有标记。'],
  ['引用待修复','角色全目录的旧编号','部分 Markdown 引用仍使用旧编号和旧目录。原文照存，请通过档案标题搜索对应文档。'],
  ['版本差异','相识经历与救援经过','入学前未见过学院老师的描述，与伊瑟尔以其他身份训练／见过雪的记录需要区分身份视角；罪西与黎的救援经过也有不同版本，暂不合并。']
 ];
 const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const inline=s=>esc(s).replace(/\*\*([^*\n]+)\*\*/g,'<strong>$1</strong>').replace(/`([^`\n]+)`/g,'<code>$1</code>');
 function markdown(text){
  const lines=text.split(/\r?\n/);let output='',code=false,table=false;
  for(const line of lines){
   if(line.trim().startsWith('```')){if(table){output+='</tbody></table></div>';table=false;}output+=code?'</pre>':'<pre class="archive-code">';code=!code;continue;}
   if(code){output+=esc(line)+'\n';continue;}
   if(/^\s*\|.*\|\s*$/.test(line)){
    if(!table){output+='<div class="archive-table"><table><tbody>';table=true;}
    if(!/^\s*\|[\s:|\-]+\|\s*$/.test(line))output+='<tr>'+line.trim().slice(1,-1).split('|').map(x=>'<td>'+inline(x.trim())+'</td>').join('')+'</tr>';
    continue;
   }
   if(table){output+='</tbody></table></div>';table=false;}
   const h=line.match(/^\s*(#{1,6})\s+(.+)$/);
   if(h)output+=`<h${Math.min(h[1].length+1,6)}>${inline(h[2])}</h${Math.min(h[1].length+1,6)}>`;
   else if(/^\s*---+\s*$/.test(line))output+='<hr>';
   else if(line.trim())output+=line.startsWith('>')?'<blockquote>'+inline(line.replace(/^>\s?/,''))+'</blockquote>':'<p>'+inline(line)+'</p>';
  }
  if(code)output+='</pre>';if(table)output+='</tbody></table></div>';return output;
 }
 function filtered(){return IDEAArchive.documents.filter(d=>(state.group==='全部'||d.group===state.group)&&(!state.query||[d.title,d.source,(!d.restricted||state.author)?d.body:''].join('\n').toLocaleLowerCase().includes(state.query.trim().toLocaleLowerCase())));}
 function render(){
  const docs=filtered();const selected=IDEAArchive.documents.find(d=>d.id===state.selected);
  return `<section class="page-heading"><div><h1>设定档案</h1><p>来自 IDEA 的 ${IDEAArchive.documents.length} 份原始资料 · 本地只读快照</p></div><span class="outline-badge">内部创作资料</span></section>
  <div class="archive-toolbar"><input id="archive-search" aria-label="搜索设定档案" placeholder="搜索人物、地点、概念或原文…" value="${esc(state.query)}"><select id="archive-group" aria-label="档案分类">${['全部',...new Set(IDEAArchive.documents.map(d=>d.group))].map(g=>`<option ${g===state.group?'selected':''}>${esc(g)}</option>`).join('')}</select><button id="archive-author">${state.author?'收起':'显示'}作者层</button></div>
  <p class="archive-note">保留设定、推测与修订的原始语境。此资料库未纳入 Matrees 导出。作者层开关仅用于防止误读，不是访问权限控制。</p>
  <details class="archive-review"><summary>归档核对笔记 · ${issues.length} 项</summary>${issues.map(i=>`<div><small>${i[0]}</small><strong>${i[1]}</strong><p>${i[2]}</p></div>`).join('')}</details>
  <div class="archive-layout"><section class="archive-list" aria-label="档案列表"><p class="archive-count" role="status">${docs.length} 份匹配资料</p>${docs.map(d=>`<button class="archive-card ${selected?.id===d.id?'selected':''}" data-document="${d.id}" aria-pressed="${selected?.id===d.id}"><small>${esc(d.group)}${d.restricted?' · 作者层':''}</small><strong>${esc(d.title)}</strong><span>${esc(d.restricted?'含作者层真相与讨论，默认折叠。':d.summary||'保留原始内容及修订上下文，点击阅读。')}</span></button>`).join('')||'<p class="empty-state">没有匹配资料，请更换关键词或分类。</p>'}</section><article class="archive-reader">${selected?reader(selected):'<div class="archive-welcome"><span>▤</span><h2>从一份设定开始</h2><p>选择左侧档案，阅读原文与来源。<br>人物、场景、事件和讨论分别保留。</p></div>'}</article></div>`;
 }
 function reader(d){return `<div class="archive-reader-head"><small>${esc(d.group)} · 源标记：${esc(d.sourceStatus)}（不代表定稿）</small><h2>${esc(d.title)}</h2><p class="archive-source">${esc(d.source)}</p><div><span>${d.sourceDate?'源文档日期 '+esc(d.sourceDate):'源文档未标日期'}</span><button id="archive-raw">${state.raw?'排版阅读':'原文视图'}</button></div></div>${d.restricted&&!state.author?'<div class="archive-welcome"><h3>作者层内容已折叠</h3><p>需要阅读时，点击上方“显示作者层”。</p></div>':`<div class="archive-prose">${state.raw?'<pre class="archive-raw">'+esc(d.body)+'</pre>':markdown(d.body)}</div>`}`;}
 function install(refresh){
  const main=document.querySelector('#main');
  main.addEventListener('click',e=>{const card=e.target.closest('[data-document]');if(card){state.selected=card.dataset.document;state.raw=false;refresh();document.querySelector('.archive-reader').scrollTop=0;}
   if(e.target.closest('#archive-author')){state.author=!state.author;refresh();}
   if(e.target.closest('#archive-raw')){state.raw=!state.raw;refresh();}
  });
  main.addEventListener('input',e=>{if(e.target.id==='archive-search'){const position=e.target.selectionStart;state.query=e.target.value;refresh();const input=document.querySelector('#archive-search');input.focus();input.setSelectionRange(position,position);}});
  main.addEventListener('change',e=>{if(e.target.id==='archive-group'){state.group=e.target.value;refresh();}});
 }
 return {render,install,markdown};
})();
