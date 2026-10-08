"""Build an offline, lossless snapshot of the explicitly selected setting sources."""
from pathlib import Path
import hashlib
import json
import re
from datetime import datetime, timezone

project = Path(__file__).resolve().parents[1]
root = project.parent
sources = [root / 'IDEA/WORLD.md']
sources += sorted((root / 'IDEA/记忆——世界观').rglob('*.md'))
sources += sorted((root / 'IDEA/记忆——设计').glob('*.md'))
documents = []
for source in sources:
    raw = source.read_bytes()
    content = raw.decode('utf-8-sig')
    relative = source.relative_to(root).as_posix()
    meta = {}
    body = content
    match = re.match(r'^---\s*\n(.*?)\n---\s*\n', content, re.S)
    if match:
        meta = dict(re.findall(r'^(\w+):[ \t]*(.*)$', match[1], re.M))
        body = content[match.end():]
    group = '基础设定'
    if '第四部分' in relative: group = '人物与组织'
    elif '第二部分' in relative: group = '地理与场景'
    elif '第三部分' in relative: group = '世界历史'
    elif '情节事件追踪' in relative: group = '事件与伏笔'
    elif '/补充/' in relative: group = '补充与修订'
    elif '记忆——设计' in relative: group = '设计讨论'
    elif source.name == 'WORLD.md': group = '作者层档案'
    title = meta.get('title', source.stem).strip()
    if source.name in ('WORLD.md', '总大纲2.0.md') or group == '设计讨论': title = source.stem
    target = project / 'Archive/Sources' / source.relative_to(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    documents.append(dict(id=hashlib.sha256(relative.encode()).hexdigest()[:16], title=title,
        source=relative, group=group, sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw),
        sourceStatus=meta.get('status', '未标注').strip(), sourceDate=meta.get('date', '').strip(),
        restricted=group == '作者层档案', body=body, metadata=meta,
        summary=meta.get('summary', '').strip(), syncEligible=False))
dataset = dict(schemaVersion=1, importedAt=datetime.now(timezone.utc).isoformat(), documents=documents)
(project / 'Archive/manifest.json').write_text(json.dumps({**dataset, 'documents': [{k:v for k,v in d.items() if k != 'body'} for d in documents]}, ensure_ascii=False, indent=2), encoding='utf-8')
(project / 'src/archive-data.js').write_text('window.IDEAArchive = ' + json.dumps(dataset, ensure_ascii=False).replace('<', '\\u003c') + ';\n', encoding='utf-8')
print(f'Archived {len(documents)} documents, {sum(d["bytes"] for d in documents)} bytes; originals unchanged.')
