# MicroFlow 发布与更新方案

更新时间：2026-03-31

## 文档目的

本文档用于整理 MicroFlow 在正式发布前的发布、更新与远程控制策略，覆盖以下目标：

1. 支持 Windows / macOS 双平台发布
2. 保留现有远程启停与版本检查能力
3. 为后续“更新提醒 -> 一键下载 -> 自动更新”预留演进路径
4. 在用户以 Windows 为主的前提下，优先明确 Windows 发布策略

本文档不直接修改当前打包脚本，而是先定义发布规范、远程配置协议和客户端行为。

相关补充文档：

- [版本号与更新日志策略](./VERSIONING_POLICY.md)
- [数据库发布基线与迁移策略](./DATABASE_RELEASE_BASELINE.md)
- [远程版本模板](./version.template.json)
- [CHANGELOG](../CHANGELOG.md)

## 当前基础

项目当前已经具备以下能力：

- 后端已存在统一远程配置入口 `VERSION_URL`
- 启动时会请求远程 `version.json`
- 已支持 `is_active` 远程启停
- 已支持离线 TTL 存活期与本地锁定
- 已支持远程公告读取
- 已支持版本更新检查与平台下载链接返回
- 已支持 ETag 缓存协商，减少重复请求
- 当前已有 macOS 的 PyInstaller `.app` 打包基础

因此，后续发布方案不需要推翻重做，重点是：

- 收敛远程配置协议
- 增加 Windows 正式发布链路
- 明确客户端更新行为
- 为后续增量自动更新预留接口

## 平台优先级

### 结论

- Windows：主发布平台，优先打磨安装、升级、错误回传、更新提醒体验
- macOS：同步支持，但允许先维持“下载新版安装包覆盖升级”的路径

### 建议节奏

1. Windows `x64` 稳定版优先
2. macOS `arm64` 稳定版同步
3. Windows `arm64`、macOS `universal2` 作为后续增强项

## 发布产物建议

## Windows

建议区分两类产物：

### 正式用户版

- 主产物：`MicroFlow-Setup-x.y.z.exe`
- 打包方式：`PyInstaller dist` + `Inno Setup` 或 `NSIS`
- 分发形式：腾讯云 COS 下载链接
- 安装行为：
  - 安装到 `Program Files`
  - 创建开始菜单快捷方式
  - 可选创建桌面快捷方式
  - 支持覆盖升级
  - 可选注册开机自启

### 内测版

- 辅助产物：`MicroFlow-x.y.z-windows-portable.zip`
- 用途：内测、快速回归、问题复现

### Windows 重点建议

- 正式发布前准备代码签名证书
- 安装包和主程序均建议签名
- 更新阶段优先采用“提示下载新版安装包”方案
- 后续再引入独立 updater 进程

## macOS

建议区分两类产物：

### 正式用户版

- 主产物：`MicroFlow-x.y.z-macos.dmg` 或 `MicroFlow-x.y.z-macos.zip`
- 当前基础：已有 `.app` 构建能力
- 建议补齐：
  - `.icns` 图标
  - 正式 `bundle_identifier`
  - `codesign`
  - `notarization`

### 内测版

- 辅助产物：`MicroFlow.app.zip`

### macOS 重点建议

- 正式发布前补全签名与公证
- 如果短期内先不做自动更新，维持“检测到新版本 -> 打开下载页”即可
- 若后续需要无感升级，再评估单独 updater 或引入 Sparkle 类方案

## 发布渠道建议

建议统一定义三个渠道：

- `stable`
  - 面向正式用户
  - 默认更新频率低、强调稳定
- `beta`
  - 面向测试用户
  - 提前验证新功能与更新包
- `internal`
  - 面向开发、自测、灰度验证

客户端本地建议增加 `channel` 配置字段，默认 `stable`。

## 远程配置文件建议

当前远程配置建议仍沿用单一 `version.json`，但字段升级为正式协议。

## 建议字段

