# MicroFlow 匿名统计与遥测方案

更新时间：2026-03-31

## 文档目的

本文档用于定义 MicroFlow 的匿名使用统计、稳定性上报与性能监控方案，目标是：

1. 为后续功能迭代提供真实使用依据
2. 为 Windows / macOS 双平台发布提供质量监控基础
3. 在不上传正文、笔记、私密配置的前提下，收集最小必要数据
4. 保持可关闭、可解释、可灰度的遥测策略

## 核心原则

遥测方案必须同时满足以下原则：

- 匿名
- 最小化
- 可关闭
- 可审计
- 可灰度

换句话说：

- 只上传产品决策需要的数据
- 不上传用户正文和敏感内容
- 用户可在设置中关闭
- 服务端可按版本与渠道灰度启用

## 明确允许收集的数据

## 1. 安装与活跃数据

- 安装量
- 启动次数
- 活跃设备数
- 活跃会话数
- 版本分布
- 平台分布
- 渠道分布

## 2. 功能使用数据

- 是否使用固定订阅源
- 是否使用 RSS 订阅
- 是否使用自定义 HTML 数据源
- 是否触发搜索、收藏、截图、复制、笔记
- 是否切换原文 / 摘要 / AI 增强模式
- 是否使用 AI 重新生成
- 是否配置邮件推送

## 3. 稳定性数据

- Python 异常
- 前端 JS 异常
- 爬虫抓取失败
- AI 请求失败
- 更新检查失败
- 下载校验失败

## 4. 性能数据

- 冷启动耗时
- 首屏可交互耗时
- 更新检查耗时
- 抓取耗时
- AI 总结耗时
- 列表打开详情耗时

## 明确禁止收集的数据

以下内容原则上不进入遥测：

- 文章正文原文
- 文章标题全文
- 用户复制内容
- 用户笔记内容
- 用户 API Key
- 用户 SMTP 密码
- 自定义规则里的完整 URL
- 自定义规则里的完整 CSS 选择器
- 用户输入的完整提示词
- 邮箱收件人列表全文

如果某些数据对分析有价值，应改为“匿名衍生字段”：

- 自定义数据源 URL：只上传域名哈希
- 提示词：只上传是否自定义、长度区间
- 正文：只上传字符数区间，不上传原文

## 身份模型建议

## 不建议直接使用现有 `deviceId`

当前配置层已经存在 `deviceId`，它更适合本地签名与授权用途，不建议直接作为统计标识上传。

原因：

- 含设备指纹意味较强
- 不利于对外说明“匿名统计”
- 后续如果用户要求清除数据，不够灵活

## 建议新增三个标识

### `install_id`

- 首次安装后随机生成 UUID
- 写入本地配置
- 只用于匿名统计
- 卸载重装后可视为新安装

### `session_id`

- 每次启动生成 UUID
- 仅用于一次运行周期内的事件关联

### `event_id`

- 每条事件单独生成 UUID
- 用于服务端去重

## 事件公共字段建议

每条事件建议包含以下通用字段：

```json
{
  "event_id": "uuid",
  "event": "app_launch",
  "ts": 1770000000,
  "install_id": "uuid",
  "session_id": "uuid",
  "app_version": "v1.1.3",
  "channel": "stable",
  "platform": "windows",
  "platform_version": "10.0.22631",
  "arch": "x64",
  "locale": "zh-CN",
  "props": {}
}
```

## 推荐事件清单

## P0：首批必须埋点

### 生命周期

- `app_launch`
- `app_exit`
- `startup_check_result`
- `app_read_only_entered`

### 更新相关

- `update_check_result`
- `update_available`
- `update_download_click`
- `update_download_finished`
- `update_install_start`

### 抓取与内容链路

- `source_fetch_result`
- `article_open`
- `detail_mode_switch`
- `article_copy`
- `article_snapshot`
- `article_favorite_toggle`

### AI 相关

- `ai_summary_request`
- `ai_summary_result`
- `ai_regenerate_request`
- `ai_regenerate_result`

### 自定义规则

- `custom_rule_create`
- `custom_rule_edit`
- `custom_rule_test_result`
- `custom_rule_save_result`

### 错误相关

- `error_python`
- `error_js`
- `error_api`

## P1：第二阶段建议埋点

- `search_submit`
- `search_result_empty`
- `note_create`
- `note_delete`
- `settings_changed`
- `email_test_result`
- `rss_preview_result`
- `html_preview_result`

## P2：后续增强埋点

- `retention_day_1`
- `retention_day_7`
- `feature_discovery`
- `onboarding_complete`
- `release_notes_open`

## 关键事件属性建议

## `startup_check_result`

建议属性：

- `result`: `success` / `read_only` / `offline_ttl` / `network_error`
- `has_update`: `true` / `false`
- `force_update`: `true` / `false`
- `response_ms`

## `source_fetch_result`

建议属性：

- `source_type`: `official` / `rss` / `html`
- `source_name`: 仅官方源可上传明文
- `custom_domain_hash`: 自定义源时上传域名哈希
- `status`: `success` / `empty` / `error`
- `new_count`
- `duration_ms`

## `ai_summary_result`

建议属性：

- `status`: `success` / `error` / `cancelled`
- `model_name`
- `source_type`
- `duration_ms`
- `retry_count`
- `error_code`

## `article_open`

建议属性：

- `source_type`
- `source_name`
- `has_ai_summary`
- `default_mode`
- `current_mode`

