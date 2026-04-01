import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from tests import feedparser_stub

sys.modules.setdefault("webview", types.SimpleNamespace(windows=[]))
sys.modules.setdefault(
    "feedparser", types.SimpleNamespace(parse=feedparser_stub.parse)
)

from src import api as api_module


class ApiReadOnlyRecoveryTests(unittest.TestCase):
    def make_api(self, *, is_locked=False, previous_config=None):
        api = api_module.Api.__new__(api_module.Api)
        api._config_service = Mock()
        api._config_service.current = SimpleNamespace(is_locked=is_locked)
        api._config_service.to_dict.return_value = previous_config or {
            "themeAppearance": "snow-frost",
            "fontFamily": "sans-serif",
            "trackMode": "continuous",
            "pollingInterval": 60,
            "autoStart": False,
            "isLocked": False,
            "apiBalanceOk": True,
            "updateCooldown": 60,
            "configSign": "server-signature",
            "lastCloudSyncTime": 123456.0,
            "deviceId": "device_abc",
            "telemetryEnabled": True,
            "telemetryErrorReportsEnabled": True,
        }
        api._config_service.save.return_value = True
        api._set_autostart = Mock()
        api._apply_config = Mock()
        api._track_telemetry = Mock()
        return api

    def test_save_config_does_not_relock_when_frontend_payload_is_stale(self):
        api = self.make_api(
            is_locked=False,
            previous_config={
                "themeAppearance": "snow-frost",
                "fontFamily": "sans-serif",
                "trackMode": "continuous",
                "pollingInterval": 60,
                "autoStart": False,
                "isLocked": False,
                "apiBalanceOk": True,
                "updateCooldown": 60,
                "configSign": "server-signature",
                "lastCloudSyncTime": 123456.0,
                "deviceId": "device_abc",
                "telemetryEnabled": True,
                "telemetryErrorReportsEnabled": True,
            },
        )

        result = api.save_config(
            {
                "themeAppearance": "sepia-focus",
                "isLocked": True,
                "apiBalanceOk": False,
                "updateCooldown": 0,
                "configSign": "stale-signature",
                "lastCloudSyncTime": 1.0,
                "deviceId": "device_stale",
                "telemetryEnabled": True,
                "telemetryErrorReportsEnabled": True,
            }
        )

        self.assertEqual(result["status"], "success")
        saved_payload = api._config_service.save.call_args[0][0]
        self.assertEqual(saved_payload["themeAppearance"], "sepia-focus")
        self.assertFalse(saved_payload["isLocked"])
        self.assertTrue(saved_payload["apiBalanceOk"])
        self.assertEqual(saved_payload["updateCooldown"], 60)
        self.assertEqual(saved_payload["configSign"], "server-signature")
        self.assertEqual(saved_payload["lastCloudSyncTime"], 123456.0)
        self.assertEqual(saved_payload["deviceId"], "device_abc")


if __name__ == "__main__":
    unittest.main()
