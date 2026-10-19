# FnSVN

为飞牛 fnOS 打造的原生 Subversion 管理应用。FnSVN 以 FPK 应用包安装，自带 Python 与 Subversion 运行库，在应用中心完成启动、停止和运行状态检查，并通过中文 Web 控制台管理仓库、SVN 账户与访问权限。

当前版本：**0.4.0** · 平台：**x86-64 fnOS** · 许可协议：**MIT**

## 功能

- **仓库管理**：创建仓库，查看最新版本号、UUID、目录内容和最近 30 条提交日志；支持仓库改名和输入名称确认删除。
- **账户与授权**：独立创建 SVN 账户、修改密码、删除账户，并按仓库根目录授予只读（`r`）或读写（`rw`）权限。
- **备份**：为单个仓库生成完整 SVN dump，在线查看备份列表并下载。
- **接管已有仓库**：扫描飞牛上的绝对目录，识别标准服务端仓库，通过 `svnadmin hotcopy` 复制接管；源仓库保持不变。
- **存储迁移**：在系统设置中切换仓库目录时暂停 SVN，复制并核对 UUID 与最新版本后切换；失败时恢复原配置和原服务。
- **Web 管理安全**：首次使用创建管理员，密码使用 scrypt 散列保存；支持登录会话、登录限速、CSRF 请求标记和可选的 Web 登录验证开关。
- **原生集成**：提供飞牛应用中心入口、iframe 管理页、安装/升级/配置向导，以及独立的服务日志和持久化数据目录。

## 环境要求

- x86-64 架构的飞牛 fnOS 设备，当前版本不支持 ARM。
- 管理端口 `18880` 与配置的 SVN 端口保持空闲，端口范围为 `1024–65535`。
- 自定义仓库目录需要事先授予 `fnsvn` 应用用户读写权限。

安装包已经包含 Debian 12 amd64 的 Python 3.11、Subversion 及私有动态库，无需在 NAS 上额外安装运行环境，也不会修改系统级软件包。

## 安装

当前仓库提供源码与 FPK 构建脚本。先生成应用包：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build-fpk.ps1
```

产物为 `dist/fnsvn.fpk`。在飞牛应用中心选择手动安装并上传该文件，然后按向导完成配置：

1. 设置 SVN 端口，默认 `3690`。
2. 设置仓库根目录；留空时使用应用私有目录 `data/repos`。
3. 选择是否启用 Web 登录验证，默认启用。
4. 启动 FnSVN，从应用卡片或桌面入口打开管理页，也可直接访问 `http://NAS地址:18880`。

启用 Web 登录验证时，首次打开管理页需要创建 Web 管理员。该账户仅用于管理控制台，与 SVN 客户端账户相互独立。

## 快速开始

1. 在「仓库管理」中新建仓库。
2. 在「SVN 账户」中创建客户端账户。
3. 打开仓库的「访问权限」，为用户授予只读或读写权限。新仓库默认拒绝所有客户端访问，包括匿名访问。
4. 复制管理页显示的应用连接地址，在 TortoiseSVN 或命令行中检出：

```bash
svn checkout svn://NAS地址:3690/仓库名
```

默认端口 `3690` 在生成的地址中可以省略；使用其他端口时会保留端口，例如 `svn://NAS地址:13690/仓库名`。

## 已有仓库迁移

在「仓库管理」中选择「扫描已有仓库」，填写飞牛上的绝对路径。FnSVN 会识别含有 `format`、`db`、`conf` 的服务端仓库，并显示 UUID 与版本号。

接管过程会复制仓库到 FnSVN 的仓库目录，保留版本历史和 UUID，源仓库不会被移动或改写。复制完成后：

- 客户端认证切换为 FnSVN 账户体系，需要重新授权。
- 原认证配置保留为 `conf/svnserve.conf.before-fnsvn`。
- 原仓库后续的新提交不会自动同步，切换前应停止向旧仓库写入。
- 已有 hooks 会随仓库复制，并将在 FnSVN 应用用户身份下执行，需要自行检查其依赖。

## 数据与运维

在常规 fnOS 安装中：

| 内容 | 路径 |
| --- | --- |
| 应用代码与运行库 | `$TRIM_APPDEST` |
| 应用数据 | `$TRIM_PKGVAR/data` |
| 服务设置 | `$TRIM_PKGVAR/service-settings.json` |
| 服务日志 | `$TRIM_PKGVAR/logs/fnsvn.log` |
| 默认仓库目录 | `$TRIM_PKGVAR/data/repos` |
| dump 备份 | `$TRIM_PKGVAR/data/backups` |
| 删除仓库回收区 | `$TRIM_PKGVAR/data/deleted` |

应用数据目录中的 `config/` 保存管理员密码散列、SVN 账户和授权配置。svnserve 的内置认证需要可恢复的客户端密码，因此 SVN 密码以私有文件权限保存；请限制该目录的访问。

