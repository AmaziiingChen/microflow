# 前端主题系统重构方案

更新时间：2026-03-30

## 文档目的

本文档用于为 MicroFlow 当前前端界面建立一套可执行、可扩展、可长期维护的主题系统方案。

目标不是只做一轮“换颜色”，而是把当前项目中已经存在的 Apple 风格 UI 基础，升级成真正可以支持以下需求的主题架构：

- 浅色主题持续打磨
- 深色主题后续接入
- 经典 / 深色 / Sepia 等阅读主题扩展
- JS 与 CSS 共享颜色来源
- 后续新增组件不再继续散落硬编码颜色

## 当前判断

### 结论

当前项目的前端主题体系属于：

> 已经具备全局变量雏形，但仍处于“半主题化、半硬编码”的过渡阶段。

这意味着：

- 现在的 UI 视觉基调已经很稳定
- 但还没有真正形成完整的 design tokens 体系
- 如果直接上多主题，会被现有硬编码颜色和 JS 颜色逻辑拖住

### 已有优势

- 在 [frontend/css/styles.css](../frontend/css/styles.css) 顶部已经存在基础变量：
  - `--bg-color`
  - `--card-bg`
  - `--text-main`
  - `--text-sub`
  - `--accent-color`
  - `--border-color`
- 底部悬浮栏、搜索胶囊等区域已经有第二组材质相关变量：
  - `--pill-bg`
  - `--pill-hover`
  - `--btn-bg`
- 整体 UI 美学方向稳定，已经有明确的 Apple HIG 语境。

### 当前痛点

- 大量“魔法颜色”仍散落在业务 CSS 中，例如：
  - `#636366`
  - `#111111`
  - `#EDEDED`
  - `#D5D5D5`
  - `#86868B`
- CSS 中存在多段重复定义，后面的规则会覆盖前面的规则，导致“改了变量但页面没变”。
- 来源筛选色、部门图标色、AI 标签色仍由 JS 直接控制。
- Toast / Modal / Image Preview / RSS TOC 等组件仍有较多硬编码颜色，尚未纳入统一主题入口。

## 重构原则

### 1. 不直接做“全局替换”

本次重构不建议直接把所有 `#FFFFFF` 替换成 `var(--bg-color)`。

原因：

- 相同颜色在不同组件中的语义不同
- 后续深色主题时，不同组件不一定应该一起变化
- 当前样式文件有重复规则，机械替换容易失控

### 2. 使用三层 Token 结构

建议本项目主题系统采用三层结构：

1. `Palette Tokens`
   - 纯色板，不带语义
   - 例如：`--blue-500`、`--gray-900`

2. `Semantic Tokens`
   - 赋予颜色含义
   - 例如：`--color-text-primary`、`--color-bg-surface`

3. `Component Tokens`
   - 某个组件自己的可替换主题入口
   - 例如：`--dock-bg`、`--toast-bg`、`--reader-quote-border`

这是因为 MicroFlow 当前的高级感不只来自颜色，也来自：

- 材质透明度
- 模糊强度
- 边界亮度
- 阴影层级

如果只做语义色层，不足以支撑底栏、Toast、图片预览这类组件。

### 3. JS 不再负责“算颜色”

后续 JS 不应继续：

- 解析 `rgb(...)`
- 动态拼 `rgba(...)`
- 自己维护多主题颜色真值

JS 只应该：

- 返回 token key
- 或读取 CSS 变量
- 或给 DOM 写入 `var(--token-name)`

## 建议文件结构

建议按下面结构落地：

```text
frontend/
├── css/
│   ├── theme-tokens.css      # 全局 palette / semantic / component tokens
│   └── styles.css            # 保留业务样式，逐步改为消费 token
└── js/
    └── app.js                # 只保留 token key 或 CSS variable bridge
```

### 推荐落地方式

1. 新建 `frontend/css/theme-tokens.css`
2. 在 `frontend/index.html` 中先于 `styles.css` 引入
3. 第一阶段只把 tokens 建好，不急着一次性替换所有样式

## 第一版 Token 命名表

下面这份命名表不是最终上限，而是第一批应该优先收编的核心入口。

### A. Base / Palette Tokens

