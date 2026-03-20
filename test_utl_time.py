import requests
import re
from bs4 import BeautifulSoup

def test_time_extraction(url: str):
    print(f"\n🔗 正在请求测试文章: {url}")
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    }
    
    try:
        res = requests.get(url, headers=headers, timeout=10)
        res.encoding = 'utf-8'
        soup = BeautifulSoup(res.text, 'html.parser')
        
        exact_time = ""
        
        print("🕵️ 开始执行增强版时间防御体系侦察...")
        
        info_selectors = [
            '.v_news_info', '.content_t', '.cnt_note', '.info', 
            '.article-meta', '.news_info', '.time', '.article-time', 
            '.source', '.detail_message', '.message_right'
        ]
        
        # 🌟 修复点：用最稳健的 for 循环，找到就 break，绝不会被 None 提前截断！
        info_bar = None
        for selector in info_selectors:
            info_bar = soup.select_one(selector)
            if info_bar:
                print(f"   🎯 [底层追踪] 成功命中选择器: {selector}")
                break
        
        if info_bar:
            raw_text = info_bar.get_text(strip=True)
            print(f"   🔎 [第二级追踪] 成功锁定信息栏，包含文本: {raw_text[:40]}...")
            
            match = re.search(r'(\d{4}[-年/]\d{1,2}[-月/]\d{1,2}日?(?:\s*\d{1,2}:\d{1,2}(?::\d{1,2})?)?)', raw_text)
            if match: 
                exact_time = match.group(1)
                # 标准化时间格式
                exact_time = exact_time.replace('年', '-').replace('月', '-').replace('日', '').replace('/', '-')
                print(f"   ✅ [提取成功] 精确时间为: {exact_time}\n")
            else:
                print("   ⚠️ 未能正则匹配出时间。")
        else:
            print("   ❌ 失败：依然没有找到已知的信息栏容器。")

    except Exception as e:
        print(f"   ❌ 发生异常: {e}")

if __name__ == "__main__":
    test_url = "https://utl.sztu.edu.cn/info/1016/2919.htm"
    test_time_extraction(test_url)