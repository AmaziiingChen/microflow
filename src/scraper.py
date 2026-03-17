import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import logging
import re # 确保顶部引入了 re 模块
import json # 👈 确保顶部引入 json
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
# 配置日志记录器。后续在后台静默运行时，如果校园网断了，我们可以通过日志排查问题
logger = logging.getLogger(__name__)

class TongWenScraper:
    """
    数据采集层：专门负责与深圳技术大学公文通网站的通信与页面解析。
    绝对不包含任何数据库操作或 AI 调用的业务逻辑，保持纯粹的“抓取”职责。
    """
    def __init__(self):
        self.base_url = "https://nbw.sztu.edu.cn/"
        self.list_url = urljoin(self.base_url, "list.jsp?urltype=tree.TreeTempUrl&wbtreeid=1029")
        
        # 1. 🌟 第一步：必须先在这里把 session 对象创建出来！
        self.session = requests.Session()
        
        # 2. 第二步：创建好 session 之后，再去给它挂载重试策略
        retry_strategy = Retry(
            total=3,  
            backoff_factor=1,  
            status_forcelist=[429, 500, 502, 503, 504]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

        # 3. 第三步：最后再去更新请求头
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
        })

    def fetch_notice_list(self, limit=50):
        """
        抓取公文列表页（已支持突破单页限制的自动翻页功能）
        :param limit: 每次最多抓取的前 N 条记录。由于增加了追更机制，默认放大至 50 条。
        :return: 包含字典的列表 [{"title": ..., "url": ..., "date": ..., "category": ..., "department": ...}]
        """
        notices = []
        page = 1
        
        # 只要收集到的公文数量还没达到上限，就继续翻页
        while len(notices) < limit:
            # 根据你提供的真实 URL 规律，动态构造带 PAGENUM 的分页地址
            current_url = f"https://nbw.sztu.edu.cn/list.jsp?PAGENUM={page}&urltype=tree.TreeTempUrl&wbtreeid=1029"
            logger.info(f"正在抓取公文列表 (第 {page} 页): {current_url}")
            
            try:
                # 设置 10 秒超时，防止校园网卡顿导致后台线程永远阻塞
                res = self.session.get(current_url, timeout=10)
                res.encoding = 'utf-8' 
                
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(res.text, 'html.parser')
                
                # 定位公文列表的父级 <ul> 以及下属的每一个 <li>
                articles_tags = soup.select('ul.news-ul li.clearfix')
                
                # 🌟 安全网：如果这一页没有任何列表项，说明已经翻到了公文通的最底端，强制结束抓取
                if not articles_tags:
                    logger.info("已到达列表最后一页，没有更多数据。")
                    break
                
                for article in articles_tags:
                    # 根据你提供的 HTML 框架进行精准元素定位
                    title_tag = article.select_one('div.width04 a')
                    # 增加存在性检查
                    if title_tag is None:
                        continue  # 如果这行没找到 a 标签，直接跳过本行，防止后续 get 崩溃
                        
                    date_tag = article.select_one('div.width06')
                    category_tag = article.select_one('div.width02 a')
                    department_tag = article.select_one('div.width03 a')
                    
                    # 提取标题并防御性转换
                    title = title_tag.get('title', '')
                    if not isinstance(title, str):
                        title = str(title)
                    title = title.strip()
                    
                    href = title_tag.get('href')
                    
                    # 防御性编程：消除 Pylance 警告，确保 href 是有效的字符串
                    if not isinstance(href, str):
                        continue
                        
                    # urljoin 是处理路径拼接的“神器”，它能智能处理相对路径和绝对路径
                    full_url = urljoin(self.base_url, href)
                    
                    # 提取其他维度的数据，增加数据丰度
                    date_str = date_tag.text.strip() if date_tag else "未知时间"
                    category = category_tag.text.strip() if category_tag else "未知类别"
                    department = department_tag.text.strip() if department_tag else "未知单位"
                    
                    notices.append({
                        "title": title,
                        "url": full_url,
                        "date": date_str,
                        "category": category,
                        "department": department
                    })
                    
                    # 🌟 核心刹车逻辑：如果当前收集到的数量已经满足了 limit 限制，立刻跳出 for 循环
                    if len(notices) >= limit:
                        break
                        
            except requests.exceptions.RequestException as e:
                logger.error(f"网络请求失败，请检查是否连接校园网 (第 {page} 页): {e}")
                break # 网络断开时，直接跳出 while 循环，返回已抓取的部分
            except Exception as e:
                logger.error(f"解析公文列表时发生未知错误 (第 {page} 页): {e}")
                break
                
            # 本页解析完毕，翻页准备抓取下一页
            page += 1
            
        return notices
    def fetch_article_content(self, url):
        """抓取详情页，并提取正文、附件状态和精确时间"""
        logger.info(f"正在抓取公文正文: {url}")
        try:
            res = self.session.get(url, timeout=10)
            res.encoding = 'utf-8'
            soup = BeautifulSoup(res.text, 'html.parser')
            
            # 1. 提取正文
            content_div = soup.find('div', class_='v_news_content')
            
            if content_div:
                raw_text = content_div.get_text(strip=True, separator='\n')
            elif soup.body:
                # 增加了 soup.body 的存活判定，彻底消除 Pylance 警告
                raw_text = soup.body.get_text(strip=True, separator='\n')[:3000]
            else:
                # 兜底机制：如果连 body 都没有，返回空字符串
                raw_text = ""

            # 2. 智能提取附件列表
            attachments = []
            # 寻找所有的 a 标签
            for a_tag in soup.find_all('a', href=True):
                href = a_tag.get('href')
                
                # 安全校验
                if not isinstance(href, str):
                    continue
                    
                text = a_tag.get_text(strip=True) or "未命名附件"
                
                # 👇 核心修复：扩展白名单，加入了你截图中出现的专属动态接口词汇
                valid_keywords = [
                    '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', 
                    '.rar', '.zip', 'clickdown', 'download.jsp', 'downloadattachurl'
                ]
                
                if any(kw in href.lower() for kw in valid_keywords):
                    full_url = urljoin(url, href)
                    attachments.append({"name": text, "url": full_url})
            
            # 3. 智能提取精确时间
            exact_time = ""
            full_text = soup.get_text()
            # 匹配 2026-03-13 15:18 或 2026年03月13日 15:18 等格式
            time_match = re.search(r"(\d{4}[-年/.]\d{1,2}[-月/.]\d{1,2}日?\s*\d{1,2}:\d{1,2})", full_text)
            if time_match:
                exact_time = time_match.group(1).replace('  ', ' ') # 清理多余空格
            
            return {
                "raw_text": raw_text,
                "attachments": json.dumps(attachments, ensure_ascii=False), # 👈 将附件列表转为 JSON 字符串返回
                "exact_time": exact_time
            }

        except Exception as e:
            logger.error(f"解析正文时发生错误: {url} -> {e}")
            return None
        

# # --- 以下是测试代码，直接加在文件最底部 ---
# if __name__ == "__main__":
#     # 1. 临时配置日志，让我们可以看到抓取过程
#     logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    
#     # 2. 实例化爬虫
#     scraper = TongWenScraper()
    
#     # 3. 尝试抓取列表
#     print("\n--- 正在尝试抓取最新的公文列表 ---")
#     results = scraper.fetch_notice_list(limit=5)
    
#     if results:
#         for idx, item in enumerate(results, 1):
#             print(f"{idx}. 【{item['category']}】{item['title']} ({item['date']})")
#             print(f"   链接: {item['url']}")
            
#             # 4. 随机抓取第一条的正文试试看（10000字模式）
#             if idx == 1:
#                 print(f"\n--- 正在尝试抓取第一条的正文 (限10000字) ---")
#                 content = scraper.fetch_article_content(item['url'])
#                 if content:
#                     print(f"正文前200字预览:\n{content[:200]}...")
#                 else:
#                     print("正文抓取失败。")
#     else:
#         print("未抓取到任何内容，请检查是否连接校园网或官网是否改版。")