# 飞牛原生应用封装

FnSVN 使用飞牛原生 FPK 格式，应用中心直接管理 Python Web 服务及其 SVN 子进程，运行身份为专属应用用户 `fnsvn`。只支持 x86-64 飞牛设备；不支持 ARM。Web 管理端口为 18880，SVN 端口默认3690，由安装向导配置。

## 构建和安装

在 Windows 项目根目录运行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build-fpk.ps1
```

脚本调用 `build-runtime.py` 获取并准备 Debian bookworm amd64 原生 Python、Subversion 和动态库，复制 `server.py`、`static` 到 `fnos/app`，最后调用官方 fnpack 1.2.3 生成 `dist/fnsvn.fpk`。运行库已就绪时可用 `-SkipRuntimeBuild`，用 `-PythonPath` 指定开发机 Python，用 `-FnpackPath` 指定本地官方打包工具。

通过飞牛应用中心手动安装该 FPK，在向导填写SVN端口和仓库根目录绝对路径，并选择是否启用网页管理登录验证（默认启用）。端口范围1024–65535；目录留空使用应用私有目录，自定义目录须事先授予应用用户读写权限。点击入口会在飞牛内部iframe窗口打开管理页；启用登录验证时需设置独立管理员账号，再创建仓库、用户和授权。SVN客户端使用管理页显示的实际端口连接。

配置保存到 `${TRIM_PKGVAR}/service-settings.json`，包含 `svn_port`、`repos_path`、`svn_host`、`web_auth_required`，由包内helper及后端读取；不作为Shell代码执行。安装、配置、升级向导均提供端口、目录及网页登录验证字段；配置及升级留空保留已有端口和目录，登录验证默认选择「保留当前设置」。旧配置缺少 `web_auth_required` 时按启用处理，Web设置的连接IP/域名也会保留。修改端口后在应用中心重新启动。后端不自动换端口，避免客户端地址悄然改变。

安装后也可在网页「系统设置」启用或关闭网页管理登录验证。关闭后，任何能访问 18880 管理端口的人均可直接管理仓库、账号及系统设置；这不是复用或验证飞牛登录会话。此开关只影响网页管理，SVN 客户端仍按仓库账号及权限认证。

安装后可在 FnSVN 网页「系统设置」修改 IP/域名与仓库目录；IP/域名只控制生成的客户端URL，不改变0.0.0.0监听。目录变化由后端暂停SVN服务、hotcopy、核对UUID/版本并切换，源目录保留。失败恢复原服务和配置；目标若有复制副本会明确提示。已有仓库不允许用飞牛向导直接切到其他目录，应使用该迁移流程。

标准入口为HTTP iframe，本地已验证跨端口嵌入的认证和操作；飞牛HTTPS桌面可能受混合内容策略限制，需要后续同域HTTPS网关支持及实机验证。

包内 `runtime/bin` 提供 `python3`、`svn`、`svnadmin`、`svnlook`、`svnserve` 启动包装器；`runtime/root` 提供对应原生程序、Python 标准库和动态链接库。运行库包装器使用自带 Linux 动态加载器及私有库路径，不安装全局系统软件。安装及升级回调只修正应用目录内的执行权限并检查运行库，不需要 root。

## 运行控制和数据

`cmd/main` 将日志写入 `${TRIM_PKGVAR}/logs/fnsvn.log`，记录 PID 到 `${TRIM_PKGVAR}/fnsvn.pid`。启动后检查真实健康端点；状态检查同时验证 PID、进程用户、命令行中的应用路径和健康端点，避免误认其他进程。停止先发 TERM，让后端关闭 SVN 子进程；超时再终止主进程。

配置及备份位于 `${TRIM_PKGVAR}/data`，默认仓库位于该目录下的 `repos`；自定义仓库目录独立保存。数据不写入升级时替换的 `${TRIM_APPDEST}`。对自定义目录以应用用户执行读写、重命名探测，不递归修改所有权或权限。Web迁移不会删除原仓库；目标不能存在同名仓库，Linux采用renameat2无覆盖发布复制结果。

更新前停止应用并备份数据目录。卸载前单独备份仓库和配置；数据是否保留取决于飞牛卸载选项，不能把保留行为当作保证。入口可见性由飞牛控制，Web 管理是否需要独立登录由上述配置决定。

## 官方格式依据与验证边界

- [Native 应用案例](https://developer.fnnas.com/docs/examples/native/)
- [运行时环境](https://developer.fnnas.com/docs/core-concepts/runtime/)
- [应用框架及生命周期](https://developer.fnnas.com/docs/core-concepts/framework/)
- [应用入口](https://developer.fnnas.com/docs/core-concepts/app-entry/)
- [应用权限](https://developer.fnnas.com/docs/core-concepts/privilege/)
- [环境变量](https://developer.fnnas.com/docs/core-concepts/environment-variables/)
- [fnpack](https://developer.fnnas.com/docs/cli/fnpack/)

本地 FPK 打包通过只证明包结构检查通过。原生二进制 ABI、CPU、内核及目标系统安全策略仍需在真实 fnOS 设备验证；必须继续检查安装、启动停止、应用入口、SVN 提交、重启持久化及升级保留数据，才能确认实机可用。
