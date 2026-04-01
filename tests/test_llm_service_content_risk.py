import types
import unittest
from unittest.mock import Mock, patch

from src.llm_service import LLMService
from src.utils.llm_safety import sanitize_llm_provider_risk_text


def _build_response(content: str):
    return types.SimpleNamespace(
        choices=[
            types.SimpleNamespace(
                message=types.SimpleNamespace(content=content)
            )
        ]
    )


class LLMServiceContentRiskTests(unittest.TestCase):
    def setUp(self):
        self.service = LLMService(api_key="", base_url="https://example.com/v1")

    def test_sanitize_helper_replaces_builtin_risk_terms(self):
        text = "在习近平总书记和党中央的坚强领导下，学校各项工作顺利推进。"
        sanitized, replacements = sanitize_llm_provider_risk_text(text)

        self.assertTrue(replacements)
        self.assertNotIn("习近平总书记", sanitized)
        self.assertNotIn("党中央", sanitized)
        self.assertIn("上级统一部署", sanitized)

    def test_content_risk_auto_retries_with_sanitized_user_content(self):
        create_mock = Mock(
            side_effect=[
                RuntimeError(
                    "Error code: 400 - {'error': {'message': 'Content Exists Risk'}}"
                ),
                _build_response("### 摘要\n- 已自动降险后完成总结。"),
            ]
        )
        self.service.client = types.SimpleNamespace(
            chat=types.SimpleNamespace(
                completions=types.SimpleNamespace(create=create_mock)
            )
        )

        fake_config = Mock()
        fake_config.get_api_balance_ok.return_value = True

        with patch("src.llm_service._get_config_service", return_value=fake_config):
            result = self.service._generate_with_retry(
                title="测试文章",
                raw_text="在习近平总书记和党中央的坚强领导下，学校各项工作顺利推进。",
                system_prompt="system",
                user_content="以下是文章《测试文章》的正文内容：在习近平总书记和党中央的坚强领导下，学校各项工作顺利推进。",
                priority="manual",
                cancel_event=None,
                target_label="公文",
            )

        self.assertIn("已自动降险后完成总结", result)
        self.assertEqual(create_mock.call_count, 2)
        first_call_user_content = create_mock.call_args_list[0].kwargs["messages"][1]["content"]
        second_call_user_content = create_mock.call_args_list[1].kwargs["messages"][1]["content"]
        self.assertIn("习近平总书记", first_call_user_content)
        self.assertNotIn("习近平总书记", second_call_user_content)
        self.assertIn("上级统一部署", second_call_user_content)

    def test_content_risk_returns_guidance_after_retry_still_blocked(self):
        create_mock = Mock(
            side_effect=[
                RuntimeError(
                    "Error code: 400 - {'error': {'message': 'Content Exists Risk'}}"
                ),
                RuntimeError(
                    "Error code: 400 - {'error': {'message': 'Content Exists Risk'}}"
                ),
            ]
        )
        self.service.client = types.SimpleNamespace(
            chat=types.SimpleNamespace(
                completions=types.SimpleNamespace(create=create_mock)
            )
        )

        fake_config = Mock()
        fake_config.get_api_balance_ok.return_value = True

        with patch("src.llm_service._get_config_service", return_value=fake_config):
            result = self.service._generate_with_retry(
                title="测试文章",
                raw_text="在习近平总书记和党中央的坚强领导下，学校各项工作顺利推进。",
                system_prompt="system",
                user_content="以下是文章《测试文章》的正文内容：在习近平总书记和党中央的坚强领导下，学校各项工作顺利推进。",
                priority="manual",
                cancel_event=None,
                target_label="公文",
            )

        self.assertIn("内容触发模型风控", result)
        self.assertEqual(create_mock.call_count, 2)


if __name__ == "__main__":
    unittest.main()
