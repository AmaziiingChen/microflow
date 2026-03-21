from scrapegraphai.graphs import ScriptCreatorGraph

# 你的 DeepSeek 引擎配置保持不变
graph_config = {
    "llm": {
        "api_key": "sk-b80d6b2f4edc428e9455dd55abd874f6",  
        "model": "openai/deepseek-chat",     
        "base_url": "https://api.deepseek.com/v1",
    },
    "verbose": True,
    "headless": True, 
    "library": "beautifulsoup", # 👈 就是少了这极其关键的一行！
}

# 这一次，我们使用的是 ScriptCreatorGraph
script_creator = ScriptCreatorGraph(
    prompt="""
    你是一个资深的 Python 爬虫工程师。请帮我写一段使用 `requests` 和 `BeautifulSoup` 的代码。
    目标是解析当前这个高校公文详情网页。
    
    代码需要精准实现两个核心提取功能：
    1. 提取正文：找到包含文章正文的核心 DOM 容器（通常是带有特定 class 的 div），提取里面的所有纯文本内容。
    2. 【极其重要】提取附件：在正文容器内部或尾部，寻找所有带有 href 属性的 <a> 标签。如果该链接的文本包含“附件”、“下载”或者后缀名（如 .doc, .pdf, .xls, .zip 等），请将其识别为附件。
    
    请在代码中打印出：正文的前 100 个字符，以及一个包含【附件名称】和【完整真实下载链接】的字典。如果是相对路径，请用 urljoin 拼接完整。
    """,
    source="https://ai.sztu.edu.cn/xwzx/tzgg1/qb.htm", 
    config=graph_config
)

# 运行并获取生成的代码
result = script_creator.run()

# 打印 AI 给你写的爬虫代码
print("================ AI 生成的代码 ===================")
print(result)