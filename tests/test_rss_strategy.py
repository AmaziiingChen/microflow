import unittest

from src.utils.rss_strategy import (
    attach_rss_strategy_metadata,
    analyze_rss_source_profile,
    get_rss_strategy_catalog,
    resolve_rss_rule_strategy,
)


class RssStrategyTests(unittest.TestCase):
    def test_infers_news_profile_for_compact_text_sample(self):
        sample = {
            "body_text": "这是较短的资讯样本，主要提供简洁更新和核心动作说明。",
            "content_blocks": [{"type": "paragraph"}],
            "image_assets": [],
        }

        result = analyze_rss_source_profile([sample])

        self.assertEqual(result["profile"], "news")

    def test_infers_longform_profile_for_long_structured_sample(self):
        sample = {
            "body_text": (
                "### 背景\n\n"
                + ("这是一个很长的段落，用来模拟长文正文的展开和上下文说明。" * 80)
            ),
            "content_blocks": [
                {"type": "title"},
                {"type": "title"},
                {"type": "title"},
                {"type": "paragraph"},
                {"type": "list"},
            ],
            "image_assets": [],
        }

        result = analyze_rss_source_profile([sample])

        self.assertEqual(result["profile"], "longform")

    def test_infers_visual_profile_for_image_heavy_sample(self):
        sample = {
            "body_text": "图文快讯，仅包含简短说明。",
            "content_blocks": [{"type": "paragraph"}],
            "image_assets": [{}, {}, {}, {}],
        }

        result = analyze_rss_source_profile([sample])

        self.assertEqual(result["profile"], "visual")

    def test_resolve_strategy_uses_profile_defaults_when_ai_enabled(self):
        strategy = resolve_rss_rule_strategy(
            {
                "source_type": "rss",
                "source_profile": "longform",
                "enable_ai_formatting": True,
                "enable_ai_summary": True,
            }
        )

        self.assertEqual(strategy["template_id"], "longform_focus")
        self.assertEqual(strategy["effective_max_items"], 12)
        self.assertIn("长文", strategy["effective_formatting_prompt"])
        self.assertIn("长文", strategy["effective_summary_prompt"])

    def test_resolve_strategy_keeps_ai_disabled_when_switches_are_off(self):
        strategy = resolve_rss_rule_strategy(
            {
                "source_type": "rss",
                "source_profile": "news",
                "enable_ai_formatting": False,
                "enable_ai_summary": False,
            }
        )

        self.assertEqual(strategy["effective_formatting_prompt"], "")
        self.assertEqual(strategy["effective_summary_prompt"], "")

    def test_attach_strategy_metadata_persists_inferred_profile(self):
        decorated = attach_rss_strategy_metadata(
            {
                "source_type": "rss",
                "task_name": "Campus Feed",
                "url": "https://example.com/feed.xml",
            },
            sample_articles=[
                {
                    "body_text": "这是较短的资讯样本，主要提供简洁更新和核心动作说明。",
                    "content_blocks": [{"type": "paragraph"}],
                    "image_assets": [],
                }
            ],
        )

        self.assertEqual(decorated["source_profile"], "news")
        self.assertEqual(decorated["source_profile_source"], "inferred")
        self.assertEqual(decorated["source_template_id"], "news_brief")

    def test_manual_template_sets_manual_profile(self):
        strategy = resolve_rss_rule_strategy(
            {
                "source_type": "rss",
                "source_template_id": "longform_focus",
                "enable_ai_formatting": True,
            }
        )

        self.assertEqual(strategy["profile"], "longform")
        self.assertEqual(strategy["profile_source"], "manual")
        self.assertEqual(strategy["template_id"], "longform_focus")

    def test_strategy_catalog_exposes_templates_and_profiles(self):
        catalog = get_rss_strategy_catalog()

        self.assertGreaterEqual(len(catalog["profiles"]), 3)
        self.assertGreaterEqual(len(catalog["templates"]), 3)
        self.assertIn("profile_label", catalog["templates"][0])


if __name__ == "__main__":
    unittest.main()
