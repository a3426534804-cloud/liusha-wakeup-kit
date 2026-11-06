# 本次开源提取的验证

审计与验证日期：2026-11-06。

## 已执行

- 30 项 Python 标准库单元 / HTTP 测试通过。
- 真实提取的 usage 函数与醒迹日期函数使用虚构文件测试：指定旧日期超过 60 条仍完整返回；账本响应剔除正文和标识符。
- 核对累计 CLI 费用按轮差分、token 按轮保留、不把未知费用和部分输出当成 0。
- 验证过期、忙锁、配置变化、无真实活动、意外大创建等策略分支。
- 验证未鉴权用量请求 401、POST 只读端点 405、非法 offset 400、源码文件路径 404。
- 验证 GET 用量后 JSONL 字节未变化，不触发模型调用。
- 本地浏览器检查 390×844、320×740；分页、来源过滤、三套主题切换、details 明细和无横向溢出通过。
- README 图片来自合成数据；生产原始下载文件不在仓库目录内。
- 发布白名单与常见密钥 / 私人路径扫描通过。JavaScript 语法检查通过。

Windows 本机沙箱的系统临时目录权限受限，测试改用项目 `var/test-temp` 目录后通过。该目录被忽略，不进入公开仓库。生产提取版补了一个 SQLite connection 的显式关闭，以便 Windows 测试能清理虚构数据库。

## 没有据此宣称的事情

本次没有开启新的生产 timer，没有调用真实模型测试节省额度，没有验证所有 Claude Code / 订阅 / 供应商组合，也没有把原站完整记忆与压缩引擎公开。

GitHub Actions 配置提供 Linux Python 3.11 测试与发布扫描。远端执行情况以仓库 Actions 页面为准，不能用本地通过替代远端结果。

## 读者可复现

```bash
python -m unittest discover -s tests -v
python scripts/audit_release.py
python -m compileall -q liusha integrations
python -m liusha.server --demo
```

生产算法与便携策略模块有所区分；`docs/provenance.json` 记录源代码 SHA-256、公开提取文件 SHA-256 和做过的参数化改动。哈希用于追踪提取，不含任何原始部署数据。
