# 流沙美化与前端实现

## 设计目标

延续原有清淡、纸感、细线和克制留白的视觉：费用可见，但不让数字像报警面板。卡片分成概览、读写统计、静默保活状态、逐次调用四段。英文仅做辅助信息，不影响中文阅读。

独立演示的资产全部在 `web/`：没有远程图片、网页字体、UI 框架或构建工具。手机默认单列主结构、统计两列；桌面统计四列。使用原生 select、details 和 dialog，保留键盘焦点、减少动态效果支持与 320px 小屏布局。

## 两份 UI 的区别

- `integrations/frontend/usage-page.*`：原站生产页面的提取，DOM ID 和原接口保持一致。
- `web/`：新增的独立美化演示，适合单独运行或作为参考；数据契约见 `/api/usage`。

生产提取不包含头像、主页水彩、聊天壁纸与任何外部创作者的素材。公开截图只使用合成调用记录。

## 语义颜色变量

| 独立 UI | 原站语义变量 | 用途 |
| --- | --- | --- |
| --bg | --nc-bg | 页面底色 |
| --paper | --nc-card | 纸感卡片 |
| --ink | --nc-text | 正文与主要数字 |
| --soft | --nc-muted / --nc-faint | 说明、日期 |
| --accent | --nc-accent | 状态点、读取进度 |
| --line | --nc-line | 卡片边框、分隔线 |
| --wash | --nc-accent-soft | 柔和衬底 |

嵌入旧样式时可在页面根节点做变量别名：

```css
#usagePage {
  --text: var(--nc-text);
  --text-soft: var(--nc-muted);
  --text-faint: var(--nc-faint);
  --card-bg: var(--nc-card);
  --card-line: var(--nc-line);
  --hairline: var(--nc-line);
  --accent: var(--nc-accent);
}
```

不要把聊天中某个气泡的局部变量当作整个账本的主题来源。前端截图的主题切换也要检查下拉框、部分回报标签和错误提示。

## 数据显示约定

读取比例进度条只表示输入 token 比例，不表示套餐剩余额度。费用小数在合计和逐条处分别处理；未知费用单列计数。部分 usage 显示 ≥ 和“未完整回报”。工具列表、记录字段统一转义，不能把日志字符串当 HTML 信任。

静默保活卡片展示 checked_at、status 与 next_due，不用一个绿点宣称“已永久保温”。当前保活来源、实际读写量仍需要查看逐次记录。

## 请求和分页

页面第一次加载并行结构准备、随后只读 GET；切换 period / source 重置 offset；“再看一些”追加 15 条；刷新按钮禁用重入。不存在任何后台调用模型的 UI 隐式行为。

本版测试覆盖 390×844 与 320×740 下的边界溢出、来源过滤、更多分页和皮肤切换。截图见 `docs/images/`，运行说明见 [validation](validation.md)。
