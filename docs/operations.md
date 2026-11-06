# 部署、观察、停用和排错

## 本地只读演示

```bash
python -m liusha.server --demo
python -m unittest discover -s tests -v
```

演示会在 `var/demo/` 生成合成 JSONL 和状态；目录被 .gitignore 排除。它没有 Claude CLI 依赖，不执行真实唤醒，也不会试图访问生产凭据。

## 生产接入前检查

自动任务适配以 Linux + Python 3.11+ 为目标。原脚本使用 fcntl、进程组和 /proc，不应直接拿到 Windows 定时任务中运行。用量只读演示可以在 Windows 运行；生产 ZoneInfo 在部分系统需安装 `tzdata`。

先阅读 [integration](integration.md)，为自己的程序建立 bridge 契约。不要把示例协议当成私人系统提示词的替代。

```bash
python -m venv .venv
.venv/bin/pip install -e '.[integration]'
# 建议在自己的服务账号下准备目录，不要共享真实状态给所有系统用户
install -d -m 700 /srv/liusha-runtime/state /srv/liusha-runtime/logs
```

`.env.example` 复制到受保护的环境文件中；令牌只在本机填写。不要把 .env、自己的 chat-mcp.json 或 established 会话状态提交到 Git。

## 定时器模板默认是 dry-run

`deploy/systemd/` 中 service 的 ExecStart 自带 `--check`。模板只做本地条件判断，不发送消息、不调用模型。

1. 修改账号、路径和 EnvironmentFile。
2. 用 `systemd-analyze verify` 检查你的本地 unit。
3. 在真实调用处取得共享锁，确认所有参与者使用同一锁路径。
4. 手动运行 service，读 decision，不需要立即启用 timer。
5. 一次受控实测证实 usage 正常后，再决定是否移除 `--check`。

只有明确完成上述检查时才启用：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now liusha-silent-cache.timer
sudo systemctl enable --now liusha-idle-message.timer
```

这些命令只在你自己的新部署执行；本仓库发布过程没有在原站运行它们。

## 如何确认真的执行了

```bash
systemctl is-active liusha-silent-cache.timer
systemctl list-timers 'liusha-*'
journalctl -u liusha-silent-cache.service -n 40 --no-pager
journalctl -u liusha-idle-message.service -n 40 --no-pager
```

timer active 只说明检查被安排了，不说明成功刷新了缓存。下一步应在 usage 找到 source、时间、实际读取与创建字段；再核对后续真实聊天。dry-run 不应有新的模型 usage。

## 停用互不混淆

```bash
sudo systemctl disable --now liusha-silent-cache.timer
sudo systemctl disable --now liusha-idle-message.timer
```

原生自主唤醒还需把偏好 enabled 设为 false，并等待 / 确认 runtime 停止安排下一次唤醒。不要仅关闭网页后就认为服务端自动任务停止。

## 排错表

| 症状 | 先检查 |
| --- | --- |
| 只有 60 条，旧日期没数据 | 是否对原日志先截尾再筛日期；day 是否用北京时间 |
| 后台一运行就大量创建 | 会话、model、effort、MCP、系统协议、TTL；先暂停，不反复探测 |
| 静默保活持续 not_due | 最近真实调用或成功刷新仍在窗口内，可能正常 |
| 持续 native_chat_owns_session | 常驻会话持锁；不能偷偷绕开该锁 |
| 配置改后一直等聊天 | 设计行为：等一次真实聊天建立新前缀 |
| 页面显示费用 0 | 上游缺失 usage / partial；未知不能按零宣传 |
| 页面刷新多出模型请求 | 检查统计 API 是否误复用了生成端点 |
| 原生唤醒不可用 | init tools 是否包含 ScheduleWakeup、CLI control 是否成功、当前账号能力 |
| 关闭唤醒还在冒泡 | 原生偏好与 idle timer 是两套开关 |
| 流沙颜色与主题打架 | 是否还使用旧 --accent / --card-bg，变量映射是否统一 |

## 稳定运行建议

日志轮转和备份必须按自己的隐私要求设计。只读接口扫描完整 JSONL，规模大时迁移索引存储。令牌不可放 URL、HTML 或仓库。对外提供 dashboard 时用 TLS + 现有身份认证 + no-store；统计日志也可能包含敏感运营信息。
