"""腾讯云函数 SCF 版本的遥测接收端 - 带 COS 持久化存储

无需额外依赖，使用 HTTP 直接写入 COS
"""

import hashlib
import hmac
import json
import time
import urllib.request
from datetime import datetime
from typing import Any, Dict, List
from urllib.parse import quote


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


def save_to_cos_http(events: List[Dict[str, Any]], secret_id: str, secret_key: str, bucket: str, region: str) -> bool:
    """使用 HTTP 请求保存事件到 COS"""
    try:
        # 生成文件路径：telemetry/events/2026-04-03/1775185760.ndjson
        date_str = datetime.now().strftime("%Y-%m-%d")
        timestamp = int(time.time())
        object_key = f"telemetry/events/{date_str}/{timestamp}.ndjson"

        # 转换为 NDJSON 格式
        ndjson_content = "\n".join(
            json.dumps(event, ensure_ascii=False, separators=(",", ":"))
            for event in events
        )
        body = ndjson_content.encode("utf-8")

        # COS 请求参数
        host = f"{bucket}.cos.{region}.myqcloud.com"
        url = f"https://{host}/{object_key}"
        method = "PUT"

        # 生成签名
        http_string = f"{method}\n/{object_key}\n\nhost={host}\n"
        string_to_sign = f"sha1\n{int(time.time())}\n{hashlib.sha1(http_string.encode()).hexdigest()}\n"
        sign_key = hmac.new(secret_key.encode(), f"{int(time.time())}".encode(), hashlib.sha1).hexdigest()
        signature = hmac.new(sign_key.encode(), string_to_sign.encode(), hashlib.sha1).hexdigest()

        authorization = (
            f"q-sign-algorithm=sha1&"
            f"q-ak={secret_id}&"
            f"q-sign-time={int(time.time())};{int(time.time()) + 3600}&"
            f"q-key-time={int(time.time())};{int(time.time()) + 3600}&"
            f"q-header-list=host&"
            f"q-url-param-list=&"
            f"q-signature={signature}"
        )

        # 发送请求
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Host": host,
                "Authorization": authorization,
                "Content-Type": "application/x-ndjson",
                "Content-Length": str(len(body)),
            },
            method=method,
        )

        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status == 200:
                print(f"✅ 已保存 {len(events)} 条事件到 COS: {object_key}")
                return True
            else:
                print(f"❌ COS 返回错误: {response.status}")
                return False

    except Exception as e:
        print(f"❌ 保存到 COS 失败: {e}")
        import traceback
        traceback.print_exc()
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

        # 打印到云函数日志
        print(f"📊 收到 {len(normalized_events)} 条事件")
        for evt in normalized_events[:3]:  # 只打印前3条
            print(
                f"  - {evt.get('event')} from {evt.get('platform')} {evt.get('app_version')}"
            )

        # 尝试保存到 COS
        import os
        secret_id = os.environ.get("COS_SECRET_ID")
        secret_key = os.environ.get("COS_SECRET_KEY")
        bucket = os.environ.get("COS_BUCKET")
        region = os.environ.get("COS_REGION", "ap-guangzhou")

        if all([secret_id, secret_key, bucket]):
            save_to_cos_http(normalized_events, secret_id, secret_key, bucket, region)
        else:
            print("⚠️  未配置 COS 环境变量，跳过持久化存储")

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
        print(f"❌ 处理请求失败: {e}")
        import traceback
        traceback.print_exc()
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)}),
        }
