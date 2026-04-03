"""腾讯云函数 SCF 版本的遥测接收端

部署方式：
1. 在腾讯云控制台创建云函数
2. 运行时选择 Python 3.9
3. 上传此文件作为入口文件
4. 配置 API 网关触发器
5. 将生成的 API 地址配置到 version.json

数据存储：
- 使用 COS 存储事件数据（NDJSON 格式）
- 需要配置环境变量：COS_BUCKET, COS_REGION, COS_SECRET_ID, COS_SECRET_KEY
"""

import json
import time
from typing import Any, Dict, List


def normalize_events(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """规范化事件数据"""
    events = payload.get("events")
    if not isinstance(events, list):
        return []

    normalized: List[Dict[str, Any]] = []
    for item in events:
        if not isinstance(item, dict):
            continue
        event_id = str(item.get("event_id") or "").strip()
        event_name = str(item.get("event") or "").strip()
        install_id = str(item.get("install_id") or "").strip()
        session_id = str(item.get("session_id") or "").strip()
        if not event_id or not event_name or not install_id or not session_id:
            continue
        normalized.append(item)
    return normalized


def save_to_cos(events: List[Dict[str, Any]]) -> bool:
    """保存事件到 COS（可选）"""
    try:
        import os
        from qcloud_cos import CosConfig, CosS3Client

        secret_id = os.environ.get("COS_SECRET_ID")
        secret_key = os.environ.get("COS_SECRET_KEY")
        region = os.environ.get("COS_REGION", "ap-guangzhou")
        bucket = os.environ.get("COS_BUCKET")

        if not all([secret_id, secret_key, bucket]):
            print("COS 配置不完整，跳过存储")
            return False

        config = CosConfig(Region=region, SecretId=secret_id, SecretKey=secret_key)
        client = CosS3Client(config)

        # 按日期分片存储
        date_str = time.strftime("%Y-%m-%d", time.localtime())
        timestamp = int(time.time())
        key = f"telemetry/events/{date_str}/{timestamp}.ndjson"

        # 转换为 NDJSON 格式
        ndjson_content = "\n".join(
            json.dumps(event, ensure_ascii=False, separators=(",", ":"))
            for event in events
        )

        client.put_object(
            Bucket=bucket, Body=ndjson_content.encode("utf-8"), Key=key
        )
        print(f"已保存 {len(events)} 条事件到 COS: {key}")
        return True
    except Exception as e:
        print(f"保存到 COS 失败: {e}")
        return False


def main_handler(event, context):
    """云函数入口"""
    try:
        # 解析请求
        if isinstance(event, dict):
            body = event.get("body", "")
            if isinstance(body, str):
                payload = json.loads(body)
            else:
                payload = body
        else:
            payload = json.loads(event)

        if not isinstance(payload, dict):
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "invalid payload"}),
            }

        # 规范化事件
        normalized_events = normalize_events(payload)
        if not normalized_events:
            return {
                "statusCode": 200,
                "body": json.dumps(
                    {
                        "ok": True,
                        "accepted": 0,
                        "received_at": int(time.time()),
                    }
                ),
            }

        # 打印到云函数日志（可在控制台查看）
        print(f"收到 {len(normalized_events)} 条事件")
        for evt in normalized_events[:3]:  # 只打印前3条
            print(
                f"  - {evt.get('event')} from {evt.get('platform')} {evt.get('app_version')}"
            )

        # 尝试保存到 COS（可选）
        save_to_cos(normalized_events)

        # 返回成功响应
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(
                {
                    "ok": True,
                    "accepted": len(normalized_events),
                    "received_at": int(time.time()),
                    "schema_version": str(payload.get("schema_version") or "1"),
                }
            ),
        }

    except Exception as e:
        print(f"处理请求失败: {e}")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)}),
        }
