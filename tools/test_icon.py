import os
import requests
import base64
import urllib3

# 禁用安全警告（针对某些实验室网络环境）
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def test_download_and_process_icon(model_name: str):
    print(f"🚀 开始测试模型: {model_name}")
    
    # 1. 模拟品牌映射逻辑
    brand = model_name.split('-')[0].lower()
    brand_map = {
        'gpt': 'openai', 
        'claude': 'anthropic', 
        'gemini': 'google', 
        'mimo': 'xiaomi',
        'qwen': 'qwen',
        'deepseek': 'deepseek'
    }
    slug = brand_map.get(brand, brand)
    
    # 2. 确定存储路径 (模拟存储在 data/icons)
    # 获取当前脚本所在目录下的 data/icons
    current_dir = os.path.dirname(os.path.abspath(__file__))
    icon_dir = os.path.join(current_dir, 'data', 'icons')
    if not os.path.exists(icon_dir):
        os.makedirs(icon_dir)
        print(f"📁 已创建文件夹: {icon_dir}")

    icon_path = os.path.join(icon_dir, f"icon_{slug}.svg")
    url = f"https://unpkg.com/@lobehub/icons-static-svg@latest/icons/{slug}-color.svg"

    try:
        # 3. 执行抓取
        print(f"🌐 正在从 CDN 抓取: {url}")
        resp = requests.get(url, timeout=10, verify=False)
        
        if resp.status_code == 200:
            svg_data = resp.text
            print("✅ 抓取成功！正在处理源码...")

            # 4. 🌟 核心逻辑：检查并补全尺寸，确保截图不消失
            if 'width=' not in svg_data:
                # 强行在 <svg 标签里注入宽和高
                svg_data = svg_data.replace('<svg', '<svg width="100%" height="100%"')
                print("🔧 已检测到尺寸缺失，完成自动补全")

            # 5. 保存到本地
            with open(icon_path, 'w', encoding='utf-8') as f:
                f.write(svg_data)
            print(f"💾 文件已保存至: {icon_path}")

            # 6. 模拟前端需要的 Base64 输出
            b64 = base64.b64encode(svg_data.encode('utf-8')).decode('utf-8')
            print(f"💎 Base64 转换完成 (长度: {len(b64)})")
            print("\n🎉 测试成功！你可以去文件夹里查看这个 SVG 了。")
            
        else:
            print(f"❌ 抓取失败，HTTP 状态码: {resp.status_code}")
            
    except Exception as e:
        print(f"💥 发生错误: {str(e)}")

if __name__ == "__main__":
    # 你可以修改这里的名称来测试不同的模型图标
    test_download_and_process_icon("Xiaomi MiMo")
    # test_download_and_process_icon("gpt-4o")
    # test_download_and_process_icon("mimo-v1")