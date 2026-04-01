# MicroFlow 遥测落地指南

更新时间：2026-04-01

## 目标

把当前已经完成的“客户端匿名埋点 + 本地队列”补成一条真正可上线的链路：

1. 客户端继续本地入队
2. 云端 `version.json` 下发遥测开关与上报地址
3. 独立的 HTTPS 接收端保存事件
4. 后续再做简单查询与看板

当前项目里，客户端部分已经具备：

- 事件采集
- 脱敏
- 本地队列
- 批量 flush
- 失败退避重试
- 远程 `version.json -> telemetry` 配置读取

现在真正缺的只有“服务端接收端”。

## 一句话架构

```text
MicroFlow 客户端
  -> 本地 SQLite 队列
  -> POST https://api.example.com/microflow/telemetry
  -> 你的接收服务
  -> SQLite / MySQL / PostgreSQL / NDJSON
  -> 后续统计分析
```

注意：

- `version.json` 继续放在腾讯云 COS
- 遥测接收端不要放在 COS
- 遥测接收端必须是支持 `POST` 的服务

## 推荐发布形态

对你现在这个阶段，最推荐的方案是：

### 方案 A：腾讯云 CVM + Nginx + FastAPI

优点：

- 最直观
- 最容易排障
- 方便后面扩展成后台
- 最适合现在这种需要快速迭代的小规模产品

### 方案 B：腾讯云云函数 SCF + API 网关

优点：

- 前期省机器
- 运维少

缺点：

- 调试体验一般
- 日志与数据库联动没有 CVM 直观

如果你现在主要目的是“先收集到真实用户数据”，优先选方案 A。

## 客户端现在会上报什么

客户端当前会批量发送如下结构：

```json
{
  "schema_version": "1",
  "channel": "stable",
  "events": [
    {
      "event_id": "uuid",
      "event": "article_open",
      "ts": 1770000000,
      "install_id": "uuid",
      "session_id": "uuid",
      "app_version": "v1.0.0",
      "channel": "stable",
      "platform": "windows",
      "platform_version": "10.0.22631",
      "arch": "x64",
      "locale": "zh_CN",
      "props": {}
    }
  ]
}
```

## 当前已埋点的核心事件

- `app_launch`
- `app_exit`
- `startup_check_result`
- `update_check_result`
- `update_available`
- `update_download_click`
- `source_fetch_result`
- `article_open`
- `detail_mode_switch`
- `article_copy`
- `article_snapshot`
- `article_favorite_toggle`
- `ai_regenerate_request`
- `ai_regenerate_result`
- `search_submit`
- `search_result_empty`
- `note_create`
- `note_delete`
- `custom_rule_test_result`
- `custom_rule_save_result`
- `error_python`
- `error_js`
- `error_api`

## 当前明确不会上传的内容

- 文章正文
- 用户批注内容
- API Key
- SMTP 密码
- 提示词全文
- 完整自定义规则
- 完整 URL

当前已经做了脱敏：

- 自定义源 URL 只上传域名哈希
- 自定义源名称会做哈希
- 错误堆栈只上传哈希与短消息

## 最小接收端接口要求

你的服务端只需要实现一个接口：

- `POST /microflow/telemetry`

建议返回：

```json
{
  "ok": true,
  "accepted": 30,
  "received_at": 1770000000
}
```

状态码要求：

- `2xx`：表示客户端本批事件已成功接收，可标记为已发送
- 非 `2xx`：客户端会保留在本地队列，稍后重试

## 仓库内已提供的最小接收端示例

可直接参考：

- [scripts/telemetry_receiver_fastapi.py](/Users/chen/Code/MicroFlow/scripts/telemetry_receiver_fastapi.py)
- [packaging/telemetry/nginx.microflow-telemetry.conf](/Users/chen/Code/MicroFlow/packaging/telemetry/nginx.microflow-telemetry.conf)
- [packaging/telemetry/microflow-telemetry.service](/Users/chen/Code/MicroFlow/packaging/telemetry/microflow-telemetry.service)
- [TELEMETRY_TENCENT_CVM_COMMANDS.md](/Users/chen/Code/MicroFlow/docs/TELEMETRY_TENCENT_CVM_COMMANDS.md)

依赖：

```bash
pip install fastapi uvicorn
```