| 字段 | 类型 | 必填 | 用途 |
| --- | --- | --- | --- |
| `schema_version` | string | 是 | 远程配置协议版本 |
| `channel` | string | 是 | 当前配置所属渠道 |
| `version` | string | 是 | 最新正式版本号 |
| `build` | integer | 否 | 构建号 |
| `release_date` | string | 否 | 发布时间 |
| `is_active` | boolean | 是 | 是否允许软件正常运行 |
| `kill_message` | string | 否 | 远程停用提示语 |
| `min_supported_version` | string | 否 | 最低支持版本 |
| `force_update` | boolean | 否 | 是否强制更新 |
| `downloads` | object | 是 | 双平台下载信息 |
| `announcement` | object | 否 | 系统公告 |
| `rollout` | object | 否 | 灰度发布配置 |
| `telemetry` | object | 否 | 遥测开关与采样配置 |

## `downloads` 字段建议

### `downloads.windows`

| 字段 | 类型 | 必填 | 用途 |
| --- | --- | --- | --- |
| `url` | string | 是 | Windows 下载链接 |
| `sha256` | string | 是 | 安装包摘要校验 |
| `size` | integer | 否 | 文件大小（字节） |
| `installer_type` | string | 否 | `inno` / `nsis` / `msix` |
| `signature_subject` | string | 否 | 代码签名证书主体 |

### `downloads.macos`

| 字段 | 类型 | 必填 | 用途 |
| --- | --- | --- | --- |
| `url` | string | 是 | macOS 下载链接 |
| `sha256` | string | 是 | 安装包摘要校验 |
| `size` | integer | 否 | 文件大小（字节） |
| `package_type` | string | 否 | `dmg` / `zip` |
| `notarized` | boolean | 否 | 是否已公证 |

## `announcement` 字段建议

| 字段 | 类型 | 必填 | 用途 |
| --- | --- | --- | --- |
| `id` | string | 否 | 公告唯一 ID |
| `title` | string | 否 | 公告标题 |
| `summary` | string | 否 | 公告摘要 |
| `content` | string | 否 | 公告正文 |
| `publish_time` | string | 否 | 发布时间 |
| `url` | string | 否 | 外部链接 |
| `version` | string | 否 | 与哪个版本关联 |

## `rollout` 字段建议

| 字段 | 类型 | 必填 | 用途 |
| --- | --- | --- | --- |
| `enabled` | boolean | 否 | 是否启用灰度 |
| `percentage` | integer | 否 | 灰度比例，1-100 |
| `whitelist` | array | 否 | 灰度白名单 install_id |

## `telemetry` 字段建议

| 字段 | 类型 | 必填 | 用途 |
| --- | --- | --- | --- |
| `enabled` | boolean | 否 | 是否允许遥测上报 |
| `endpoint` | string | 否 | 上报地址 |
| `batch_size` | integer | 否 | 单次批量上报数量 |
| `flush_interval_sec` | integer | 否 | 定时上报间隔 |
| `sample_rate` | number | 否 | 采样比例，0-1 |

## 示例 `version.json`

```json
{
  "schema_version": "1",
  "channel": "stable",
  "version": "v1.2.0",
  "build": 1200,
  "release_date": "2026-04-10",
  "is_active": true,
  "kill_message": "",
  "min_supported_version": "v1.0.0",
  "force_update": false,
  "downloads": {
    "windows": {
      "url": "https://cdn.example.com/MicroFlow-Setup-v1.2.0.exe",
      "sha256": "9f9b5b4b7b2b7a8f5d0e0caa11223344556677889900aabbccddeeff00112233",
      "size": 118734245,
      "installer_type": "inno",
      "signature_subject": "Shenzhen MicroFlow Studio"
    },
    "macos": {
      "url": "https://cdn.example.com/MicroFlow-v1.2.0.dmg",
      "sha256": "0f1e2d3c4b5a69788776655443322110ffeeddccbbaa99887766554433221100",
      "size": 102345678,
      "package_type": "dmg",
      "notarized": true
    }
  },
  "announcement": {
    "id": "announce_20260410",
    "title": "v1.2.0 已发布",
    "summary": "新增 Windows 正式安装包与更新提醒优化",
    "content": "本次更新主要包含：1. Windows 正式安装流程；2. RSS 阅读模式优化；3. 错误上报增强。",
    "publish_time": "2026-04-10 09:00:00",
    "url": "https://example.com/releases/v1.2.0",
    "version": "v1.2.0"
  },
  "rollout": {
    "enabled": false,
    "percentage": 100,
    "whitelist": []
  },
  "telemetry": {
    "enabled": true,
    "endpoint": "https://api.example.com/v1/telemetry/batch",
    "batch_size": 30,
    "flush_interval_sec": 1800,
    "sample_rate": 1.0
  }
}
```