| Token | 当前值 | 当前用途 |
|------|------|------|
| `--white` | `#FFFFFF` | 卡片、详情页、弹层主底 |
| `--black` | `#000000` | 遮罩、阴影、深色叠加 |
| `--gray-25` | `#FAFAFA` | footer、次级浅底 |
| `--gray-50` | `#F6F6F8` | 全局背景 |
| `--gray-75` | `#F5F5F7` | 输入框背景 |
| `--gray-100` | `#F3F4F6` | 元标签浅灰背景 |
| `--gray-150` | `#EBEBF0` | 筛选胶囊未激活背景 |
| `--gray-200` | `#E5E7EB` | 通用边框、分隔线 |
| `--gray-300` | `#D5D5D5` | 占位图标 / 文案 |
| `--gray-400` | `#C7C7CC` | 输入框 placeholder |
| `--gray-500` | `#9CA3AF` | 弱提示、输入提示、禁用态 |
| `--gray-600` | `#86868B` | 次级正文、设置标签、提示文案 |
| `--gray-650` | `#808080` | 列表时间、灰色标签文字 |
| `--gray-700` | `#6B7280` | 次级按钮、说明文案 hover |
| `--gray-750` | `#636366` | 列表标题 |
| `--gray-800` | `#4B5563` | 副文本主色 |
| `--gray-850` | `#374151` | 阅读正文基础色 |
| `--gray-900` | `#1D1D1F` | 图片预览按钮 / Toast 主文案 |
| `--gray-950` | `#111827` | 全局主文本 |

### B. Brand / Accent Tokens

| Token | 当前值 | 当前用途 |
|------|------|------|
| `--blue-500` | `#007AFF` | Apple 蓝，筛选激活默认色、状态蓝 |
| `--blue-550` | `#408FF7` | 当前全局 accent |
| `--blue-600` | `#3070D9` | 主按钮 hover |
| `--blue-700` | `#2563EB` | 正文链接、选中高亮深蓝 |
| `--blue-800` | `#1D4ED8` | 链接 hover |
| `--green-500` | `#34C759` | 成功状态 |
| `--orange-500` | `#FF9500` | Toast 警告 |
| `--orange-550` | `#FF9F0A` | 收藏 Toast |
| `--red-500` | `#FF3B30` | 错误状态 |
| `--amber-500` | `#FFCC00` | 筛选色板成员 |
| `--purple-500` | `#AF52DE` | 筛选色板成员 |

### C. Semantic Tokens

| Token | 建议值 | 映射说明 |
|------|------|------|
| `--color-bg-base` | `var(--gray-50)` | App 全局底色 |
| `--color-bg-surface` | `var(--white)` | 卡片 / 详情页主面板 |
| `--color-bg-subtle` | `var(--gray-25)` | 次级浅底 |
| `--color-bg-muted` | `var(--gray-75)` | 输入框、辅助区块 |
| `--color-border-subtle` | `var(--gray-200)` | 常规边框与分割线 |
| `--color-border-strong` | `rgba(0, 0, 0, 0.08)` | 强分割线、双栏边界 |
| `--color-text-primary` | `var(--gray-950)` | 一级文本 |
| `--color-text-secondary` | `var(--gray-800)` | 二级文本 |
| `--color-text-tertiary` | `var(--gray-600)` | 标签 / 提示 / UI 辅助文本 |
| `--color-text-quaternary` | `var(--gray-500)` | 禁用 / placeholder / 更弱提示 |
| `--color-accent-primary` | `var(--blue-500)` | 主品牌色 |
| `--color-accent-strong` | `var(--blue-700)` | 深一点的交互色 |
| `--color-accent-soft` | `rgba(0, 122, 255, 0.1)` | 淡蓝底 |
| `--color-success` | `var(--green-500)` | 成功状态 |
| `--color-warning` | `var(--orange-500)` | 警告状态 |
| `--color-danger` | `var(--red-500)` | 错误状态 |

### D. Material Tokens

| Token | 当前值 | 当前用途 |
|------|------|------|
| `--material-overlay` | `rgba(0, 0, 0, 0.45)` | 图片预览遮罩 |
| `--material-overlay-strong` | `rgba(0, 0, 0, 0.5)` | 删除确认 / Modal 遮罩 |
| `--material-panel-thick` | `rgba(250, 250, 252, 0.85)` | 图片预览主容器 |
| `--material-panel-thin` | `rgba(255, 255, 255, 0.15)` | 底部悬浮栏 |
| `--material-panel-soft` | `rgba(255, 255, 255, 0.4)` | 图片预览缩略图区、轻玻璃区 |
| `--material-elevated-line` | `rgba(255, 255, 255, 0.3)` | 底栏边界 |