## `detail_mode_switch`

建议属性：

- `from_mode`
- `to_mode`
- `source_type`

## `error_python`

建议属性：

- `module`
- `error_type`
- `error_message_short`
- `stack_hash`
- `is_fatal`

## `error_js`

建议属性：

- `page`
- `error_type`
- `message`
- `line`
- `stack_hash`

## 自定义源脱敏规则

自定义数据源相关事件必须脱敏。

建议规则如下：

- 不上传完整 URL
- 只上传 `domain_hash`
- 不上传完整 selector
- 只上传是否启用 `detail_selector` / `time_selector` / `attachment_selector`
- 不上传提示词全文
- 只上传：
  - `has_custom_summary_prompt`
  - `summary_prompt_length_bucket`
  - `has_custom_formatting_prompt`
  - `formatting_prompt_length_bucket`

## 采样建议

为了避免正式发布后流量过大，建议分级采样：

- 生命周期事件：100%
- 更新事件：100%
- 错误事件：100%
- 抓取成功事件：30%-100%，可按渠道调整
- 高频交互事件：10%-30%

推荐初始配置：

- `stable`: `sample_rate = 0.3`
- `beta`: `sample_rate = 1.0`
- `internal`: `sample_rate = 1.0`

## 本地存储建议

遥测事件不应直接同步阻塞上传，建议先写本地队列。

## 推荐实现方式

- 本地 SQLite 表或 `jsonl` 队列文件
- 主线程只负责写入本地
- 后台线程定时批量上传

## 本地表结构建议

| 字段 | 类型 | 用途 |
| --- | --- | --- |
| `id` | integer | 自增主键 |
| `event_id` | text | 事件唯一 ID |
| `event_name` | text | 事件名 |
| `payload_json` | text | 事件载荷 |
| `status` | text | `pending` / `sent` / `failed` |
| `retry_count` | integer | 重试次数 |
| `created_at` | integer | 创建时间 |
| `next_retry_at` | integer | 下次重试时间 |

## 上报策略建议

满足任一条件即触发上传：

- 启动后 30 秒
- 队列积压达到 20 条
- 每 30 分钟定时上传
- 退出前尽力上传一次

失败时建议采用指数退避：

- 第 1 次失败：1 分钟后重试
- 第 2 次失败：5 分钟后重试
- 第 3 次失败：30 分钟后重试
- 第 4 次及以上：12 小时后重试

## 服务端架构建议

基于腾讯云，建议优先采用轻量架构：

### P0

- `API Gateway`
- `SCF`
- `COS` 或 `CLS`

### P1

- 增加 `TDSQL-C` / `PostgreSQL`
- 增加聚合报表任务

## 推荐接口

### `POST /v1/telemetry/batch`

用途：

- 批量上传事件

请求体：

```json
{
  "schema_version": "1",
  "channel": "stable",
  "events": [
    {
      "event_id": "uuid",
      "event": "app_launch",
      "ts": 1770000000,
      "install_id": "uuid",
      "session_id": "uuid",
      "app_version": "v1.1.3",
      "platform": "windows",
      "arch": "x64",
      "props": {
        "cold_start_ms": 1380
      }
    }
  ]
}
```

返回：

```json
{
  "status": "success",
  "accepted": 1
}
```

## `POST /v1/crash`

用途：

- 专门接收致命错误

这个接口不是首期必须，但后续对 Windows 正式用户会很有价值。

## 设置项建议

建议在设置页新增三个开关：

- `帮助改进产品`
  - 控制匿名使用统计
- `发送稳定性与错误报告`
  - 控制异常上报
- `加入测试版更新`
  - 控制渠道是否切换为 `beta`

## 首次启动建议

建议在首次启动时展示一个轻量说明：

- 说明会收集匿名使用数据，用于改进抓取质量、更新稳定性和功能设计
- 说明不会上传文章正文、笔记、密码和 API Key
- 提供“同意 / 稍后决定 / 关闭统计”

## 建议的用户文案

可直接用于首启或设置页：

> MicroFlow 可以发送匿名使用统计和错误信息，帮助我们判断哪些功能最常用、哪些来源最不稳定，以及更新后是否带来了新的问题。我们不会上传文章正文、笔记内容、API Key、密码和完整自定义规则。你可以随时在设置中关闭。

## 数据保留策略

建议服务端保留策略：

- 原始事件：90 天
- 错误事件：180 天
- 聚合报表：365 天

用户侧建议支持：

- 关闭遥测后停止上报
- 清空本地待上报队列

## 与当前项目的对应关系

当前项目已经有以下可复用能力：

- 本地配置管理
- 设备标识字段
- 远程 `version.json`
- 前后端桥接 API
- 本地 SQLite

后续实现阶段建议新增：

- `src/services/telemetry_service.py`
- 本地遥测队列表
- `flush_telemetry` 后端接口
- 前端 JS 错误捕获桥接
- Python 异常统一上报入口

## 推荐实施顺序

1. 先补 `install_id` 与用户开关
2. 再补本地队列和 `batch` 上报接口
3. 先上 P0 事件
4. 稳定后再加 P1 / P2 事件

## 与更新系统的关系

遥测系统后续将直接服务更新策略：

- 判断哪些版本仍在活跃
- 判断新版本是否真正被下载与安装
- 判断某版本是否导致抓取失败率或崩溃率飙升
- 为灰度发布提供依据

因此，更新系统与遥测系统建议同步规划、分步实现。