## 客户端更新策略

## P0：更新提醒

这一阶段优先做“伪推送”：

- 启动时检查一次
- 稳定版每 12 小时检查一次
- 测试版每 6 小时检查一次
- 用户手动点击“检查更新”时强制刷新

如果发现新版本：

- 列表中显示系统公告
- 设置页显示“有新版本可用”
- 弹出轻量通知
- 点击后打开下载链接

这个阶段已经能满足绝大多数桌面软件的“更新提醒”需求。

## P1：一键下载更新包

在 P0 基础上扩展：

- 后台下载新版安装包到临时目录
- 下载完成后校验 `sha256`
- 向用户展示“立即安装 / 稍后安装”
- Windows 先支持下载 `.exe` 安装包
- macOS 先支持下载 `.dmg` / `.zip`

这一阶段仍不要求自动替换正在运行的应用。

## P2：独立 Updater

在 P1 基础上继续扩展：

- 主程序退出后启动独立 updater
- updater 负责替换旧版本并重启主程序
- Windows 与 macOS 各自维护独立更新脚本或 helper

只有进入这一阶段，才算真正意义上的“自动更新”。

## 强制更新策略

建议仅在以下场景启用 `force_update=true`：

- 接口协议发生不兼容变更
- 某版本存在严重崩溃或数据损坏风险
- 某版本存在安全问题

客户端判定建议：

- 如果 `force_update=true` 且当前版本 `< min_supported_version`
- 则禁止继续进入主界面
- 只保留“下载新版”和“退出软件”

## 远程停用策略

现有 `is_active=false` 的只读停用能力可以继续保留，但建议明确只用于以下场景：

- 重大安全问题
- 法规或授权策略调整
- 后端协议不可恢复地失配

不建议把普通版本升级也走 `is_active=false`，否则体验会过重。

普通升级建议优先使用：

- `has_update`
- `min_supported_version`
- `force_update`

## 发布流程建议

每次正式发布建议统一按以下顺序执行：

1. 更新本地版本号
2. 打包 Windows / macOS 产物
3. 计算各平台 `sha256`
4. 上传安装包到腾讯云 COS
5. 更新 `version.json`
6. 先灰度到 `beta` 或小比例 `stable`
7. 观察埋点与错误率
8. 确认稳定后放量到 100%

## 回滚策略

建议保留最近至少 3 个正式版本安装包。

当新版本出现问题时：

1. 将 `version.json` 的 `version` 回退到上一稳定版本
2. 将 `downloads` 指回上一稳定版本安装包
3. 必要时把 `min_supported_version` 回退
4. 不建议直接用 `is_active=false` 停掉全部客户端，除非问题极其严重

## 客户端显示文案建议

### 普通更新

- 标题：`发现新版本`
- 文案：`MicroFlow v1.2.0 已可用，可下载后安装更新。`

### 强制更新

- 标题：`需要更新后继续使用`
- 文案：`当前版本已不再受支持，请先更新到最新版本。`

### 远程停用

- 标题：`当前版本已暂停服务`
- 文案：直接显示 `kill_message`

## 与当前项目的对应关系

当前代码中，以下模块已经可以直接复用：

- 远程配置地址：`src/api.py`
- 启动检查：`perform_startup_check`
- 更新检查：`check_software_update`
- 公告读取：前端 `get_version_info`
- macOS 打包：`MicroFlow.spec`

后续真正进入实现阶段时，建议新增：

- Windows 安装包构建脚本
- 统一的发布清单生成脚本
- `version.json` 自动校验脚本
- 下载包 `sha256` 自动生成脚本

## 下一步建议

发布与更新部分的后续执行顺序建议为：

1. 先补 `version.json` 正式协议
2. 再补 Windows 安装包链路
3. 再补“更新提醒”界面
4. 最后评估一键下载与独立 updater