### E. Component Tokens

#### 1. 列表卡片

| Token | 当前值 | 位置 |
|------|------|------|
| `--list-card-bg` | `#FFFFFF` | `.list-card` |
| `--list-card-border` | `rgba(0, 0, 0, 0.03)` | `.list-card` |
| `--list-card-hover-border` | `rgba(0, 0, 0, 0.06)` | `.list-card:hover` |
| `--list-card-title` | `#636366` | `.list-card-title` |
| `--list-card-meta-text` | `#808080` | `.list-date`, `.meta-tag-gray` |
| `--list-card-unread-dot` | `#3378F9` | `.unread-dot` |
| `--list-card-selected-border` | `rgba(64, 143, 247, 0.34)` | `.list-card.is-selected` |
| `--list-card-selected-glow` | `rgba(64, 143, 247, 0.16)` | `.list-card.is-selected` |
| `--list-card-selected-strip-start` | `#60A5FA` | `.list-card.is-selected::before` |
| `--list-card-selected-strip-mid` | `#3B82F6` | `.list-card.is-selected::before` |
| `--list-card-selected-strip-end` | `#2563EB` | `.list-card.is-selected::before` |

#### 2. 筛选栏

| Token | 当前值 | 位置 |
|------|------|------|
| `--filter-chip-bg` | `#EBEBF0` | `.filter-chip` |
| `--filter-chip-hover-bg` | `#E1E1E8` | `.filter-chip:hover` |
| `--filter-chip-text` | `#3C3C43` | `.filter-chip` |
| `--filter-chip-text-muted` | `#6E6E73` | `.filter-chip:not(.active)` |
| `--filter-chip-active-text` | `#FFFFFF` | `.filter-chip.active` |

#### 3. 底部悬浮栏 / Dock

| Token | 当前值 | 位置 |
|------|------|------|
| `--dock-bg` | `rgba(255, 255, 255, 0.15)` | `.main-card-header` |
| `--dock-border` | `rgba(255, 255, 255, 0.3)` | `.main-card-header` |
| `--dock-pill-bg` | `rgba(0, 0, 0, 0.06)` | `--pill-bg` |
| `--dock-pill-hover` | `rgba(0, 0, 0, 0.1)` | `--pill-hover` |
| `--dock-icon` | `#111827` | `.dock-item` |
| `--dock-tooltip-bg` | `#111827` | `::after` tooltip |
| `--dock-tooltip-text` | `#FFFFFF` | `::after` tooltip |

#### 4. 阅读器 / 详情页

| Token | 当前值 | 位置 |
|------|------|------|
| `--reader-bg` | `#FFFFFF` | `.detail-view` |
| `--reader-text` | `#374151` | `.detail-content` |
| `--reader-title` | `#111111` | `.detail-title` |
| `--reader-h1` | `#0F172A` | `.markdown-body h1` |
| `--reader-h2` | `#0F172A` | `.markdown-body h2` |
| `--reader-h3` | `#0F172A` | `.markdown-body h3` |
| `--reader-h4` | `#1F2937` | `.markdown-body h4` |
| `--reader-h5` | `#334155` | `.markdown-body h5` |
| `--reader-h6` | `#475569` | `.markdown-body h6` |
| `--reader-heading-label` | `rgba(148, 163, 184, 0.92)` | `h*[data-heading-label]::before` |
| `--reader-h3-accent-bar` | `var(--accent-color)` | 标准详情 H3 左侧蓝线 |
| `--reader-link-text` | `#2563EB` | `.markdown-body a` |
| `--reader-link-hover` | `#1D4ED8` | `.markdown-body a:hover` |
| `--reader-link-bg` | `rgba(239, 246, 255, 0.68)` | `.markdown-body a` |
| `--reader-link-bg-hover` | `rgba(219, 234, 254, 0.92)` | `.markdown-body a:hover` |
| `--reader-quote-text` | `#475569` | `.markdown-body blockquote` |
| `--reader-quote-border` | `rgba(37, 99, 235, 0.3)` | `.markdown-body blockquote` |
| `--reader-quote-bg-start` | `rgba(248, 250, 252, 0.94)` | `.markdown-body blockquote` |
| `--reader-quote-bg-end` | `rgba(241, 245, 249, 0.92)` | `.markdown-body blockquote` |

#### 5. RSS 阅读器专属

