# Project World 环境依赖约定

- 新增运行环境、工具链、虚拟环境和下载缓存统一安装在仓库根目录 `Environments/` 下，不安装到仓库根目录或各子项目目录。
- 按环境分目录，例如 `Environments/Node/` 放共享 Node 工具依赖，`Environments/Python/<环境名>/` 放后续 Python 环境，`Environments/Cache/` 放下载缓存。
- 复用前先核实已有环境。不使用 `game/garden/` 临时桌宠项目的运行时。
- 现存子项目环境不自动迁移；需要迁移时先核验用途和影响。
- 共享 Node 依赖清单在 `Environments/Node/package.json`，通过根目录 `Install-Environments.ps1` 安装。
- 新建环境和项目目录优先使用正式、可见的名称，不使用无必要的点前缀。