升级前应停止应用，并备份整个数据目录、`service-settings.json` 和自定义仓库目录。单个仓库的 dump 不包含账户、授权配置和其他仓库，不能替代整应用备份。

删除仓库时，FnSVN 会将仓库移入回收区而不是立即永久清除；dump 备份不会随删除操作移除。

### 恢复 dump

请在独立目录中验证恢复，不要直接覆盖正在服务的仓库：

```bash
svnadmin create /path/to/restored-repo
svnadmin load /path/to/restored-repo < project.svndump
svnadmin verify /path/to/restored-repo
```

## 安全说明

- Web 管理默认使用 HTTP，SVN 使用原生 `svn://` 协议，二者都不加密传输内容，建议仅部署在可信局域网；远程访问请使用 VPN 或受控的安全入口。
- 关闭「Web 登录验证」后，任何能访问 `18880` 管理端口的人都可以管理仓库、账户和设置。该开关不会影响 SVN 客户端认证，也不会复用飞牛登录会话。
- 当前授权粒度为仓库根目录，不提供子目录级 `authz`。
- 暂不支持在线 dump 恢复、定时备份和普通 SVN 用户的 Web 登录。
- 飞牛桌面通过 HTTPS 加载管理页时，浏览器可能拦截 HTTP iframe；当前版本尚未提供同域 HTTPS 网关。

关于 SVN 客户端重复提示登录，请参阅 [认证排查说明](docs/authentication.md)。

## 构建

构建机需要 Python 3.12 与 PowerShell。首次构建会从 Debian 官方 HTTPS 软件源下载 amd64 运行库依赖，按官方索引校验 SHA256，并检查 ELF 架构与动态库依赖闭包；整个过程不会在构建机或 NAS 上执行 deb 安装脚本。

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build-fpk.ps1
```

常用参数：

```powershell
# 运行库已就绪时跳过下载
powershell -ExecutionPolicy Bypass -File scripts/build-fpk.ps1 -SkipRuntimeBuild

# 指定 Python、fnpack 或输出目录
powershell -ExecutionPolicy Bypass -File scripts/build-fpk.ps1 `
  -PythonPath C:\Python312\python.exe `
  -FnpackPath C:\tools\fnpack.exe `
  -OutputDirectory .\dist
```

刷新 Debian 运行库依赖：

```powershell
py -3.12 scripts/build-runtime.py --refresh
py -3.12 scripts/check-runtime.py
```

`scripts/build-fpk.ps1` 使用官方 fnpack 1.2.3；本机未安装时会下载到用户工具缓存。构建后在 `fnos/app/runtime/packages.json` 生成原生运行库的软件包清单，各组件的许可证保留在运行库的 `/usr/share/doc` 中。

## 本地开发与测试

本地需要将 `svn`、`svnadmin`、`svnlook` 和 `svnserve` 放入 `PATH`：

```powershell
py -3.12 server.py --host 127.0.0.1 --data data
py -3.12 -m unittest discover -s tests -v
node --check static/app.js
```

`--web-only` 只启动管理界面而不启动 SVN，适合前端调试。浏览器流程测试位于 `tests/browser.cjs`，需要先启动一个使用全新数据目录的本地服务，并安装 Playwright：

```powershell
node tests/browser.cjs
```

可用环境变量：

| 变量 | 作用 |
| --- | --- |
| `FNSVN_TEST_URL` | 覆盖浏览器测试地址 |
| `PLAYWRIGHT_MODULE` | 指定 Playwright 模块路径 |
| `FNSVN_TEST_IFRAME=1` | 测试跨端口 iframe 嵌入流程 |

后端也支持通过 `--data`、`--port`、`--svn-port`、`--repos` 和 `--settings-file` 指定运行参数。

## 项目结构

```text
.
├── server.py              # 无第三方依赖的 Web 管理服务与 svnserve 生命周期管理
├── static/                # Web 管理界面
├── fnos/                  # FPK 清单、向导、生命周期脚本与界面资源
├── scripts/               # 原生运行库准备、依赖审计与 FPK 构建
├── tests/                 # 设置、认证缓存、HTTP/SVN 集成与浏览器测试
└── docs/                  # 认证排查文档
```

FPK 封装细节、生命周期和数据目录说明见 [fnos/README.md](fnos/README.md)。

## 验证边界

本项目已完成 Windows 本地 SVN 检出、提交、只读拒写、账户认证、dump 备份与恢复、Web 认证和设置保存/迁移约束测试，并对 Linux 运行库的 ELF 架构和动态依赖闭包、FPK 包结构进行了检查。发布前仍应在真实 x86-64 fnOS 设备上验证安装权限、应用启停、桌面入口、实际提交、重启持久化以及升级后的数据保留。

## 许可证

本项目基于 [MIT License](LICENSE) 发布。
