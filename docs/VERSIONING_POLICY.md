# MicroFlow 版本号与更新日志策略

更新时间：2026-03-31

## 目标

本文件用于明确 MicroFlow 在进入正式发布前后的版本号策略、变更日志维护方式，以及远程 `version.json` 的对应关系。

## 当前状态

- 当前代码内版本号入口为 [src/version.py](/Users/chen/Code/MicroFlow/src/version.py)
- 当前版本号：`v1.1.3`
- 当前 `v1.1.x` 仍视为开发阶段内部版本线
- 当前远程配置模板位于 [docs/version.template.json](/Users/chen/Code/MicroFlow/docs/version.template.json)

结论：

- `v1.1.x` 可以继续用于本地联调和内部测试
- 第一个面向正式用户的结构化发布版本，建议从 `v1.2.0` 开始

## 版本号规则

采用 `vX.Y.Z` 风格：

- `X`：大版本
  - 出现架构调整、数据库基线切换、更新协议切换时递增
- `Y`：功能版本
  - 面向用户的新功能、明显体验升级、重要链路补齐时递增
- `Z`：修订版本
  - Bug 修复、稳定性收口、小范围兼容修复时递增

## 递增建议

### 何时递增 `Z`

- 修复前端 / 后端 bug
- 修复爬虫兼容性
- 修复 RSS / HTML 规则编辑链路
- 修复渲染、复制、编辑、回归测试问题

例如：

- `v1.1.3 -> v1.1.4`

### 何时递增 `Y`

- 设置页结构重构
- 详情页阅读模式升级
- HTML AI 爬虫进入可正式使用状态
- 上线新的更新策略、遥测策略、主题系统

例如：

- `v1.1.9 -> v1.2.0`

### 何时递增 `X`

- 数据库存储模型发生不可忽略的兼容断点
- 客户端更新协议或远程配置协议大幅升级
- 桌面壳、前端骨架、核心调度模型发生平台级变化

例如：

- `v1.9.4 -> v2.0.0`

## 渠道约定

建议统一为 3 个渠道：

- `stable`
- `beta`
- `internal`

规则：

- 默认用户安装包使用 `stable`
- 开发自测与灰度验证使用 `internal`
- 小范围外部测试使用 `beta`

## 远程配置对应关系

远程 `version.json` 中以下字段必须与本地版本策略保持一致：

- `schema_version`
- `channel`
- `version`
- `build`
- `release_date`
- `min_supported_version`
- `force_update`

建议：

- `version` 与 [src/version.py](/Users/chen/Code/MicroFlow/src/version.py) 保持一致
- `build` 单调递增，不回退
- `release_date` 使用绝对日期，例如 `2026-04-10`

## 更新日志维护规则

更新日志统一记录在 [CHANGELOG.md](/Users/chen/Code/MicroFlow/CHANGELOG.md)。

建议结构：

- `Unreleased`
- `Added`
- `Changed`
- `Fixed`
- `Validation`

每次准备正式发布时，执行以下步骤：

1. 将 `Unreleased` 中已完成内容整理为一个正式版本节
2. 更新 [src/version.py](/Users/chen/Code/MicroFlow/src/version.py)
3. 更新 [docs/version.template.json](/Users/chen/Code/MicroFlow/docs/version.template.json)
4. 在远程 `version.json` 中同步新版本号和下载链接

## 发布前最小动作清单

进入下一次正式版本发布前，至少完成：

1. 更新 [CHANGELOG.md](/Users/chen/Code/MicroFlow/CHANGELOG.md)
2. 更新 [src/version.py](/Users/chen/Code/MicroFlow/src/version.py)
3. 更新 [docs/version.template.json](/Users/chen/Code/MicroFlow/docs/version.template.json)
4. 确认 `pytest -q` 通过
5. 确认安装包链接、`sha256`、渠道字段、公告字段已同步
