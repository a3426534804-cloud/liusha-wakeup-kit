# 接入已有前端和后端

## 先选择接入深度

**只需要流沙页面**：使用 `liusha.server` 读取自己的 usage JSONL，完全不安装定时任务。没有模型调用权限，也能做用量审计。

**已有聊天 relay / bridge**：把 production 适配器接到你的调用链和状态文件中。它们是成熟运行方案的提取，不附带原站完整 bridge、私人协议、记忆服务和压缩引擎。

**从零做 AI 陪伴站点**：先实现真实聊天 + 会话持久化 + 鉴权 + JSONL usage，再加可见自动消息，最后评估原生唤醒和静默保活。不要只启动一个 keepalive timer 就期望得到完整聊天系统。

## A. 用量采集

普通调用结束时写一行：

```python
from liusha.usage import ResultUsage
collector = ResultUsage()  # 每个 CLI 进程一份，不要跨进程共用累计成本基线
row = collector.record(result_event, at=finished_iso,
    source="chat", model=actual_model, effort=actual_effort,
    last_assistant_usage=last_assistant_usage)
# 由你的单一日志写入者 / 持锁函数 append 到 token_usage.jsonl
```

不要把 result 的累计费用原样叠加进账本。上游 usage 字段若缺失，保留未知。需要从 relay 重归类冒泡的旧记录，可以使用生产版 `_usage_ledger`；新实现优先在调用源头写 `source=bubble`。

## B. 生产用量接口

`integrations/production/usage_ledger.py` 是实际后端函数提取。它的全来源 summary 与筛选 items 作用域沿用原站。`liusha.usage.ledger` 的 summary 是当前筛选，另提供 period_summary；前端要选择一套契约，不能混用。

FastAPI 示例（FastAPI 由你自己的应用提供，本仓库演示不依赖它）：

```python
import asyncio
from fastapi import Request
from integrations.production.usage_ledger import _usage_ledger

@app.get("/app/usage_ledger")
async def usage_ledger(request: Request, period: str="24h", source: str="all", offset: int=0):
    check_auth(request)  # 复用已有登录，不创建另一套公开口令
    return await asyncio.to_thread(_usage_ledger, period, source, offset)
```

为 API 添加 no-store。`LIUSHA_TRANSCRIPT_DIR` 指向你自己的单个项目 transcript 目录，不应由用户请求传入任意文件路径。数据库缺失会失去旧冒泡重归类；transcript 缺失会失去工具名和压缩事件，但不应让普通账本无法读取。

## C. 原生会话的 bridge 契约

`cc_native.Runtime(bridge)` 需要以下成员。字段名和行为保留原生产代码，以便逐项迁移：

| 成员 | 作用 |
| --- | --- |
| `CLAUDE_SESSION_ID` | 已验证、可 resume 的现有会话 ID |
| `CLAUDE_RUN_LOCK` | 与静默保活相同路径的跨进程锁，支持 acquire/release/locked |
| `_CLAUDE_PROJECT_CWD`、`UPLOAD_DIR` | 你的项目与上传目录 |
| `MODEL_MAP`、`_DEFAULT_MODEL` | 模型选择映射 |
| `OUTPUT_PROTOCOL_PROMPT` | 与真实聊天一致的完整输出协议 |
| `VisibleOutputParser()` | 流式 feed / finish，返回 summary、answer 等事件，保留 raw |
| `_build_prompt_with_attachments` | 真实消息与附件组装 |
| `ambient.get()` | 由现有 bridge 提供的上下文，可为空 |
| `bridge_support.pending_context / mark_synced` | 私有上下文同步，接入者自行实现；没有该能力可显式返回空内容 / 空 ID 列表 |
| `send`、`send_work_status` | relay / SSE 的发送函数，保留消息与会话归属 |
| `_log_usage` | 统一 JSONL 写入入口 |
| `_ACTIVE_PID_FILE` | 与自动冒泡脚本使用同一个活动 PID 标记 |
| `_check_runaway`、`_work_tool_details`、`_safe_work_text` | watchdog、状态提取与安全日志 |
| `subprocess` | 标准库 subprocess 模块 |

`handoff()` 是原应用压缩系统的可选扩展点，运行时导入 `continuity_compaction`。本仓库不包含私人记忆和压缩引擎；不使用此扩展时不要调用 handoff。常规唤醒、事件归属和 usage 不依赖调用该扩展。

`wake_visible_output()` 与 `Turn` 仍可单独研究和单元测试。当前原生运行时的实际能力必须由 init / control response 验证；不是创建同名 Python 函数就能获得 `ScheduleWakeup`。

## D. 静默保活的精确接法

1. 在自己的运行目录准备 `state/claude_session.json`，仅把真实建立的会话标为 established。
2. 设置 `.env.example` 中的根目录、模型 CLI、项目目录、协议文件、MCP 配置和锁路径。
3. 普通聊天与静默探测必须完全使用同一个 `LIUSHA_CALL_LOCK`。
4. 核对生产原型的 `command()` 与普通聊天前缀：model、effort、输出协议、MCP 内容、allowed/disallowed tools、附加目录都一致。
5. 运行 `--check` 只做决策；只有自己的真实聊天 usage 足够新鲜时才尝试一次受控实测。
6. 一次实测失败先停下，不要按分钟重复试错。

公开版为独立接入提供 `liusha.locking.GlobalClaudeLock`，并让静默脚本使用它。**它不会自动改造你原来的普通聊天锁**。若已有兼容的跨进程锁，可让两边使用现有锁并记录配置。

## E. 唤醒开关

控制 API 应鉴权、严格要求布尔值、同目录原子写入偏好文件，并设置权限 0600：

```json
{"enabled": false, "updated_at": "2026-01-01T00:00:00Z"}
```

关闭操作只写偏好，不应专门调用模型“请你关闭自己”。提取版 night_watch 会检查文件并等待安全时机停止；接口返回 desired_enabled，状态页显示实际 enabled 和 status，避免先显示成功但实际任务还在跑。

## F. 醒迹日期

`awakening_records.py` 保留“先遍历完整日志，按北京时间选定日期，再应用无日期查询的旧 limit”逻辑，修复早期只有 60 条的问题。对 day/month 做严格日期验证。醒迹含思考与答复，比用量账本更敏感，必须只对本人鉴权开放。

其中 `_read_idle_script_status` 是明确标记的适配占位：接入者需要从自己的 systemd/journal 与 relay DB 读取检查状态，未适配时显示 unknown，不假装脚本已运行。

## G. 嵌入前端

原有页面提取在 `integrations/frontend/`，依赖 `$`、`authHeaders`、`API_BASE`、`handle401`、`showToast`、`scrollEl` 等宿主函数。醒迹还复用 `makeThinking` 和 `makeMessage`。这些不是独立全站页面，完整依赖表见 JS 源码与 [design](design.md)。

独立美化版使用 `/api/usage` 和 `/api/runtime`，仅需对齐数据结构。默认资产和 API 都从域名根路径加载；部署在 `/liusha/` 时要统一调整 index.html 的资产 URL 和 app.js 的 API 前缀，或使用专用子域名。
