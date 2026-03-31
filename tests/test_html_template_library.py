import unittest
import sys
import types

sys.modules.setdefault("webview", types.SimpleNamespace(windows=[]))

from src.services.html_template_library import (
    build_site_profile,
    match_template_candidates,
)


class HtmlTemplateLibraryTests(unittest.TestCase):
    def test_match_template_candidates_prefers_sztu_template(self):
        html = """
        <html>
          <head><title>创意设计学院 通知公告</title></head>
          <body>
            <ul class="list-gl">
              <li>
                <a href="/info/1001.htm" title="文章 A">文章 A</a>
                <span>2026-03-30</span>
              </li>
            </ul>
            <div class="wp_articlecontent">正文</div>
          </body>
        </html>
        """

        candidates = match_template_candidates(
            "https://design.sztu.edu.cn/xydt/tzgg.htm",
            html,
            target_fields=["title", "url", "date"],
            limit=3,
        )

        self.assertTrue(candidates)
        self.assertEqual(candidates[0]["id"], "sztu_standard_ul_list")
        self.assertEqual(candidates[0]["profile_label"], "高校院系站")
        self.assertIn("dom_marker", candidates[0]["matched_by"])

    def test_build_site_profile_returns_template_backed_profile(self):
        html = """
        <html>
          <head><title>某学院 新闻中心</title></head>
          <body>
            <ul class="list-gl">
              <li><a href="/a">文章 A</a></li>
            </ul>
          </body>
        </html>
        """

        profile = build_site_profile(
            "https://music.sztu.edu.cn/xydt.htm",
            html,
            target_fields=["title", "url", "date"],
        )

        self.assertEqual(profile["id"], "college_department")
        self.assertEqual(profile["recommended_template_id"], "sztu_standard_ul_list")
        self.assertEqual(profile["confidence_label"], "高置信")


if __name__ == "__main__":
    unittest.main()
