import json
import os
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import Mock, patch

import requests

from tests import feedparser_stub

sys.modules.setdefault("webview", types.SimpleNamespace(windows=[]))
sys.modules.setdefault(
    "feedparser", types.SimpleNamespace(parse=feedparser_stub.parse)
)

from src import api as api_module
from src.services.config_service import ConfigService
from src.services.telemetry_service import TelemetryService


class DummyConfigService:
    def __init__(self):
        self.data = {
            "channel": "stable",
            "telemetryEnabled": True,
            "telemetryErrorReportsEnabled": True,
            "telemetryConsentStatus": "enabled",
            "telemetryInstallId": "",
        }

    def get(self, key, default=None):
        return self.data.get(key, default)

    def to_dict(self):
        return dict(self.data)

    def save(self, config_dict):
        self.data.update(config_dict)
        return True


class InMemoryTelemetryDb:
    def __init__(self):
        self.events = []

    def enqueue_telemetry_event(
        self,
        event_id,
        event_name,
        payload_json,
        created_at,
        next_retry_at,
    ):
        self.events.append(
            {
                "event_id": event_id,
                "event_name": event_name,
                "payload_json": payload_json,
                "created_at": created_at,
                "next_retry_at": next_retry_at,
                "retry_count": 0,
                "status": "pending",
                "last_error": "",
                "sent_at": None,
            }
        )
        return True

    def get_pending_telemetry_events(self, limit=30):
        now_ts = int(time.time())
        return [
            dict(item)
            for item in self.events
            if item["status"] in ("pending", "failed")
            and int(item["next_retry_at"] or 0) <= now_ts
        ][:limit]

    def mark_telemetry_events_sent(self, event_ids):
        matched = 0
        event_id_set = set(event_ids or [])
        for item in self.events:
            if item["event_id"] in event_id_set:
                item["status"] = "sent"
                item["sent_at"] = int(time.time())
                matched += 1
        return matched

    def mark_telemetry_events_failed(self, failure_items):
        failure_map = {
            str(item.get("event_id") or "").strip(): item for item in failure_items or []
        }
        matched = 0
        for item in self.events:
            event_id = str(item.get("event_id") or "").strip()
            if event_id not in failure_map:
                continue
            payload = failure_map[event_id]
            item["status"] = "failed"
            item["retry_count"] = int(payload.get("retry_count") or 0)
            item["next_retry_at"] = int(payload.get("next_retry_at") or 0)
            item["last_error"] = str(payload.get("last_error") or "")
            matched += 1
        return matched

    def clear_telemetry_events(self, statuses=None):
        if statuses:
            keep_statuses = set(statuses)
            before = len(self.events)
            self.events = [item for item in self.events if item["status"] not in keep_statuses]
            return before - len(self.events)
        deleted = len(self.events)
        self.events = []
        return deleted

    def prune_sent_telemetry_events(self, older_than_ts):
        before = len(self.events)
        self.events = [
            item
            for item in self.events
            if not (
                item["status"] == "sent"
                and int(item.get("sent_at") or 0) < int(older_than_ts or 0)
            )
        ]
        return before - len(self.events)

    def get_telemetry_queue_stats(self, now_ts=None):
        safe_now = int(now_ts or 0) or int(time.time())
        pending_count = sum(1 for item in self.events if item["status"] == "pending")
        failed_count = sum(1 for item in self.events if item["status"] == "failed")
        sent_count = sum(1 for item in self.events if item["status"] == "sent")
        ready_count = sum(
            1
            for item in self.events
            if item["status"] in ("pending", "failed")
            and int(item.get("next_retry_at") or 0) <= safe_now
        )
        return {
            "total_count": len(self.events),
            "pending_count": pending_count,
            "failed_count": failed_count,
            "sent_count": sent_count,
            "ready_count": ready_count,
        }


