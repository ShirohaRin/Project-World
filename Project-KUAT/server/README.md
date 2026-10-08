# K.U.A.T 云端后端

生产入口：`https://shiroha-rin.world/kuat-api/`。

- 独立 Python 标准库服务，监听 `127.0.0.1:8910`，systemd `kuat.service`，专用 `kuat` 用户。
- 数据：`/var/lib/kuat/workspace.sqlite3`，SQLite WAL、原子事务、版本检查、服务端历史版本。
- 部署：`/opt/kuat-server/service.py`；HTTPS 经既有 Nginx 站点的独立 `/kuat-api/` 路径反代。
- 身份认证完全独立：K.U.A.T账号、scrypt加盐密码哈希、30天会话（仅存令牌哈希并绑定设备）。不调用IDEA认证，不接受IDEA令牌，无注册接口。
- GET `/workspace` 返回 world / entries / timeline / state / archive 五类云端档案。
- PUT `/documents/{name}` 请求 `{data,revision}`，旧版本返回409，避免静默覆盖其他成员修改。
- 管理员 GET/POST/PUT `/accounts` 列出、创建、停用/启用、重置成员密码，客户端有“账号管理”入口；仅可发放editor/reader，不能通过接口增加其他管理员。POST `/password` 修改本人密码并撤销既有会话。
- 首次Owner通过一次性授权 `/setup` 创建；授权仅通过SSH交给当前设备，初始化后服务端和客户端删除授权文件，后续无法再次初始化。普通客户端不会出现初始化入口。账号只能由管理员创建后自行发放。
- 所有数据接口需Bearer身份和设备ID；匿名仅能读取 `/health`。数据响应no-store。

客户端启动时恢复系统加密保存的登录态并验证服务端会话；密码不保存。创作数据只保存在进程内存，不写入客户端JSON、localStorage或持久化浏览器会话。关系图与待办在短暂合并修改后上传，界面显示保存状态；失败弹窗阻止继续修改，未确认保存时阻止关闭。概览、词条、时间线保存等待服务器确认。网络不可用时不降级到本地存储。

首次迁移脚本 `scripts/export-cloud-migration.cjs` 仅读旧数据并生成一次性迁移包。通过SSH上传并以 `service.py --init` 导入；已有云端数据时拒绝覆盖。当前迁移包含108词条、1事件、27来源及关系图状态；上传后全档案规范化SHA256校验一致。旧版用户档案未删除，以便登录确认后处理；新版不读取旧版档案。

测试：`python tests/cloud-server.test.py`；共享Electron运行 `tests/cloud-ui.cjs`（隔离内存接口，使用源资料夹具）。旧UI测试针对本地版，不适用于云端登录启动流程。

边界：当前共创是共享文档及乐观并发控制，尚无实时光标、逐字段自动合并或客户端自动轮询更新。遇409应保留修改文本并重新载入后处理。

## K.U.A.T 十级身份权限（2026-10-05）

| 级别 | 中文 / 别名 | 可修改模块 |
| --- | --- | --- |
| Infinite | 最高 | 全部创作模块 |
| Orange | 创始 / Origin | 全部创作模块 |
| White | 白羽 / Shiro | 全部创作模块 |
| Purple | 直属 / Kuat | 全部创作模块 |
| Darkblue | 议会 / Cabinet | 全部创作模块 |
| Red | 红级 / Alpha | 概览、设定、时间线、关系图及待办 |
| Blue | 蓝级 / Beta | 设定、时间线、关系图及待办 |
| Cyan | 青级 / Tech | 设定、时间线、关系图及待办 |
| Green | 绿级 / Gamma | 只读 |
| Grey | 灰级 / Omega | 只读 |

Infinite/Orange/White同级；Purple不受议会管理。当前能力仅涉及创作模块，未实现组织隶属审批和科研专用模块，因此部分身份具有相同可操作范围。这是软件权限映射，不新增世界设定。所有获准账号当前均可阅读整个创作空间，不存在作者层保密隔离。

账号发放/停用/重置/分级仅由最初初始化的空间所有者操作，与上述级别分开；授予Infinite也不会授予发号权。初始所有者迁移为White，既有editor迁移Blue，reader迁移Grey。成员改级撤销其既有会话，服务端每次写入均校验具体模块。权限配置唯一来源server/permissions.py，客户端读取服务端元数据。账号管理有十级选择与改级按钮，顶栏显示身份配色。

## 登录态保存（2026-10-05）

新登录会话有效期为固定30天，启动时自动校验并载入云端。客户端使用Electron safeStorage（Windows系统用户加密）保存令牌、设备标识和到期时间至用户数据目录LoginSession.bin，不保存密码；创作数据仍只在云端。网络失败保留凭据并提供重试；到期或服务端撤销后重新登录。顶栏退出登录先保存待提交画布状态，再撤销当前会话并清除本机凭据；网络异常时提示重试。修改密码、停用或改级继续撤销相应会话。首次使用新版需登录一次，旧的内存会话无法恢复。
