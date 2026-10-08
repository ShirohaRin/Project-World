from pathlib import Path
import re
root = Path('/app/knowledge_novel')
files = []
for path in root.rglob('*.md'):
    try:
        content = path.read_text(encoding='utf-8')
    except UnicodeDecodeError:
        continue
    if '## 夸特（K.U.A.T集团）' in content and '## 梅莹一族' in content:
        files.append((path, content))
if len(files) != 1:
    raise SystemExit(f'candidate count: {len(files)}')
path, content = files[0]
section = Path('/tmp/kuat-section.md').read_text(encoding='utf-8').rstrip() + '\n\n'
updated, count = re.subn(r'^## 夸特（K\.U\.A\.T集团）\n.*?(?=^## 梅莹一族\n)', section, content, count=1, flags=re.S | re.M)
if count != 1:
    raise SystemExit(f'K.U.A.T section count: {count}')
path.write_text(updated, encoding='utf-8')
print('updated:', path)
