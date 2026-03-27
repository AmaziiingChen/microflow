import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import json
import re  # 必须加上这行


def main():
    url = "https://musicyyds.sztu.edu.cn/zxdt/fmxw.htm"

    try:
        # 【手术一：增加伪装头】加上 User-Agent 和 Referer，防止某些附件接口校验来源拦截你
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": url,
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()

        # 【极其重要的小细节】强制指定编码，防止后续的 HTML 乱码导致正则匹配失败
        response.encoding = "utf-8"

    except requests.RequestException as e:
        print(json.dumps({"error": str(e)}))
        return

    soup = BeautifulSoup(response.text, "html.parser")

    # --- 正文提取模块（保留你的严谨逻辑） ---
    core_container = soup.find("div", class_="v_news_content")
    if not core_container:
        core_container = soup.find("div", class_="news_conent_two_text")

    if not core_container:
        print(
            json.dumps({"error": "核心内容容器未找到", "url": url}, ensure_ascii=False)
        )
        return

    result = {}

    paragraphs = core_container.find_all("p")
    text_content = []
    for p in paragraphs:
        text = p.get_text(strip=True)
        if text:
            text_content.append(text)

    # 【兜底优化】如果 p 标签没抓到东西，直接暴力抓取容器内的所有纯文本
    if not text_content:
        text_content = [core_container.get_text(strip=True)]

    result["正文文本"] = text_content

    # --- 附件提取模块（终极火力升级版） ---
    # --- 附件提取模块（防污染版：相对 DOM 遍历） ---
    attachments = []

    # 【核心修正】：向外跳级，寻找真正包裹“文章+附件”的大框架，彻底隔绝导航栏
    # 特征 1：很多 Webplus 系统的文章及附件，都会被统一包裹在一个 name 为特定的 form 表单里
    article_wrapper = soup.find("form", attrs={"name": "_newscontent_fromname"})

    if not article_wrapper:
        # 特征 2：如果没有表单，我们就从正文框向外跳 2 级。
        # 第一级通常包裹文字，第二级通常包含了文章头部、文字、和底部的附件列表。
        article_wrapper = core_container.parent
        if article_wrapper and article_wrapper.parent:
            article_wrapper = article_wrapper.parent

    # 现在，我们只在这个“干净的无菌室”里寻找附件！
    search_scope = article_wrapper if article_wrapper else core_container
    attach_tags = search_scope.find_all("a")

    for a in attach_tags:
        href = a.get("href", "")
        text = a.get_text(strip=True)

        if not href or href.startswith("javascript:"):
            continue

        is_system_attach = "DownloadAttachUrl" in href or "download.jsp" in href
        is_keyword_attach = any(
            keyword in text.lower() for keyword in ["附件", "下载", "点击查看"]
        ) or href.lower().endswith(
            (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".rar")
        )

        if is_system_attach or is_keyword_attach:
            full_url = urljoin(url, href)

            if not any(att["附件链接"] == full_url for att in attachments):
                file_name = text if text else a.get("title", "未命名附件")

                attachments.append({"附件名称": file_name, "附件链接": full_url})

    result["附件信息"] = attachments

    # --- 发文精确时间提取模块（底层 Meta 标签 + 降维打击） ---
    publish_time = "未找到精确时间"

    # 首选方案（狙击）：直接深入网页大脑 <head> 抓取系统记录的精确到秒的发布时间
    meta_pub_date = soup.find("meta", attrs={"name": "PubDate"})

    if meta_pub_date and meta_pub_date.get("content"):
        # 提取出的内容通常非常规整，例如："2026-03-20 14:30:00"
        publish_time = meta_pub_date["content"]
    else:
        # 兜底方案（扫射）：如果遇到几年前极其老旧的页面，没有生成 Meta 标签，退回使用正则盲狙
        if article_wrapper:
            header_text = article_wrapper.get_text(separator=" ", strip=True)[:500]
            # 升级版正则：尝试连同 HH:mm:ss 一起抓取下来（如果页面上显示了的话）
            datetime_match = re.search(
                r"(20\d{2}[-/年]\d{1,2}[-/月]\d{1,2}(?:\s+\d{1,2}:\d{1,2}(?::\d{1,2})?)?)",
                header_text,
            )

            if datetime_match:
                raw_date = datetime_match.group(1)
                publish_time = (
                    raw_date.replace("年", "-").replace("月", "-").replace("/", "-")
                )
                if publish_time.endswith("日"):
                    publish_time = publish_time[:-1]

    result["发布时间"] = publish_time

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
