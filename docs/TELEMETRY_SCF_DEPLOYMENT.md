# 腾讯云 Serverless 遥测部署指南

使用腾讯云函数（SCF）+ API 网关部署遥测接收端，**几乎零成本**。

## 成本说明

- 云函数 SCF：每月 **100万次** 免费调用
- API 网关：每月 **100万次** 免费调用
- 对于小规模应用（<1000用户），**完全免费**

## 部署步骤

### 1. 登录腾讯云控制台

访问：https://console.cloud.tencent.com/scf

### 2. 创建云函数

1. 点击「新建」
2. 选择「从头开始」
3. 填写基本信息：
   - **函数名称**：`microflow-telemetry`
   - **地域**：选择广州（与你的 COS 同地域）
   - **运行环境**：`Python 3.9`
   - **函数类型**：`事件函数`

### 3. 配置函数代码

#### 方式A：在线编辑（推荐）

1. 在「函数代码」区域，选择「在线编辑」
2. 删除默认代码
3. 复制 `scripts/telemetry_receiver_scf.py` 的全部内容
4. 粘贴到编辑器中
5. 点击「部署」

#### 方式B：本地上传

```bash
# 打包代码
cd /Users/chen/Code/MicroFlow
zip telemetry_scf.zip scripts/telemetry_receiver_scf.py

# 在控制台选择「本地上传zip包」，上传 telemetry_scf.zip
```

### 4. 配置执行角色（可选）

如果需要保存数据到 COS：

1. 在「函数配置」→「执行角色」中
2. 选择「新建角色」或使用已有角色
3. 确保角色有 COS 写入权限

### 5. 配置环境变量（可选）

如果需要保存到 COS，在「环境变量」中添加：

| 键 | 值 | 说明 |
|---|---|---|
| `COS_BUCKET` | `microflow-1412347033` | 你的 COS 存储桶名称 |
| `COS_REGION` | `ap-guangzhou` | COS 地域 |
| `COS_SECRET_ID` | `你的SecretId` | 腾讯云密钥ID |
| `COS_SECRET_KEY` | `你的SecretKey` | 腾讯云密钥Key |

**注意**：如果不配置这些环境变量，事件只会打印到云函数日志，不会持久化存储。

### 6. 配置 API 网关触发器

1. 在「触发管理」标签页，点击「创建触发器」
2. 选择「API 网关触发器」
3. 配置参数：
   - **API 服务**：选择「新建API服务」
   - **服务名称**：`microflow-telemetry-api`
   - **发布环境**：`发布`
   - **请求方法**：`POST`
   - **集成响应**：启用
4. 点击「提交」

### 7. 获取 API 地址

创建成功后，会显示类似这样的地址：

```
https://service-xxx-1234567890.gz.apigw.tencentcs.com/release/microflow-telemetry
```

**复制这个地址**，这就是你的遥测接收端点。

### 8. 更新 version.json

将获取到的 API 地址配置到 `version.json`：

```json
"telemetry": {
  "enabled": true,
  "endpoint": "https://service-xxx-1234567890.gz.apigw.tencentcs.com/release/microflow-telemetry",
  "batch_size": 30,
  "flush_interval_sec": 1800,
  "sample_rate": 1.0
}
```

### 9. 上传到 COS

将更新后的 `version.json` 上传到你的 COS 存储桶，覆盖原文件。

### 10. 测试验证

1. 重启 MicroFlow 应用
2. 进入设置页面，查看遥测状态
3. 点击「立即上报」
4. 在腾讯云控制台查看云函数日志：
   - 进入云函数详情页
   - 点击「日志查询」标签
   - 查看是否有「收到 X 条事件」的日志

## 查看数据

### 方式1：查看云函数日志

在云函数控制台 → 日志查询，可以看到：

```
收到 5 条事件
  - app_launch from darwin v1.0.0
  - article_open from darwin v1.0.0
  - source_fetch_result from darwin v1.0.0
```

### 方式2：从 COS 下载数据

如果配置了 COS 存储，数据会保存在：

```
telemetry/events/2026-04-03/1775181234.ndjson
telemetry/events/2026-04-03/1775181567.ndjson
```

下载这些文件，每行是一个 JSON 事件。

### 方式3：使用 COS Select 查询

腾讯云 COS 支持直接查询 JSON 文件，无需下载。

## 数据分析

下载 NDJSON 文件后，可以用以下方式分析：

### Python 分析

```python
import json
from collections import Counter

# 读取事件
events = []
with open('events.ndjson', 'r') as f:
    for line in f:
        events.append(json.loads(line))

# 统计事件类型
event_types = Counter(e['event'] for e in events)
print(event_types)

# 统计平台分布
platforms = Counter(e['platform'] for e in events)
print(platforms)
```

### 命令行分析

```bash
# 统计事件类型
cat events.ndjson | jq -r '.event' | sort | uniq -c

# 统计平台分布
cat events.ndjson | jq -r '.platform' | sort | uniq -c

# 查看最近10条事件
tail -10 events.ndjson | jq .
```

## 成本估算

假设你有 **100个活跃用户**，每人每天产生 **50个事件**：

- 每天事件数：100 × 50 = 5,000
- 每月事件数：5,000 × 30 = 150,000
- 云函数调用次数：约 5,000 次/月（批量上报）
- API 网关调用次数：约 5,000 次/月

**费用：0元**（远低于免费额度）

即使用户增长到 **1000人**，每月也只需要 **5万次调用**，依然在免费额度内。

## 进阶配置

### 1. 配置自定义域名（可选）

如果你有域名，可以在 API 网关中绑定自定义域名：

```
https://api.yourdomain.com/microflow/telemetry
```

### 2. 配置数据库存储（可选）

如果需要实时查询，可以将数据写入：
- 腾讯云 MySQL（按量计费）
- 腾讯云 PostgreSQL（按量计费）
- 腾讯云 MongoDB（按量计费）

修改云函数代码，添加数据库写入逻辑即可。

### 3. 配置告警（可选）

在云函数监控中配置告警：
- 错误率超过 5%
- 调用次数异常

## 常见问题

### Q: 云函数日志保留多久？
A: 默认保留 7 天，可以配置 CLS 日志服务延长保留时间。

### Q: 如何导出历史数据？
A: 如果配置了 COS 存储，直接从 COS 下载。否则需要从云函数日志中提取。

### Q: 如何保护 API 不被滥用？
A: 可以在 API 网关配置：
- IP 访问频率限制
- 密钥认证
- 请求签名验证

### Q: 数据安全吗？
A:
- 传输：API 网关默认使用 HTTPS
- 存储：COS 支持服务端加密
- 访问：通过 CAM 权限控制

## 总结

使用腾讯云 Serverless 方案：
- ✅ **零成本**（免费额度足够）
- ✅ **无需域名**（API 网关提供默认域名）
- ✅ **无需运维**（自动扩缩容）
- ✅ **5分钟部署**（在线编辑即可）
- ✅ **数据安全**（HTTPS + 权限控制）

非常适合初期用户量不大的场景！
