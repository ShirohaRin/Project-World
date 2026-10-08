# 当前软件图标

2026-10-04：用户选定加粗版蓝灰纯色几何环作为软件图标，取代此前四芒星 v11。

- 原始矢量：assets/kuat-brand-ring.svg，同时用于左上角界面标识。
- 软件资源：assets/kuat-icon-ring.png 和 assets/kuat-icon-ring.ico。
- 透明背景，颜色 #536384，保留原有纵向比例，在正方形画布中居中。
- scripts/create-icon.cjs 使用已有 Electron 渲染 SVG，生成 512px PNG 和 16/24/32/48/64/128/256px ICO。
- 已更新窗口、favicon 和 Windows 打包图标。构建：release/ring-icon-20261004/KUAT-win32-x64/KUAT.exe。
