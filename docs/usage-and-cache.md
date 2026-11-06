# 流沙里的用量、对话命中与费用

## 先看字段，不看累计大数字猜原因

| 原始字段 | 流沙含义 |
| --- | --- |
| `input_tokens` | 不从缓存读取、也不计入缓存创建的直接输入 |
| `cache_read_input_tokens` | 这次实际读取的缓存输入 |
| `cache_creation_input_tokens` | 这次写入缓存的输入；会产生消耗 |
| `output_tokens` | 输出 tokens；部分回报时显示未知 |
| `context_input_tokens` | 最近 assistant 的本轮上下文输入规模；不等于多轮账本输入累计 |
| `num_turns` / `duration_ms` | 轮次与耗时 |
| `source` | chat、bubble、cc_wakeup、cc_wakeup_setup、cache_keepalive |
| `usage_partial` | 中途停止等场景，尚未完整回报 |

```text
本次总输入 = cache_read + cache_creation + input
本次输入读取比例 = cache_read / 本次总输入
时段读取比例 = Σcache_read / Σ本次总输入
```

最后一个比例不能用每条百分比的简单平均代替。一次 100-token 调用与一次 100,000-token 调用不是同样的权重。

## 命中不是二选一

例如读 64,000、建 800、直接输入 20，表示大部分前缀命中，同时新内容仍在写缓存。创建量增加不代表整个历史被重建。

界面的“部分命中 / 大量创建”与“无读取 / 新建”是启发式标签。归因要结合会话切换、model/effort、系统协议、MCP、附件和压缩事件；不能只凭一个阈值声称冷重建。

## result usage 与累计费用不能混算

当前生产 CLI 路径的 token usage 按轮回报，`total_cost_usd` 在同一进程中可能为累计值。

```text
单次费用 = max(0, 本次累计费用 - 上次累计费用)
新进程开始时，累计费用基线归零
token 字段保留本次 result 的值，不再做累计差分
```

如果换供应商或 CLI 版本，要先测语义；不要把这条规则强行套到逐请求 API。原生流式输出中 replay acknowledgement 也不能当作一次额外模型调用。

## 四种费用不要混成一个金额

1. 官方 API 实际计费：最终以供应商账单为准。
2. CLI 根据模型价格给出的估算：可用于逐轮分析。
3. 静默探测的输入下限估算：输出与部分 usage 可能未回报。
4. Claude 订阅套餐使用率：是另一套窗口和配额，不是美元估算的线性换算。

演示里采用示意模型与示意价格，只用于看 UI。生产提取版里遗留的某个模型价格也不能当作当前通用价格。[官方价格说明](https://platform.claude.com/docs/en/about-claude/pricing)应在部署时单独核对。

## 统计接口如何实现

`integrations/production/usage_ledger.py` 保留实际算法：

- 扫完整 usage 文件，先日期过滤，不只取尾部 60 条。
- 对历史写成 chat 的自动消息，用 relay message ID 关联自动消息标记，在服务端重新归类为 bubble。
- 按来源、模型汇总；逐页返回 15 条；返回前去掉消息 ID、会话 ID、wake ID。
- 读取当前会话 transcript 的 `compact_boundary`，展示压缩前后 tokens 与耗时。
- 根据会话 + 调用时间窗口为最近一页关联 tool_use 名称；不返回参数、正文或思考内容。

工具关联使用时间窗口，是尽力归属，不保证重叠调用时完全准确；更严格的实现应记录 request/call UUID。压缩列表只属于当前会话，不是所有历史会话的完整压缩史。

独立版 `liusha.usage.ledger` 额外明确了未知费用、无时区时间与坏 JSON 行的处理；summary 属于当前筛选，period_summary 属于时段全来源。生产提取版沿用原页面的全来源 summary，嵌入时不要混用作用域。

## 安全与性能

真实账本是私密运营数据。统计 API 需要鉴权和 `Cache-Control: no-store`，不应返回模型输入输出。演示是标准库只读服务，没有执行调度的 POST 接口。

当前提取版扫描 JSONL 的复杂度为 O(n)。高流量部署可把逐次记录写入 SQLite，按时间、来源与会话建索引；不要为优化延迟又回到“只看最后 60 条”。轮转文件也需要跨文件统计。