| Token | 当前值 | 位置 |
|------|------|------|
| `--rss-toc-line` | `rgba(148, 163, 184, 0.26)` | `.rss-floating-toc::before` |
| `--rss-toc-line-active` | `rgba(100, 116, 139, 0.42)` | `.rss-floating-toc:hover::before` |
| `--rss-toc-text` | `rgba(100, 116, 139, 0.72)` | `.rss-floating-toc-item` |
| `--rss-toc-text-hover` | `rgba(71, 85, 105, 0.94)` | `.rss-floating-toc-item:hover` |
| `--rss-toc-text-active` | `rgba(51, 65, 85, 0.96)` | `.rss-floating-toc-item.is-active` |
| `--rss-toc-dot` | `rgba(148, 163, 184, 0.38)` | `.rss-floating-toc-dot` |
| `--rss-toc-dot-hover` | `rgba(100, 116, 139, 0.62)` | `.rss-floating-toc-dot:hover` |
| `--rss-toc-dot-active` | `rgba(71, 85, 105, 0.82)` | `.rss-floating-toc-item.is-active .rss-floating-toc-dot` |
| `--rss-toc-panel-bg` | `#FFFFFF` | `.rss-floating-toc:hover` |
| `--rss-label-bg` | `rgba(239, 143, 89, 0.92)` | `.rss-source-label` |
| `--rss-label-text` | `#FFFFFF` | `.rss-source-label` |
| `--rss-placeholder-bg` | `#EDEDED` | `.detail-view-placeholder` |
| `--rss-placeholder-fg` | `#D5D5D5` | `.detail-placeholder-icon`, `.detail-placeholder-motto` |

#### 6. 设置页

| Token | 当前值 | 位置 |
|------|------|------|
| `--settings-card-bg` | `#FFFFFF` | `.settings-card` |
| `--settings-label` | `#86868B` | `.settings-label`, `.settings-card-title` |
| `--settings-input-bg` | `#F5F5F7` | `.form-input` |
| `--settings-input-text` | `#1D1D1F` | `.form-input` |
| `--settings-input-placeholder` | `#C7C7CC` | `.form-input::placeholder` |
| `--settings-input-focus-ring` | `rgba(0, 122, 255, 0.1)` | `.form-input:focus` |
| `--settings-divider-text` | `#6B7280` | `.divider-text` |

#### 7. Toast / Modal / 图片预览

| Token | 当前值 | 位置 |
|------|------|------|
| `--toast-bg` | `rgba(255, 255, 255, 0.85)` | `.apple-toast` |
| `--toast-title` | `#1D1D1F` | `.toast-title` |
| `--toast-message` | `#86868B` | `.toast-message` |
| `--toast-success` | `#34C759` | `.toast-icon.success` |
| `--toast-warning` | `#FF9500` | `.toast-icon.warning` |
| `--toast-danger` | `#FF3B30` | `.toast-icon.error` |
| `--toast-info` | `#007AFF` | `.toast-icon.info` |
| `--modal-overlay` | `rgba(0, 0, 0, 0.5)` | `.modal-overlay`, `.delete-confirm-overlay` |
| `--modal-bg` | `#FFFFFF` | `.delete-confirm-modal` |
| `--modal-body-bg` | `#F6F6F8` | `.modal-body` |
| `--modal-footer-bg` | `#FAFAFA` | `.modal-footer` |
| `--image-preview-overlay` | `rgba(0, 0, 0, 0.45)` | `.image-preview-overlay` |
| `--image-preview-bg` | `rgba(250, 250, 252, 0.85)` | `.image-preview-modal` |
| `--image-preview-toolbar-bg` | `rgba(255, 255, 255, 0.3)` | `.image-preview-toolbar` |
| `--image-preview-text` | `#1D1D1F` | `.image-preview-title`, `.image-preview-action` |
| `--image-preview-meta` | `#86868B` | `.image-preview-count`, `.image-preview-caption` |

## JS 与 CSS 的桥接方案

### 当前问题

当前 [frontend/js/app.js](../frontend/js/app.js) 中存在以下颜色逻辑：

- `FILTER_CHIP_COLORS`
- `getDepartmentColor()`
- `getAITagBgColor()`
- `getSubsChipColor()`
- `RSS_LABEL_BACKGROUND`

这会带来两个问题：

- 深色主题下，JS 里的高饱和度颜色不会自动调暗
- JS 还在负责“算颜色”，后面维护成本会很高

