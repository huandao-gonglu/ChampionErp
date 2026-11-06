# 服务器部署与桌面数据切换

## 当前部署

2026-11-06 已将统一业务后端部署到 `107.174.48.241`，并按用户选择迁移原桌面目录的数据。项目工作目录中的开发数据库未迁移。

| 内容 | 位置 |
| --- | --- |
| 当前发布 | `/opt/champion-erp/releases/20261106-093745` |
| 程序入口 | `/opt/champion-erp/current`，指向当前发布 |
| Python 环境 | `/opt/champion-erp/venv` |
| 业务数据 | `/var/lib/champion-erp` |
| 服务 | `champion-erp.service`，开机自启，以专用账号 `champion-erp` 运行 |
| 服务配置模板 | [`deploy/systemd/champion-erp.service`](../deploy/systemd/champion-erp.service) |
| 后端监听 | `127.0.0.1:5050`，仅服务器本机可访问 |
| 本机访问 | <http://127.0.0.1:15050>，通过 SSH 转发至服务器 |
| 依赖约束 | 当前发布目录的 `deployment-constraints.txt` |

没有域名时，使用 SSH 加密通道连接；ERP 端口未对公网开放。本机网页和桌面客户端读取同一份服务器业务数据。桌面关闭后，服务器继续运行。

本机通道由 `/Users/hdgl/Library/LaunchAgents/com.champion.erp.cloud-tunnel.plist` 管理，用户登录后自动建立，断线后自动重新连接。日志位于 `/Users/hdgl/Library/Application Support/Champion ERP/logs/cloud-tunnel.log` 和 `cloud-tunnel.error.log`。通道使用现有 SSH 密钥，不上传私钥，也不转发 SSH Agent。

`/Users/hdgl/Library/Application Support/Champion ERP/connection.json` 已设置 `backend_url=http://127.0.0.1:15050`；`/Applications/Champion ERP.app` 已更新为独立执行器版本。直接正常打开桌面程序即可。浏览器资料和本机模型配置继续保存在原电脑；安装程序为本机构建，尚未签名、公证。

其他电脑不能使用这台 Mac 的回环地址；需要自己的 SSH 授权及相同转发端口。后续使用域名公开部署时，再配置 HTTPS、访问控制和对应的 `ERP_PUBLIC_URL`，不直接公开当前业务端口。

## 迁移与验收

数据来源为 `/Users/hdgl/Library/Application Support/Champion ERP/backend`。切换前已正常停止旧桌面及其业务后端，并使用 SQLite 一致性备份生成快照。

- 保留 2 个商品、2 个平台草稿、2 个发布任务和 15 条对话历史，以及原有授权与业务凭据。
- 启动服务器业务进程前，逐一核对 10 个 SQLite 数据库的表记录数量、主键摘要和完整性；均通过。
- 113 个素材文件逐一核对 SHA256；原桌面目录下的持久化路径已转换到服务器业务目录。
- 已通过本机 SSH 通道验证网页、商品接口及 3 张真实商品图片；重启服务器服务后仍能读取 2 个商品。
- 已通过实际安装包中的执行器验证服务器请求/响应及本机配置读取，关联 1 个本机模型；本次验收未调用模型推理、浏览器界面或店铺写入。

迁移报告位于 `/var/lib/champion-erp/desktop-migration-report.json`，服务器另存 `/srv/champion-erp/migration-20261106-093745.json`。

跨电脑迁移时，上传目录与数据库中记录的原电脑目录不同，应使用迁移器的 `--source-prefix`：

```sh
python scripts/migrate_desktop_business.py \
  --source '/服务器/来源快照' \
  --source-prefix '/Users/hdgl/Library/Application Support/Champion ERP/backend' \
  --target '/新的业务目录' \
  --backup '/新的备份目录' \
  --local '/迁移输出/本机配置'
```

`--source-prefix` 只指定待转换的原始路径前缀；备份仍读取 `--source`。本机配置输出应通过 `desktop_runtime.config.LocalConfig` 保存回原电脑，浏览器登录资料继续留在原电脑。已部署的数据不要再次执行迁移。

## 运维

登录服务器后执行：

```sh
systemctl status champion-erp.service
journalctl -u champion-erp.service -n 100 --no-pager
systemctl restart champion-erp.service
```

应用日志另存 `/var/lib/champion-erp/data/logs/backend.log`。代码与虚拟环境由管理员维护，业务数据归专用服务账号。新发布应使用独立发布目录，保留实际使用的依赖约束，并在切换前验证该账号能读取代码。

本机通道状态可用 `launchctl print gui/$(id -u)/com.champion.erp.cloud-tunnel` 查看；需要重建时使用 `launchctl kickstart -k gui/$(id -u)/com.champion.erp.cloud-tunnel`。服务器重启会结束当时的桌面连接，重新打开桌面程序后建立新连接；进行中的本机操作不自动重放。

## 备份与恢复边界

本次切换备份保存在两个独立位置，均包含业务配置和凭据，应保持目录私有：

- Mac：`/Users/hdgl/Library/Application Support/Champion ERP/migration-backups/20261106-093745`。`source/` 是来源快照，`manifest.json` 记录数据库与素材摘要，`Champion ERP.before-cloud.app` 是切换前的安装程序。
- 服务器：`/srv/champion-erp/backups/20261106-093745`，保存迁移前数据。原上传快照另留在仅管理员可访问的 `/srv/champion-erp/staging/20261106-093745/source`。

这些是切换时的快照，不包含切换后新增的业务记录。恢复前先停止写入并另存当前服务器数据；从备份恢复到新的目录，校验后再切换 `ERP_APP_DIR`，不要覆盖现有目录。程序回退与数据恢复分别处理，不能把旧桌面库重新启用为另一套同时写入的业务库。
