"""手动写入 P24/P28（切片细识别已确认），检查 P27 完整性。"""
import json
from pathlib import Path

p = Path(r"G:\Project-World\Project-World\IDEA\文风素材\来自深渊\ocr-cache\2-76-indent-paragraph-ocr.json")
items = json.loads(p.read_text(encoding="utf-8"))

manual = {
    24: "“嗯。”我连忙点头，“他带着金面具。不过、被我打碎...血祭。那是什么？”",
    28: ("“而第二个阶段，叫做复苏。这个阶段的业火一旦燃起，不仅温度要高了不止一个层次，"
         "火焰能轻易融化钢铁，还能恢复伤势...连致命伤都可以瞬间愈合，哪怕是砍断他们的手脚，"
         "划开他们的喉咙...你已经见识过了吧？”"),
}
for x in items:
    if x["paragraph"] in manual:
        x["text"] = manual[x["paragraph"]]
        x["char_count"] = len(manual[x["paragraph"]])
        x["error"] = None
        x["fixed_from_trace"] = "切片细识别写入（API多次返回空）"
    if x["paragraph"] == 27:
        print("P27 len:", len(x["text"]))
        print("P27 tail:", x["text"][-40:])
        if not x["text"].endswith("。”"):
            print("P27 疑似不完整，需要补全")
p.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
print("written 24/28")
