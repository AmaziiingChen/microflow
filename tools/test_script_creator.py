import os
from scrapegraphai.graphs import ScriptCreatorGraph


def build_graph_config():
    api_key = str(os.getenv("DEEPSEEK_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("缺少环境变量 DEEPSEEK_API_KEY，无法运行 ScriptCreatorGraph")

    return {
        "llm": {
            "api_key": api_key,
            "model": "openai/deepseek-chat",
            "base_url": "https://api.deepseek.com/v1",
        },
        "verbose": True,
        "headless": True,
        "library": "beautifulsoup",
    }


def main():
    script_creator = ScriptCreatorGraph(
        prompt="""
        你是一个资深的 Python 爬虫工程师。请帮我写一段使用 `requests` 和 `BeautifulSoup` 的代码。
        目标是解析当前这个高校公文详情网页。

        代码需要精准实现两个核心提取功能：
        1. 提取正文：找到包含文章正文的核心 DOM 容器，提取里面的所有纯文本内容。
        2. 提取附件：在正文容器内部或尾部，寻找所有带有 href 属性的 <a> 标签。
           如果链接文本包含“附件”“下载”或常见附件后缀，请将其识别为附件。

        请在代码中打印出：正文的前 100 个字符，以及一个包含附件名称与完整下载链接的字典。
        如果是相对路径，请用 urljoin 拼接完整。
        """,
        source="https://ai.sztu.edu.cn/xwzx/tzgg1/qb.htm",
        config=build_graph_config(),
    )

    result = script_creator.run()
    print("================ AI 生成的代码 ===================")
    print(result)


if __name__ == "__main__":
    main()