本地启动：

```bash
uvicorn scripts.telemetry_receiver_fastapi:app --host 0.0.0.0 --port 8787
```

健康检查：

```bash
curl http://127.0.0.1:8787/healthz
```

## 腾讯云部署建议

## 1. 购买一台最小 CVM

推荐最低配即可，例如：

- 2C2G
- Ubuntu 22.04

## 2. 部署接收服务

把仓库中的接收端脚本上传到服务器，安装依赖后运行：

```bash
pip install fastapi uvicorn
uvicorn telemetry_receiver_fastapi:app --host 127.0.0.1 --port 8787
```

建议后续改成 `systemd` 托管。

项目中已经附带可直接改路径使用的 `systemd` 样例：

- [packaging/telemetry/microflow-telemetry.service](/Users/chen/Code/MicroFlow/packaging/telemetry/microflow-telemetry.service)

## 3. 用 Nginx 暴露 HTTPS

建议域名：

- `api.your-domain.com`

反向代理路径：

- `https://api.your-domain.com/microflow/telemetry`

项目中已经附带可直接改域名使用的 Nginx 样例：

- [packaging/telemetry/nginx.microflow-telemetry.conf](/Users/chen/Code/MicroFlow/packaging/telemetry/nginx.microflow-telemetry.conf)

## 4. 打开防火墙与证书

要求：

- 对外只暴露 `443`
- `8787` 只允许本机访问

## version.json 推荐配置

发布初期建议先全量采样，等用户量起来后再降。

```json
"telemetry": {
  "enabled": true,
  "endpoint": "https://api.your-domain.com/microflow/telemetry",
  "batch_size": 30,
  "flush_interval_sec": 1800,
  "sample_rate": 1.0
}
```

### 渠道建议

#### `stable`

发布初期：

```json
"sample_rate": 1.0
```

用户变多后再降到：

```json
"sample_rate": 0.3
```

#### `beta`

建议保持：

```json
"sample_rate": 1.0
```

#### `internal`

建议保持：

```json
"sample_rate": 1.0
```

## 你最终能看到哪些数据

接入后，你可以很快回答这些问题：

- 多少人安装了软件
- Windows 和 macOS 各占多少
- 哪个版本最稳定
- 哪些功能最常用
- 哪些来源抓取最容易失败
- AI 重新生成功能是否被频繁使用
- 搜索、复制、截图、查看原文、附件下载的使用热度
- 哪个渠道报错更多

## 建议的第一阶段分析维度

优先统计：

- 日活安装数
- 平台分布
- 版本分布
- 功能事件 Top 20
- 错误事件 Top 20
- `source_fetch_result` 按来源聚合失败率
- `ai_regenerate_result` 成功率
- `update_download_click` 转化

## 上线验证步骤

## 1. 先本地联通

本地起接收端后，把 `version.json` 里的 `telemetry.endpoint` 改到本地地址进行验证。

## 2. 在设置页检查

你应该看到：

- 上报状态不再是“等待上报服务接入”
- “立即上报”按钮可点击

## 3. 手动触发一轮

做几次这些动作：

- 打开文章
- 复制
- 截图
- 查看原文
- 执行一次更新检查

然后点击“立即上报”。

## 4. 检查接收端

确认：

- sqlite 已写入事件
- ndjson 已追加事件

## 安全边界建议

- 不要把遥测端点和 COS 混在一起
- 不要把鉴权密钥写进 `version.json`
- 接收端不要记录客户端 IP 到业务表里
- 如果后续需要鉴权，优先加服务端反向代理白名单或简单的固定 header 校验
- 不要在接收端二次补全文章正文或规则明文

## 当前最适合你的结论

你现在不用再做客户端埋点开发，已经够用了。

最短路径就是：

1. 部署一个最小接收端
2. 在 `version.json` 配好 `telemetry.endpoint`
3. 先把 `sample_rate` 设成 `1.0`
4. 发布后观察一周真实数据
5. 再决定是否扩展看板和分析后台

如果你准备按腾讯云 CVM 直接部署，请继续参考：

- [TELEMETRY_TENCENT_CVM_COMMANDS.md](/Users/chen/Code/MicroFlow/docs/TELEMETRY_TENCENT_CVM_COMMANDS.md)
