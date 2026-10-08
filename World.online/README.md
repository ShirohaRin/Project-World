# Project World 网站

这是 [Project World](../README.md) 的第三子项目，目标是在网页端介绍世界构建项目，并承载相应的功能实现。它是世界项目的网页入口，与白羽奈绪的 [Shiroha 个人网站](../ShirohaV1.1/README.md) 分工不同。

## 当前文件

本目录目前由 HTML、CSS 和 JavaScript 页面组成，没有独立的 `package.json`。

| 文件 | 用途 |
| --- | --- |
| [index.html](index.html) | 首页入口 |
| [about.html](about.html) | 关于页面 |
| [explore.html](explore.html) | 探索页面 |
| [idea.html](idea.html) | IDEA 页面 |
| [styles.css](styles.css)、[idea.css](idea.css) | 页面样式 |
| [script.js](script.js) | 页面交互脚本 |
| [PROJECT-RULES.md](PROJECT-RULES.md) | Core（核心）与漂浮几何体等视觉规则 |

## 本地查看

可在本目录使用本机静态 HTTP 服务。例如已安装 Python 时：

```powershell
python -m http.server 8000 --bind 127.0.0.1
```

随后访问 `http://127.0.0.1:8000/`。这仅是本地页面预览方式；涉及外部接口的功能还取决于对应服务和配置，不代表生产部署或功能验收。

## 与世界内容及 K.U.A.T 的边界

世界观与正文资料主要位于 [`IDEA/`](../IDEA/README.md)。网站中的公开介绍应选择适合披露的内容，不自动将创作资料、作者资料或受限设定发布出去。

[K.U.A.T](../Project-KUAT/README.md) 负责综合创作、管理、关系可视化与多 Agent 世界模拟。网站未来是否嵌入、链接或调用这些能力，需要另行设计；现有网页不代表已经实现该平台。

本页依据 2026-10-03 确认的项目定位和当前文件结构整理；网页功能完成度与线上状态未在本次文档工作中验收。
