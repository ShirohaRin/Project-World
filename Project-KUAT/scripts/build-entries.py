from pathlib import Path
import json,re,hashlib
p=Path('Project-KUAT'); archive=json.loads((p/'src/archive-data.js').read_text(encoding='utf-8').removeprefix('window.IDEAArchive = ').strip().removesuffix(';'))
# Curated entity headings; prose subheadings remain inside the entity rather than becoming cards.
groups={
'人物': '白羽绫|白羽辰|白羽芙萝拉|白羽雫|白羽梓|白羽夏菲|白羽希纳|绯吉亚·伊莎梅莱|希帕里亚·克鲁斯|柯维娜·玛拉|卡斯伊尔|艾斯克|雨桃|E-723|伊诗渊|齐霆|墨凌|厄|婪|怒|屈|伊瑟尔|利德尔|银|库洛伊|许寂|张婷|洛星雪|罪西|契铭|崔卿棱|奈乐|星雨|海德纳·黎|白柔|洛星宁|海德纳·斯诺|王庆国|梅莹·罗斯|梅莹·翎|莱拉|伊迪娅|伊莉雅斯|希尔德斯|被囚禁的王女|科尔斯',
'组织': '影子一族|白羽一族|七色议会|白羽议会|创世教会|观察者一族|魔法研习会|日亚一族|罪孽剧团|星言小队|洛星一族|夸特集团|梅莹一族|银的研究院|诺斯卡尔高等学院',
'地点': '塔罗斯|塔斯尤尔中央图书馆|塔斯尤尔城|泰拉遗迹城|星空之树|水晶之树|裂空之树|裂缝倒影|伯斯塔城|商业区|老城区|圣伯斯塔大教堂|伯斯塔监测站|圣城贝伦蒂亚|海德纳领地|洛星—海德纳联合矿区|传送大厅|中央体育场|辰の咖啡馆',
'概念': '黑雨|异象|构造体|裂缝|实位面与复位面|实属性与复属性|西斯塔常数与西斯塔位面|时空异位|货币体系|电子支付系统|天气模拟力场|遗迹分类体系|应变性设备|月花|法阵医疗|盖亚之主|圣遗物|学院引导人制度|洛星一族的预言',
'事件': '前文明时代|大毁灭|时空震荡|帝国回归|诺斯卡尔高等学院创立|海德纳事件|伯斯塔二号井矿难|伯斯塔城事件|星核计划'
}
alias={'星球概述':'塔罗斯','伊维娅·希洛瓦':'洛星雪','罪西（孙言默）':'罪西','导演（齐霆）':'齐霆','骸（墨凌）':'墨凌','厄（演员）':'厄','婪（舞者）':'婪','夸特（K.U.A.T集团）':'夸特集团','？？？（被囚禁的王女）':'被囚禁的王女','诺斯卡尔高等学院——补充设定':'诺斯卡尔高等学院','中央体育场——空间嵌套技术':'中央体育场','白羽辰——补充设定':'白羽辰','伊瑟尔——补充设定':'伊瑟尔','利德尔——补充设定':'利德尔','罪西的特殊体质':'罪西','崔卿棱与观察者':'崔卿棱','墨凌的班级调查':'墨凌','奈乐的"魔法期"':'奈乐','星雨——能力具体表现':'星雨','洛星宁——"五个人之一"':'洛星宁','海德纳·黎——现任家主':'海德纳·黎','洛星雪的"印记"':'洛星雪'}
types={name:t for t,names in groups.items() for name in names.split('|')}
entries={}
for doc in archive['documents']:
 if not any(x in doc['source'] for x in ['第一部分 世界观设计','第二部分 世界场景设计','第三部分 世界历史设计','第四部分 人物']): continue
 if '/10_' in doc['source'] or '/11_' in doc['source']: continue
 body=doc['body']; headings=list(re.finditer(r'^(#{1,3}) (.+)$',body,re.M))
 selected=[]
 for h in headings:
  title=h[2].strip(); normalized=alias.get(title,re.sub(r'（[^）]*）$','',title)); normalized=alias.get(normalized,normalized)
  if normalized in types: selected.append((h,normalized))
 for h,name in selected:
  # Keep lower-level details; stop at the next sibling heading or independently modeled child.
  end=len(body)
  for nxt in headings:
   if nxt.start()<=h.start(): continue
   if len(nxt[1])<=len(h[1]) or any(nxt.start()==m.start() for m,n in selected): end=nxt.start();break
  excerpt=body[h.end():end].strip()
  if not excerpt:
   end=next((n.start() for n in headings if n.start()>h.start() and len(n[1])<=len(h[1])),len(body))
   excerpt=body[h.end():end].strip()
  if not excerpt: continue
  e=entries.setdefault(name,dict(id='entry-'+hashlib.sha256(name.encode()).hexdigest()[:12],title=name,type=types[name],aliases=[],tags=[],summary='',body='',notes='',status='待核对',visibility='内部',sources=[],related=[],updatedAt=None,pendingSync=False,revision=0))
  if excerpt not in e['body']:
   e['body']+= ('\n\n' if e['body'] else '')+'### '+h[2].strip()+'\n'+excerpt
  e['sources'].append(dict(documentId=doc['id'],heading=h[2].strip(),role='摘录来源'))
