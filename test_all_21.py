import logging
import time
from bs4 import BeautifulSoup

# --- 1. 配置工业级双路日志系统 ---
logger = logging.getLogger('MicroFlow_Test')
logger.setLevel(logging.INFO)

if logger.hasHandlers():
    logger.handlers.clear()

file_handler = logging.FileHandler('spider_test_report.log', mode='w', encoding='utf-8')
file_handler.setLevel(logging.INFO)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)

formatter = logging.Formatter('%(message)s')
file_handler.setFormatter(formatter)
console_handler.setFormatter(formatter)

logger.addHandler(file_handler)
logger.addHandler(console_handler)

# --- 2. 导入爬虫 ---
try:
    from src.spiders.business_spider import BusinessSpider
    from src.spiders.nmne_spider import NmneSpider
    from src.spiders.ai_spider import AiSpider
    from src.spiders.cep_spider import CepSpider
    from src.spiders.cop_spider import CopSpider
    from src.spiders.design_spider import DesignSpider
    from src.spiders.future_tech_spider import FutureTechSpider
    from src.spiders.hsee_spider import HseeSpider
    from src.spiders.icoc_spider import IcocSpider
    from src.spiders.sfl_spider import SflSpider
    from src.spiders.sgim_spider import SgimSpider
    from src.spiders.utl_spider import UtlSpider
except ImportError as e:
    logger.error(f"导入爬虫失败，请检查路径: {e}")

def run_comprehensive_test():
    spiders = [
        HseeSpider(),

    ]
    # 如果有 AiSpider，可以加进去: AiSpider()

    logger.info("============================================================")
    logger.info("   MicroFlow 爬虫 V3 架构 - 全量压测任务 (包含跨页、日期与溯源)")
    logger.info("   完整诊断报告将保存在: spider_test_report.log")
    logger.info("============================================================\n")

    for spider in spiders:
        logger.info(f"\n{'='*65}")
        logger.info(f"🏢 正在全面诊断: 【{spider.SOURCE_NAME}】")
        logger.info(f"{'='*65}")

        sections = getattr(spider, 'SECTIONS', getattr(spider, 'sections', {}))
        
        for section_name, entry_path in sections.items():
            
            if entry_path.startswith('http'):
                entry_url = entry_path
            else:
                entry_url = spider.safe_urljoin(spider.BASE_URL, entry_path)
                
            logger.info(f"\n  📂 探勘板块: {section_name}")
            logger.info(f"  🌐 板块链接: {entry_url}")

            with spider:
                # 1. 正常推演翻页规律
                all_page_urls = spider.get_all_page_urls(entry_url)
                logger.info(f"     ✅ 成功解析翻页规律，该板块实际共有 {len(all_page_urls)} 页。")
                
                # 打印出前两页的具体 URL，证明我们确实跨页了
                if len(all_page_urls) >= 1:
                    logger.info(f"     👉 第 1 页 URL: {all_page_urls[0]}")
                if len(all_page_urls) >= 2:
                    logger.info(f"     👉 第 2 页 URL: {all_page_urls[1]}")

                # 🌟 核心魔术：动态方法拦截 (Monkey Patching)
                # 强制让爬虫以为这个板块只有前 2 页，防止它去请求几十页导致 IP 被封
                original_get_urls = spider.get_all_page_urls
                spider.get_all_page_urls = lambda url: original_get_urls(url)[:2]

                try:
                    logger.info("     🔄 启动列表提取引擎，严格控制爬取 第1页 和 第2页...")
                    # 此时 fetch_list 调用被我们劫持的 get_all_page_urls，只会拿到前两页的链接
                    articles = spider.fetch_list(page_num=1, section_name=section_name)
                    
                    # 恢复爬虫的原始方法，避免影响下一个循环
                    spider.get_all_page_urls = original_get_urls
                    
                    collected_articles = articles[:21] # 无论两页有多少条，只取前 21 条
                except Exception as e:
                    logger.info(f"     ❌ 抓取失败: {e}")
                    spider.get_all_page_urls = original_get_urls # 发生异常也要恢复
                    continue
                
                count = len(collected_articles)
                logger.info(f"     🎯 跨页采集完毕，共提取前 {count} 条内容。")
                
                if count == 0:
                    logger.info("     ⚠️ 该板块为空，无任何数据。")
                    continue

                # 3. 详情页深度解析测试
                logger.info("     🔬 正在解析具体内容：")
                for i, item in enumerate(collected_articles, 1):
                    title = item.get('title', '无标题')
                    url = item.get('url', '')
                    date_str = item.get('date', '无日期') # 获取列表上的新闻日期
                    
                    detail = spider.fetch_detail(url)
                    
                    body_preview = "获取失败"
                    attachments_info = "无附件"
                    
                    if detail:
                        # 🌟 1. 提取底层精确时间
                        exact_time = detail.get('exact_time') or "未提供精确到分秒的时间"
                        
                        body = detail.get('body_text', '')
                        body_preview = body[:35].replace('\n', ' ') + "..." if body else "正文为空(可能为纯图片或容器异常)"
                        
                        atts = detail.get('attachments', [])
                        if atts:
                            attachments_info = f"发现 {len(atts)} 个附件 -> [{atts[0]['name']}]"
                    
                    # 极其工整的溯源排版
                    logger.info(f"       {i:02d}. {title}")
                    logger.info(f"           📅 列表日期: {date_str}")
                    logger.info(f"           ⏱️ 精确时间: {exact_time}")  # 👈 新增：精准到秒的底层时间
                    logger.info(f"           🔗 链接: {url}")
                    logger.info(f"           📝 正文: {body_preview}")
                    
                    # 🌟 2. 完整遍历并打印所有附件的绝对链接
                    if detail and detail.get('attachments'):
                        atts = detail['attachments']
                        logger.info(f"           📎 发现 {len(atts)} 个附件:")
                        for idx, att in enumerate(atts, 1):
                            logger.info(f"              [附件 {idx}] {att['name']}")
                            logger.info(f"              👉 下载: {att['url']}")
                    else:
                        logger.info(f"           📎 附件: 无")
                    
            time.sleep(1) # 板块间礼貌暂停

    logger.info("\n🎉 压测任务全部结束！请使用文本编辑器打开 spider_test_report.log 查看全量报告。")

if __name__ == "__main__":
    run_comprehensive_test()