# MicroFlow 数据库发布基线与迁移策略

更新时间：2026-03-31

## 目标

本文件用于明确：

1. 进入正式发布前，哪些数据库表与字段应视为稳定基线
2. 哪些字段属于兼容层或可继续演进字段
3. 后续数据库迁移应遵循什么原则

## 当前数据库范围

当前核心表共 3 张：

- `articles`
- `ai_result_cache`
- `article_annotations`

对应初始化与迁移逻辑位于：

- [src/database.py](/Users/chen/Code/MicroFlow/src/database.py)

## 表级稳定性分层

### 1. `articles`

这是当前最核心的业务表，已进入“发布基线候选”状态。

建议视为稳定的字段：

- `id`
- `title`
- `url`
- `date`
- `exact_time`
- `category`
- `department`
- `attachments`
- `summary`
- `raw_text`
- `raw_markdown`
- `enhanced_markdown`
- `ai_summary`
- `ai_tags`
- `raw_hash`
- `is_read`
- `is_favorite`
- `is_deleted`
- `source_name`
- `source_type`
- `rule_id`
- `custom_summary_prompt`
- `formatting_prompt`
- `summary_prompt`
- `enable_ai_formatting`
- `enable_ai_summary`
- `content_blocks`
- `image_assets`
- `created_at`

说明：

- 这些字段已经贯穿抓取、渲染、编辑、重生成、搜索、复制、详情模式切换等多个核心链路
- 进入正式发布后，不应随意重命名或删除

### 2. `ai_result_cache`

这是性能与成本优化相关表，建议视为“稳定但允许扩展”。

当前字段：

- `cache_key`
- `cache_scope`
- `content_hash`
- `prompt_hash`
- `model_name`
- `base_url`
- `result_text`
- `created_at`
- `updated_at`

原则：

- 可以新增字段
- 不建议修改 `cache_key`、`cache_scope` 等核心索引语义

### 3. `article_annotations`

这是用户批注系统的持久化表，建议视为“稳定基线候选”。

当前字段：

- `id`
- `article_id`
- `view_mode`
- `anchor_text`
- `anchor_prefix`
- `anchor_suffix`
- `start_offset`
- `end_offset`
- `style_payload`
- `created_at`
- `updated_at`

原则：

- 不应随意删除已有锚点字段
- 可以增加新的样式字段，但优先放入 `style_payload`

## 字段稳定性分级

### A. 正式稳定字段

这些字段进入正式发布后应尽量保持不变：

- 文章主标识：`id`、`url`
- 文章来源：`source_name`、`source_type`、`rule_id`
- 阅读状态：`is_read`、`is_favorite`、`is_deleted`
- 正文链路：`raw_text`、`raw_markdown`、`enhanced_markdown`
- AI 链路：`summary`、`ai_summary`、`ai_tags`
- 结构化正文：`content_blocks`、`image_assets`
- 时间链路：`date`、`exact_time`、`created_at`

### B. 软稳定字段

这些字段可以继续优化语义，但不建议轻易删除：

- `category`
- `department`
- `attachments`
- `custom_summary_prompt`
- `formatting_prompt`
- `summary_prompt`
- `enable_ai_formatting`
- `enable_ai_summary`

### C. 兼容层字段

这些字段短期仍要保留，但后续可考虑继续收敛：

- `summary`
  - 当前仍承担旧前端 / 兼容链路的桥接职责
- `raw_text`
  - 仍是多个搜索、AI 输入与兼容链路的基础字段

结论：

- 当前不建议立即删除任何 `articles` 表字段
- 正式发布前应优先收敛“读写入口”，而不是做激进删列

## 迁移策略

进入正式发布后，数据库迁移统一遵循以下原则。

### 原则 1：优先增量迁移

优先使用：

- `ALTER TABLE ... ADD COLUMN`
- 启动时补默认值
- 启动时做幂等 backfill

避免：

- 重建整表
- 无备份的 destructive migration

### 原则 2：迁移必须幂等

任一迁移逻辑都应满足：

- 重复执行不会报错
- 重复执行不会破坏既有数据
- 可在用户升级路径中安全重放

### 原则 3：先兼容、后收敛

如果需要重构字段语义，应分两步走：

1. 先新增新字段或统一读写入口
2. 等至少一个稳定版本后，再考虑下线旧兼容逻辑

### 原则 4：发布前冻结表结构窗口

正式发布前，建议建立“结构冻结窗口”：

- 发布前最后一轮只允许新增字段，不允许删除字段
- 如果必须删字段，应至少提前一个版本完成兼容迁移

## 当前建议

接下来进入发布准备阶段时，建议按以下顺序处理数据库问题：

1. 冻结 `articles / ai_result_cache / article_annotations` 的当前表名
2. 冻结 `articles` 中的正式稳定字段集合
3. 继续减少前后端对旧兼容字段的直接依赖
4. 所有后续迁移都记录到 [CHANGELOG.md](/Users/chen/Code/MicroFlow/CHANGELOG.md)

## 本轮已完成的相关收口

- 列表页 / 搜索页改为轻载读取，不再默认返回整篇正文
- 新增详情补全接口，降低列表接口对重字段的耦合
- 默认测试入口收敛到 `tests/`，避免工具脚本污染发布前回归