for doc in archive['documents']:
 if '/02_创世教会.md' in doc['source']:
  for label in ['教会的真相','补充设定']:
   match=re.search(r'^## '+label+r'\s*$',doc['body'],re.M)
   if match:
    end=re.search(r'^## ',doc['body'][match.end():],re.M)
    text=doc['body'][match.end():match.end()+end.start() if end else len(doc['body'])].strip()
    entries['创世教会']['body']+='\n\n### '+label+'\n'+text
for name,e in entries.items():
 e['summary']=re.sub(r'[#*`>\[\]]','',next((s for s in e['body'].splitlines() if s.strip() and not s.startswith(('#','>','-','|'))),''))[:140]
 # Source discussions remain references, not automatically accepted canon.
 for d in archive['documents']:
  if any(x in d['source'] for x in ['总大纲','WORLD.md','记忆——设计','补充','情节事件追踪']) and name in d['body']:
   e['sources'].append(dict(documentId=d['id'],heading='',role='相关讨论／版本参考'))
 e['notes']='由原始设定拆分，尚未逐条统一历史版本。参考资料中的讨论与推测不自动构成定稿。'
for name,aliases in {'齐霆':['导演'],'墨凌':['骸'],'厄':['演员'],'婪':['舞者'],'莱拉':['AI管理员'],'洛星雪':['伊维娅·希洛瓦'],'罪西':['孙言默'],'魔法研习会':['魔导会'],'夸特集团':['K.U.A.T集团','夸特'],'伊迪娅':['伊迪亚'],'白羽希纳':['希娜'],'崔卿棱':['崔卿菱']}.items():
 if name in entries:entries[name]['aliases']=list(dict.fromkeys(entries[name]['aliases']+aliases))
for name,note in {'夸特集团':'总大纲写11个集团，实际列出13个；细分文档写13个，待统一。此词条是世界内组织，不是现实软件项目。','墨凌':'原文班级调查段使用“她”，第四章后补充明确星言成员为男生；保留原文等待修订。','海德纳事件':'旧事件追踪第六章揭露，后续设计要求三十章以后；救援经过也存在不同版本，勿混成单一确定历史。'}.items():
 if name in entries:entries[name]['notes']+='\n'+note
# Explicit affiliation/reference links, without inferring relationships from co-occurrence.
for a,b in [('白羽绫','白羽一族'),('白羽辰','白羽一族'),('白羽芙萝拉','白羽一族'),('伊诗渊','魔法研习会'),('卡斯伊尔','观察者一族'),('伊瑟尔','诺斯卡尔高等学院'),('洛星雪','洛星一族'),('星核计划','魔法研习会'),('莱拉','塔斯尤尔中央图书馆')]:
 if a in entries and b in entries:entries[a]['related'].append(entries[b]['id'])
author=next(d for d in archive['documents'] if '/12_夸特集团_' in d['source'])
e=entries['夸特集团'];e['body']=author['body'];e['summary']='白羽一族下辖执行集团：核心夸特具有完整主权，地方夸特嵌入辖区外文明。';e['status']='已确认';e['notes']='以2026-10-04作者补充为准，列举13个集团。Infinite、Orange、White同级。旧资料仅供历史核对。';e['sources'].insert(0,dict(documentId=author['id'],heading='夸特集团',role='作者最新确认'));e['seedRevision']='kuat-20261004';e['related']=list(dict.fromkeys(e['related']+[entries['白羽一族']['id']]))
seed={'schemaVersion':1,'entries':list(entries.values())}
(p/'src/entries-data.js').write_text('window.KUATEntrySeed = '+json.dumps(seed,ensure_ascii=False).replace('<','\\u003c')+';\n',encoding='utf-8')
print(len(entries),'entries', {t:sum(e['type']==t for e in entries.values()) for t in groups})
print('Missing:',set(types)-set(entries))