### 推荐方案

不要让 JS 再维护真实颜色值，而是改为维护 token key。

#### 不推荐

```js
const FILTER_CHIP_COLORS = ["rgb(183,42,92)", "rgb(255,56,60)"];
```

#### 推荐

```js
const FILTER_CHIP_TOKEN_KEYS = [
  "--chip-color-1",
  "--chip-color-2",
];
```

然后通过统一桥接函数读取：

```js
const readCssVar = (name) =>
  getComputedStyle(document.documentElement).getPropertyValue(name).trim();
```

这样：

- CSS 负责定义浅色 / 深色主题下真实颜色
- JS 只负责决定“选哪个 token”
- 主题切换时，JS 不需要知道颜色真值发生了变化

### AI 标签不要继续动态拼透明度

当前 `getAITagBgColor()` 是从 `rgb(...)` 动态转 `rgba(..., 0.8)`。

建议改为在 CSS 中直接准备成对变量：

- `--chip-color-1`
- `--chip-color-1-soft`
- `--chip-color-1-strong`

这样 AI 标签、部门图标、筛选激活态都能直接复用，不需要 JS 再计算透明度。

## 建议执行顺序

### Phase 0：建立 Token 文件

- [ ] 新建 `frontend/css/theme-tokens.css`
- [ ] 建立 palette / semantic / component 三层 token
- [ ] 在 `frontend/index.html` 里先引入 `theme-tokens.css`

### Phase 1：收编全局基础色

- [ ] 收编全局背景、卡片背景、边框色、主文本、次级文本
- [ ] 清理 `styles.css` 中重复的 `:root`
- [ ] 保证 `styles.css` 只消费 token，不再定义主题真值

### Phase 2：收编主界面主链路

- [ ] 列表卡片
- [ ] 筛选栏
- [ ] 底部悬浮栏
- [ ] 详情页阅读器
- [ ] RSS 阅读器目录

### Phase 3：收编次级组件

- [ ] 设置页
- [ ] Toast
- [ ] 通用 Modal
- [ ] 删除确认弹窗
- [ ] 图片预览

### Phase 4：重构 JS 颜色桥接

- [ ] `FILTER_CHIP_COLORS` 改为 token key 列表
- [ ] `getDepartmentColor()` 改为读 CSS 变量
- [ ] `getAITagBgColor()` 改为直接读 soft token
- [ ] RSS 标签色改为组件 token

### Phase 5：扩展主题

- [ ] `data-theme="light"`
- [ ] `data-theme="dark"`
- [ ] `data-theme="sepia"`
- [ ] 可选：正文独立阅读主题与应用壳主题拆分

## 第一阶段建议先改的真实入口

如果只做第一轮，优先处理下面这些位置：

1. [frontend/css/styles.css](../frontend/css/styles.css) 顶部全局 `:root`
2. [frontend/css/styles.css](../frontend/css/styles.css) 中段底栏专属 `:root`
3. `.list-card` / `.announcement-card`
4. `.filter-chip`
5. `.main-card-header` / `.dock-item` / `.btn-check`
6. `.detail-view` / `.detail-content` / `.detail-title`
7. `.rss-floating-toc`
8. `.settings-card` / `.form-input`
9. `.apple-toast`
10. `.modal-overlay` / `.delete-confirm-modal` / `.image-preview-modal`

## 验证清单

主题重构完成后，至少需要人工验证以下界面：

- [ ] 单栏列表视图
- [ ] 双栏列表 + 详情并排视图
- [ ] RSS 原始 / 增强 / 摘要三种模式
- [ ] RSS 目录 hover / active / sticky 状态
- [ ] 设置页所有表单卡片
- [ ] Toast 各状态颜色
- [ ] 删除确认弹窗
- [ ] 图片预览弹层
- [ ] 自定义数据源编辑展开区
- [ ] 底部悬浮功能栏在单栏 / 双栏 / 搜索展开下的材质统一性

## 最终建议

这套主题系统改造是值得做的，而且当前就是很适合开始做的时间点。

原因不是“颜色很多看着乱”，而是：

- 项目现在已经有足够稳定的 UI 骨架
- RSS 阅读器、双栏布局、设置页样式都已基本成型
- 如果继续叠加功能，再回头做主题系统，清理成本会更高

所以更合适的节奏是：

> 先把主题系统收编成基建，再继续做更多视觉扩展。

