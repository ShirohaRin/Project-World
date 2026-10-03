"""雨课堂 PPT 课堂笔记的 Markdown 写入。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class RainClassSlide:
    course_id: str
    course_name: str
    session_id: str
    slide_id: str
    slide_index: int | None
    discovered_at: datetime
    image_path: str
    image_url: str = ""
    page_url: str = ""


class MarkdownNoteWriter:
    """将每个新 PPT 页面保存为图片，并追加到同一份 Markdown 笔记。"""

    def __init__(self, path: str | Path, asset_dir: str | Path | None = None) -> None:
        self.path = Path(path)
        self.asset_dir = Path(asset_dir) if asset_dir else self.path.with_name(f"{self.path.stem}_assets")

    async def append_slide(
        self,
        slide: RainClassSlide,
        image_bytes: bytes,
        ocr_text: str = "",
    ) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.asset_dir.mkdir(parents=True, exist_ok=True)

        image_name = Path(slide.image_path).name
        image_target = self.asset_dir / image_name
        image_target.write_bytes(image_bytes)

        if not self.path.exists():
            self.path.write_text(
                f"# {slide.course_name}\n\n"
                f"- 课程 ID：{slide.course_id}\n"
                f"- 课堂 Session：{slide.session_id or '未识别'}\n\n",
                encoding="utf-8",
            )

        title = f"第 {slide.slide_index} 页" if slide.slide_index is not None else f"页面 {image_name}"
        relative_image = image_target.relative_to(self.path.parent).as_posix()
        lines = [
            f"## {title}",
            "",
            f"- 发现时间：{slide.discovered_at.astimezone().isoformat(timespec='seconds')}",
            f"- 页面 ID：{slide.slide_id}",
        ]
        if slide.page_url:
            lines.append(f"- 页面地址：{slide.page_url}")
        if slide.image_url:
            lines.append(f"- 图片地址：{slide.image_url}")
        if ocr_text.strip():
            lines.extend(["", "### OCR 识别文本", "", ocr_text.strip()])
        lines.extend(["", f"![{title}]({relative_image})", "", ""])

        with self.path.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines))
