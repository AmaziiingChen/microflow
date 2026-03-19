#!/usr/bin/env python3
"""
公文通时间分析工具 - 防弹版
修复了所有静态类型警告，增加了异常详细打印与动态限速防封策略
"""

import asyncio
import re
import csv
import time
from pathlib import Path
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup


# ================= 配置 =================
BASE_URL = "https://nbw.sztu.edu.cn"
LIST_URL_TEMPLATE = "https://nbw.sztu.edu.cn/list.jsp?PAGENUM={page}&urltype=tree.TreeTempUrl&wbtreeid=1029"
START_PAGE = 1
END_PAGE = 50  # 抓取页数范围
CONCURRENCY_LIMIT = 5  # 🌟 降低并发到5，防止触发校园网防火墙(WAF)封禁
REQUEST_TIMEOUT = 20.0  # 延长超时时间

OUTPUT_FILE = Path(__file__).parent.parent / "data" / "gwt_time_analysis_1.csv"
TIME_PATTERN = re.compile(r"(\d{4}[-年/.]\d{1,2}[-月/.]\d{1,2}日?\s*\d{1,2}:\d{1,2})")


class GwtTimeAnalyzer:
    def __init__(self):
        self.semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)
        self.client: httpx.AsyncClient | None = None
        self.results: list[dict] = []
        self.processed_count = 0
        self.total_count = 0
        self.lock = asyncio.Lock()
        self.start_time = time.time()

    async def __aenter__(self):
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        self.client = httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT,
            headers=headers,
            verify=False,
            follow_redirects=True
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.client:
            await self.client.aclose()

    def _print_progress(self, increment: int = 1):
        self.processed_count += increment
        elapsed = time.time() - self.start_time
        rate = self.processed_count / elapsed if elapsed > 0 else 0
        print(f"\r已抓取 {self.processed_count}/{self.total_count} "
              f"| 速率: {rate:.1f} 篇/秒 | 耗时: {elapsed:.1f}s", end="", flush=True)

    async def fetch_list_page(self, page: int) -> list[dict]:
        url = LIST_URL_TEMPLATE.format(page=page)

        async with self.semaphore:
            try:
                assert self.client is not None
                response = await self.client.get(url)
                response.raise_for_status()

                # 🌟 使用 response.text 让 httpx 自动处理编码，比强制 utf-8 安全
                soup = BeautifulSoup(response.text, "html.parser")
                articles = soup.select('ul.news-ul li.clearfix')
                
                if not articles:
                    print(f"\n⚠️ 第 {page} 页未找到文章列表。网页状态码: {response.status_code}")
                    return []

                detail_items = []
                for article in articles:
                    try:
                        a_tag = article.select_one('.width04 a')
                        if not a_tag:
                            continue

                        # 🌟 完美解决 Pylance 警告：强制转换 href 格式
                        raw_href = a_tag.get('href')
                        if isinstance(raw_href, list): raw_href = raw_href[0]
                        href_str = str(raw_href).strip() if raw_href else ""
                        if not href_str: continue
                        full_url = urljoin(BASE_URL, href_str)

                        # 🌟 完美解决 Pylance 警告：安全获取 title
                        raw_title = a_tag.get('title')
                        if not raw_title: raw_title = a_tag.get_text(strip=True)
                        title_str = str(raw_title).strip()[:100] if raw_title else "无标题"

                        cat_tag = article.select_one('.width02')
                        dept_tag = article.select_one('.width03')
                        category = cat_tag.get_text(strip=True) if cat_tag else "未知"
                        department = dept_tag.get_text(strip=True) if dept_tag else "未知"

                        detail_items.append({
                            "title": title_str,
                            "url": full_url,
                            "category": category,
                            "department": department
                        })
                    except Exception as parse_err:
                        # 不再静默吞噬错误，而是打印出来查证
                        print(f"\n⚠️ 解析单篇文章出错跳过: {parse_err}")
                        continue

                # 增加 0.5 秒缓冲，防止触发 WAF 防御
                await asyncio.sleep(0.5)
                return detail_items

            except Exception as e:
                print(f"\n⚠️ 第 {page} 页网络请求失败: {e}")
                return []

    async def fetch_detail_page(self, item: dict) -> dict | None:
        url = item["url"]

        async with self.semaphore:
            try:
                assert self.client is not None
                response = await self.client.get(url)
                response.raise_for_status()

                soup = BeautifulSoup(response.text, "html.parser")
                full_text = soup.get_text()

                time_match = TIME_PATTERN.search(full_text)
                exact_time = time_match.group(1) if time_match else "未找到时间"

                attachment_types = set()
                for a in soup.find_all('a', href=True):
                    raw_href = a.get('href')
                    if isinstance(raw_href, list): raw_href = raw_href[0]
                    href_str = str(raw_href).lower().strip() if raw_href else ""
                    
                    if 'download' in href_str or re.search(r'\.(pdf|doc|docx|xls|xlsx|ppt|pptx|zip|rar|7z)$', href_str):
                        ext_match = re.search(r'\.([a-z0-9]+)$', href_str)
                        if ext_match:
                            attachment_types.add(ext_match.group(1))

                att_str = ",".join(attachment_types) if attachment_types else "无附件"

                item["exact_time"] = exact_time
                item["attachment_types"] = att_str

                async with self.lock:
                    self.results.append(item)
                    self._print_progress()
                    
                # 增加缓冲
                await asyncio.sleep(0.1)
                return item

            except Exception:
                # 详情页失败不阻断，只是打个标记
                async with self.lock:
                    self._print_progress()
                return None

    async def run(self):
        print("=" * 60)
        print("🔍 公文通历史数据清洗工具启动")
        print(f"⚡ 并发限制已调整为: {CONCURRENCY_LIMIT} (防封禁模式)")
        print("=" * 60)

        print("\n📍 第一步: 提取列表页元数据...")
        list_tasks = [self.fetch_list_page(page) for page in range(START_PAGE, END_PAGE + 1)]
        list_results = await asyncio.gather(*list_tasks)

        all_items = []
        for items in list_results:
            all_items.extend(items)

        self.total_count = len(all_items)
        print(f"\n✅ 共安全提取 {self.total_count} 个文章链接")

        if self.total_count == 0:
            print("❌ 数据为空，请检查网络或是否被暂时限制IP")
            return

        print(f"\n📍 第二步: 深入详情页挖掘精确时间...")
        self.start_time = time.time()
        detail_tasks = [self.fetch_detail_page(item) for item in all_items]
        await asyncio.gather(*detail_tasks)

        print(f"\n\n💾 数据落地中...")
        OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = ["title", "url", "category", "department", "attachment_types", "exact_time"]
        with open(OUTPUT_FILE, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(self.results)
        
        print(f"✅ 大功告成！已成功提取并写入 {len(self.results)} 篇历史记录，请查看 data 文件夹。")


async def main():
    async with GwtTimeAnalyzer() as analyzer:
        await analyzer.run()

if __name__ == "__main__":
    asyncio.run(main())