class TelemetryServiceTests(unittest.TestCase):
    def setUp(self):
        self.config_service = DummyConfigService()
        self.db = InMemoryTelemetryDb()
        self.service = TelemetryService(
            self.config_service,
            self.db,
            app_version="v1.0.0",
        )
        self.service._remote_config["endpoint"] = "https://telemetry.example.com/ingest"

    def tearDown(self):
        self.service.shutdown(flush=False)

    def test_init_generates_install_id_and_persists_to_config(self):
        install_id = self.config_service.get("telemetryInstallId", "")
        self.assertTrue(install_id)
        self.assertEqual(self.service.get_status_payload()["install_id_ready"], True)

    def test_track_queues_event_and_hashes_custom_url_fields(self):
        result = self.service.track(
            "article_open",
            {
                "rule_id": "rule-001",
                "source_name": "我的规则",
                "source_url": "https://example.com/articles/123",
                "has_ai_summary": True,
            },
            force=True,
        )

        self.assertEqual(result["status"], "queued")
        self.assertEqual(len(self.db.events), 1)
        payload = json.loads(self.db.events[0]["payload_json"])
        props = payload["props"]
        self.assertNotIn("source_url", props)
        self.assertIn("source_urlDomainHash", props)
        self.assertIn("sourceNameHash", props)
        self.assertTrue(props["has_ai_summary"])

    def test_flush_marks_events_sent_on_success(self):
        self.service.track("app_launch", {"source_name": "启动"}, force=True)

        response = Mock(status_code=200, text="ok")
        with patch("src.services.telemetry_service.requests.post", return_value=response) as post_mock:
            result = self.service.flush(force=True)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["accepted"], 1)
        self.assertEqual(self.db.get_telemetry_queue_stats()["sent_count"], 1)
        post_mock.assert_called_once()

    def test_flush_marks_retry_state_on_failure(self):
        self.service.track("app_launch", {"source_name": "启动"}, force=True)
        before_flush = int(time.time())

        with patch(
            "src.services.telemetry_service.requests.post",
            side_effect=requests.RequestException("boom"),
        ):
            result = self.service.flush(force=True)

        self.assertEqual(result["status"], "error")
        self.assertEqual(self.db.get_telemetry_queue_stats()["failed_count"], 1)
        failed_item = self.db.events[0]
        self.assertEqual(failed_item["retry_count"], 1)
        self.assertGreaterEqual(int(failed_item["next_retry_at"]), before_flush + 60)
        self.assertIn("boom", failed_item["last_error"])

    def test_flush_skips_when_telemetry_disabled_without_force(self):
        self.service.track("app_launch", {"source_name": "启动"}, force=True)
        self.config_service.data["telemetryEnabled"] = False
        self.config_service.data["telemetryErrorReportsEnabled"] = False
        self.config_service.data["telemetryConsentStatus"] = "disabled"

        with patch("src.services.telemetry_service.requests.post") as post_mock:
            result = self.service.flush(force=False)

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["message"], "遥测已关闭")
        self.assertEqual(self.db.get_telemetry_queue_stats()["pending_count"], 1)
        post_mock.assert_not_called()

    def test_flush_force_still_sends_queued_events(self):
        self.service.track("app_launch", {"source_name": "启动"}, force=True)
        self.config_service.data["telemetryEnabled"] = False
        self.config_service.data["telemetryErrorReportsEnabled"] = False
        self.config_service.data["telemetryConsentStatus"] = "disabled"

        response = Mock(status_code=200, text="ok")
        with patch(
            "src.services.telemetry_service.requests.post",
            return_value=response,
        ) as post_mock:
            result = self.service.flush(force=True)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["accepted"], 1)
        post_mock.assert_called_once()


class ConfigServiceTelemetryFieldsTests(unittest.TestCase):
    def test_telemetry_fields_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = os.path.join(tmp_dir, "config.json")
            service = ConfigService(config_path, default_prompt="test-prompt")
            payload = service.to_dict()
            payload.update(
                {
                    "channel": "beta",
                    "telemetryEnabled": True,
                    "telemetryErrorReportsEnabled": True,
                    "telemetryConsentStatus": "enabled",
                    "telemetryInstallId": "install-123",
                    "telemetryNoticeShown": True,
                }
            )

            self.assertTrue(service.save(payload))

            reloaded = ConfigService(config_path, default_prompt="test-prompt").load()
            self.assertTrue(reloaded.telemetry_enabled)
            self.assertTrue(reloaded.telemetry_error_reports_enabled)
            self.assertEqual(reloaded.channel, "beta")
            self.assertEqual(reloaded.telemetry_consent_status, "enabled")
            self.assertEqual(reloaded.telemetry_install_id, "install-123")
            self.assertTrue(reloaded.telemetry_notice_shown)


class ApiTelemetryMethodsTests(unittest.TestCase):
    def test_get_telemetry_status_proxies_service(self):
        api = api_module.Api.__new__(api_module.Api)
        api._telemetry = Mock()
        api._telemetry.get_status_payload.return_value = {
            "status": "success",
            "queue": {"total_count": 2},
        }

        result = api.get_telemetry_status()

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["queue"]["total_count"], 2)
        api._telemetry.get_status_payload.assert_called_once_with()

    def test_clear_telemetry_queue_proxies_service(self):
        api = api_module.Api.__new__(api_module.Api)
        api._telemetry = Mock()
        api._telemetry.clear_queue.return_value = {
            "status": "success",
            "deleted_count": 3,
        }

        result = api.clear_telemetry_queue()

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["deleted_count"], 3)
        api._telemetry.clear_queue.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
