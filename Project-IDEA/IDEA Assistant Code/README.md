# IDEA Assistant 客户端

IDEA Assistant 是 Program IDEA 的本地 Electron 客户端。当前版本只提供离线功能：工作区选择、文件树、文本与 Markdown 编辑、Markdown 预览，以及 IDE 和 Work 两种工作模式。

## 技术边界

- 界面逻辑使用 React 与 TypeScript，代码在 `src/`。
- 本地文件访问通过 Electron 主进程和预加载桥实现，代码在 `electron/`。
- `index.html` 仅提供浏览器窗口所需的根节点和 JavaScript 模块入口；它不是界面开发的主体。
- 当前版本不启动 Python 服务、不连接 Agent Host、不调用网络服务。

## 目录结构

```text
IDEA Assistant/
├── src/                 # React 组件、样式与前端类型
├── electron/            # 主进程与受限 IPC 文件操作
├── public/              # 应用图标等静态资源
├── index.html           # React 挂载入口
├── package.json         # 开发、构建和 Windows 打包命令
├── tsconfig.json        # 前端 TypeScript 配置
├── tsconfig.electron.json
└── vite.config.ts       # Vite 构建配置
```

`node_modules/`、`dist/`、`dist-electron/` 和 `release/` 均为可重新生成目录，不作为源码维护。

## 启动与构建

```powershell
npm install
npm run dev:electron
```

生产构建使用：

```powershell
npm run build
npm run package:win
```
