# SVN 提交时再次要求登录

FnSVN 管理网页的管理员登录与 SVN 客户端登录是两套认证。客户端应使用管理页“用户管理”中创建的 SVN 用户，并在对应仓库获得“读写”权限。登录网页不会自动登录 TortoiseSVN；能够浏览或检出仓库，也不代表拥有提交权限。

## 当前服务端配置

由 `Store.create_repo()` 写入每个仓库的 `conf/svnserve.conf`：

```ini
[general]
anon-access = none
auth-access = write
password-db = /应用数据目录/config/passwd
authz-db = /应用数据目录/config/authz
realm = FnSVN
```

`Store.sync()` 生成共享用户密码文件和各仓库权限。`auth-access = write` 还要同时满足 `authz` 中该用户的 `rw` 权限；只有 `r` 的用户不能提交。所有仓库使用固定的 `FnSVN` realm 与共享用户库，没有每次提交轮换 realm、禁用客户端缓存或提交专用密码的逻辑。不要通过开放匿名写入解决重复登录问题。

## 建议排查顺序

1. 在仓库权限中确认提交所用 SVN 用户是“读写”，而不是“只读”。只读账号可以成功检出，提交会被拒绝；部分图形客户端可能随后再次请求其他凭据。
2. TortoiseSVN 登录时勾选“保存认证信息”。只需在自己的可信电脑上保存。命令行若使用 `--no-auth-cache`，这次登录不会为下一次操作保存认证信息。
3. 始终使用同一个仓库连接地址。`svn://192.168.x.x:3690/game` 与 `svn://svn.example.com:3690/game` 使用不同的缓存标识；更换主机名、端口或 realm 后首次访问需要重新认证。设置新的对外地址只影响后续复制的连接，已有工作副本需要使用客户端“重新定位 / Relocate”更新地址。
4. 修改过 SVN 用户密码后，重新输入新密码并保存。只有确实保存了旧凭据时，才在 TortoiseSVN“设置 → 已保存数据 → 认证数据 → 清除…”中选择该服务器清理；无需清除所有服务器的凭据。
5. 如果每次操作仍然提示，检查客户端运行时设置是否禁用了认证保存：`store-auth-creds = no` 会关闭认证保存，`store-passwords = no` 会关闭密码保存。不同客户端或不同操作系统用户可能使用不同的认证存储。

仍有问题时需要客户端名称和版本、完整的提交错误，以及检出与提交所用的仓库 URL（可以隐藏真实域名/IP，但应说明两者是否一致），才能区分认证失败与写权限不足。不要发送密码或认证缓存文件。

## 本地验证范围

2026-10-19 使用 Windows 上的 Subversion 1.14.5 和当前 FnSVN `Store` 生成的配置，完成了以下真实 `svnserve` 回归验证。每项使用独立的临时服务器、ASCII 路径和专用 `--config-dir`；没有读取或修改用户原有 SVN 认证缓存。服务进程由测试直接持有并在结束时关闭。

| 场景 | 实际结果 |
| --- | --- |
| 首次 checkout 带用户名密码并允许缓存，后续独立进程 commit 不传凭据 | 连续两次提交成功，版本分别到 r1、r2 |
| 只读用户 checkout 后新增文件并 commit | `E170001: Authorization failed`，写权限拒绝 |
| 已缓存 `127.0.0.1` 后改用 `localhost` | 无凭据访问失败；重新提供凭据后成功，生成另一份缓存 |
| 服务端修改用户密码 | 旧缓存认证失败，新密码认证并保存后后续无凭据访问成功 |
| 服务端 realm 改变 | 原缓存不匹配，重新认证后恢复 |
| 初次认证使用 `--no-auth-cache` | 下一独立进程无凭据访问失败 |

对应测试：`tests/test_auth_cache.py`。这些结果证明当前配置支持客户端复用已保存的认证，不能据此断言真实 NAS 或用户实际客户端已经排除故障。用户表示服务器已卸载后，不再进行额外测试。

## 官方资料

- [TortoiseSVN：认证与保存认证信息](https://tortoisesvn.net/docs/release/TortoiseSVN_en/tsvn-dug-general.html#tsvn-dug-general-auth)：登录对话框的勾选项用于保存认证；Windows 使用系统加密接口保护缓存密码。
- [TortoiseSVN：已保存数据设置](https://tortoisesvn.net/docs/release/TortoiseSVN_en/tsvn-dug-settings.html#tsvn-dug-settings-saved-data)：可选择清除某个服务器的认证信息。
- [Apache Subversion FAQ：凭据缓存](https://subversion.apache.org/faq.html#plaintext-passwords)：说明系统凭据存储及 `store-passwords`、`store-auth-creds` 的作用。
- [Subversion Book：svnserve 配置](https://svnbook.red-bean.com/en/1.8/svn.serverconfig.svnserve.html)：解释 realm、共享密码库及认证和授权规则如何共同生效。
