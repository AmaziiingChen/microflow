#!/usr/bin/env python3
"""MicroFlow 匿名遥测接收端示例。

最小依赖：
    pip install fastapi uvicorn

启动方式：
    uvicorn scripts.telemetry_receiver_fastapi:app --host 0.0.0.0 --port 8787

说明：
1. 当前示例将事件写入本地 sqlite 与 ndjson，便于快速验证发布链路。
2. 推荐部署在独立服务域名下，例如：
   https://api.your-domain.com/microflow/telemetry
3. 不要与 COS 静态资源桶混用；version.json 负责下发配置，遥测端点负责接收 POST。
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException, Request


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "telemetry_receiver"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "telemetry_events.db"
NDJSON_PATH = DATA_DIR / "telemetry_events.ndjson"

app = FastAPI(title="MicroFlow Telemetry Receiver", version="1.0.0")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS telemetry_events (
            event_id TEXT PRIMARY KEY,
            event_name TEXT NOT NULL,
            ts INTEGER NOT NULL,
            install_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            app_version TEXT NOT NULL,
            channel TEXT NOT NULL,
            platform TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            received_at INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_telemetry_events_name_ts "
        "ON telemetry_events(event_name, ts DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_telemetry_events_platform_ts "
        "ON telemetry_events(platform, ts DESC)"
    )
    return conn


def normalize_events(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    events = payload.get("events")
    if not isinstance(events, list):
        raise HTTPException(status_code=400, detail="missing events list")

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
        normalized.append(
            {
                "event_id": event_id,
                "event_name": event_name,
                "ts": int(item.get("ts") or 0),
                "install_id": install_id,
                "session_id": session_id,
                "app_version": str(item.get("app_version") or "").strip(),
                "channel": str(item.get("channel") or "").strip(),
                "platform": str(item.get("platform") or "").strip(),
                "payload_json": json.dumps(item, ensure_ascii=False, separators=(",", ":")),
            }
        )
    return normalized


@app.get("/healthz")
async def healthz() -> Dict[str, Any]:
    return {"ok": True, "ts": int(time.time())}


@app.post("/microflow/telemetry")
async def ingest_telemetry(request: Request) -> Dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid json: {exc}") from exc

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="invalid payload")

    normalized_events = normalize_events(payload)
    if not normalized_events:
        return {"ok": True, "accepted": 0, "received_at": int(time.time())}

    received_at = int(time.time())
    conn = get_conn()
    inserted = 0
    try:
        with conn:
            for event in normalized_events:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO telemetry_events (
                        event_id,
                        event_name,
                        ts,
                        install_id,
                        session_id,
                        app_version,
                        channel,
                        platform,
                        payload_json,
                        received_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event["event_id"],
                        event["event_name"],
                        event["ts"],
                        event["install_id"],
                        event["session_id"],
                        event["app_version"],
                        event["channel"],
                        event["platform"],
                        event["payload_json"],
                        received_at,
                    ),
                )
                inserted += conn.execute("SELECT changes()").fetchone()[0]
    finally:
        conn.close()

    with NDJSON_PATH.open("a", encoding="utf-8") as f:
        for event in normalized_events:
            f.write(event["payload_json"] + "\n")

    return {
        "ok": True,
        "accepted": inserted,
        "received_at": received_at,
        "schema_version": str(payload.get("schema_version") or "1"),
    }
