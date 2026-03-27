import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import json


def main():
    base_url = "https://musicyyds.sztu.edu.cn/zxdt/fmxw.htm"

    try:
        response = requests.get(base_url, timeout=10)
        response.raise_for_status()
        response.encoding = "utf-8"

        soup = BeautifulSoup(response.text, "html.parser")

        news_list = []

        # 查找新闻列表容器
        content_list = soup.find("div", class_="content-list")
        if content_list:
            items = content_list.find_all("div", class_="item")

            for item in items:
                news_item = {}

                # 提取标题
                title_elem = item.find("div", class_="title")
                if title_elem:
                    news_item["title"] = title_elem.text.strip()

                # 提取详情链接
                link_elem = item.find("a")
                if link_elem and "href" in link_elem.attrs:
                    href = link_elem["href"]
                    # 拼接完整URL
                    full_url = urljoin(base_url, href)
                    news_item["url"] = full_url

                # 提取发布时间
                date_elem = item.find("div", class_="date")
                if date_elem:
                    news_item["date"] = date_elem.text.strip()

                if news_item:
                    news_list.append(news_item)

        # 查找下一页链接
        next_page_url = None
        pagination = soup.find("div", class_="pb_sys_normal")
        if pagination:
            # 查找包含"下页"或"下一页"的链接
            next_link = pagination.find(
                "a", string=lambda text: text and ("下页" in text or "下一页" in text)
            )
            if next_link and "href" in next_link.attrs:
                href = next_link["href"]
                next_page_url = urljoin(base_url, href)
                print(f"下一页的真实完整URL: {next_page_url}")
            else:
                print("未找到下一页链接")

        # 构建JSON结果
        result = {"news_list": news_list, "next_page_url": next_page_url}

        print(json.dumps(result, ensure_ascii=False, indent=2))

    except requests.RequestException as e:
        print(f"请求失败: {e}")
    except Exception as e:
        print(f"解析失败: {e}")


if __name__ == "__main__":
    main()
