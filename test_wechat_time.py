import requests
import re
from datetime import datetime
from bs4 import BeautifulSoup

def test_wechat_time_extraction(url: str):
    print(f"\n🔗 正在请求微信文章: {url}")
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    try:
        res = requests.get(url, headers=headers, timeout=10)
        res.encoding = 'utf-8'
        html_content = res.text
        
        exact_time = ""
        
        print("🕵️ 开始时间提取侦察...")
        
        # 🌟 策略 1：狙击底层 JS 变量 var ct = "1689048000";
        ct_match = re.search(r'var\s+ct\s*=\s*"(\d+)";', html_content)
        if ct_match:
            timestamp = int(ct_match.group(1))
            # 将 Unix 时间戳转化为直观的 YYYY-MM-DD HH:MM:SS
            exact_time = datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')
            print(f"   ✅ [策略 1 命中] 成功穿透 JS 抓取 Unix 时间戳！")
            print(f"   ⏱️ 提取结果: {exact_time}")
            return
            
        # 🌟 策略 2：降级狙击 var publish_time = "2023-07-11"
        pub_match = re.search(r'var\s+publish_time\s*=\s*"([^"]+)"', html_content)
        if pub_match:
            exact_time = pub_match.group(1)
            print(f"   ✅ [策略 2 命中] 成功匹配 JS 字符串时间！")
            print(f"   ⏱️ 提取结果: {exact_time}")
            return
            
        # 🌟 策略 3：传统 HTML 标签兜底（大概率为空）
        soup = BeautifulSoup(html_content, 'html.parser')
        time_tag = soup.find('em', id='publish_time')
        if time_tag and time_tag.get_text(strip=True):
            exact_time = time_tag.get_text(strip=True)
            print(f"   ✅ [策略 3 命中] 从传统 HTML 标签提取成功！")
            print(f"   ⏱️ 提取结果: {exact_time}")
            return
            
        print("   ❌ 所有时间提取策略均失败！请检查微信是否更改了底层架构。")
        
    except Exception as e:
        print(f"   ❌ 请求或解析发生异常: {e}")

if __name__ == "__main__":
    # 使用你之前测试日志里未来技术学院的那篇微信文章进行靶向测试
    test_urls = [
        "https://mp.weixin.qq.com/s/JgXu6R5Oud1NUxJNYhw5KA",
        # 你可以再随便找一篇别的微信公众号文章链接贴在这里测试
    ]
    
    for u in test_urls:
        test_wechat_time_extraction(u)