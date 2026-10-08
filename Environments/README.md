# Shared environments

Project World 的新增环境依赖统一位于此目录。

- `Node/`：共享 npm 依赖，含 Electron 与桌面打包工具。清单和锁文件保留在版本控制中。
- `Cache/`：安装缓存，不纳入版本控制。
- `Python/<环境名>/`：后续 Python 环境的约定位置，尚未创建。

在仓库根目录执行 `./Install-Environments.ps1` 安装，随后运行 `Project-KUAT/Start-KUAT.ps1` 启动软件。
现有其他项目的环境不自动搬迁。新建顶层目录使用正式名称，避免无必要的点前缀。
