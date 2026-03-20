import logging
import json
from src.spiders.nmne_spider import NmneSpider
from src.spiders.future_tech_spider import FutureTechSpider
from src.spiders.icoc_spider import IcocSpider

# 开启 DEBUG 日志，让我们能看到底层的翻页推演过程
logging.basicConfig(level=logging.DEBUG, format='%(levelname)s: %(message)s')

def run_test():
    # 挑选了 3 个最具代表性的“硬骨头”学院
    test_cases = [
        (NmneSpider(), "通知公告"),     # 测试：常规逆向分页 + v_news_content
        (FutureTechSpider(), "新闻中心"), # 测试：动态解析 + vsb_content
        (IcocSpider(), "学术成果")        # 测试：特殊 DOM 列表 + content_m
    ]

    for spider_class, section in test_cases:
        print(f"\n{'='*50}")
        print(f"🕵️ 开始测试: 【{spider_class.SOURCE_NAME} - {section}】")
        print(f"{'='*50}")
        
        with spider_class as spider:
            # 1. 测试列表页抓取（只抓第一页，但会触发翻页推演）
            print("⏳ 正在抓取列表页并推演翻页规律...")
            articles = spider.fetch_list(page_num=1, section_name=section)
            
            if not articles:
                print("❌ 列表抓取失败，未找到任何文章。")
                continue
                
            print(f"✅ 成功抓取到 {len(articles)} 条列表项！")
            first_article = articles[0]
            print(f"📄 第一篇文章: {first_article['title']} (日期: {first_article.get('date', '无')})")
            print(f"🔗 链接: {first_article['url']}")
            
            # 2. 测试详情页正文与附件抓取
            print("\n⏳ 正在潜入详情页抓取正文与附件...")
            detail = spider.fetch_detail(first_article['url'])
            
            if not detail:
                print("❌ 详情页抓取失败。")
                continue
                
            print(f"✅ 详情页抓取成功！")
            print(f"⏱️ 精确时间: {detail.get('exact_time', '未找到')}")
            print(f"📝 正文预览: {detail.get('body_text', '空')[:80]}...")
            
            attachments = detail.get('attachments', [])
            if attachments:
                print(f"📎 发现 {len(attachments)} 个附件:")
                for att in attachments:
                    print(f"   - {att['name']} ({att['url']})")
            else:
                print("📎 未发现附件。")

if __name__ == "__main__":
    run_test()