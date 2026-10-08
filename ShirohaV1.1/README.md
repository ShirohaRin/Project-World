# Shiroha · 个人网站

Shiroha 是 [Project World](../README.md) 的第二子项目，目标是建立白羽奈绪的个人网站，承载个人表达与展示。它与介绍世界构建项目的 [Project World 网站](../World.online/README.md) 有不同的定位。

## 当前工程

本目录已有 Vue、TypeScript 与 Vite 前端工程，目录名称保留为 `ShirohaV1.1`。

| 位置 | 用途 |
| --- | --- |
| [src/App.vue](src/App.vue) | 应用界面入口 |
| `src/components/` | 界面组件 |
| `src/composables/` | 组合式逻辑 |
| `src/styles/` | 样式 |
| `public/`、`resource/` | 静态资源与素材 |
| [技术架构.md](技术架构.md) | 已有技术设计说明，具体实现以代码为准 |
| [package.json](package.json) | 依赖与开发脚本 |

## 开发与构建

在本目录执行：

```powershell
npm install
npm run dev
```

构建和预览分别使用：

```powershell
npm run build
npm run preview
```

`build` 会执行 Vue TypeScript 检查和 Vite 构建；`preview` 用于预览构建产物。这些命令已按脚本核对，此次文档整理未运行构建，也不确认线上部署状态。

## 项目边界

个人网站服务于白羽奈绪的个人展示；世界项目的专门介绍与网页功能属于 World.online，综合创作与管理平台属于 K.U.A.T。需要联动时再明确内容与接口，不因同属一个仓库就混用职责或自动公开私人资料。

项目定位依据白羽奈绪于 2026-10-03 的说明整理。
