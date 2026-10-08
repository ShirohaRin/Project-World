from pathlib import Path
root = Path('/app/knowledge_novel')
for path in root.rglob('*'):
    if path.is_file():
        print(repr(str(path)))
