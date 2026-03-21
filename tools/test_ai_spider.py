import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import json

def main():
    url = "https://ai.sztu.edu.cn/xwzx/tzgg1/qb.htm"
    
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        response.encoding = 'utf-8'
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        content_div = soup.find('div', class_='main_cont')
        if not content_div:
            content_div = soup.find('div', class_='page_main')
        
        if content_div:
            text_content = content_div.get_text(strip=True, separator=' ')
            print("正文前100字符:", text_content[:10000])
            
            attachments = []
            for a_tag in content_div.find_all('a', href=True):
                link_text = a_tag.get_text(strip=True)
                href = a_tag['href']
                
                is_attachment = False
                attachment_keywords = ['附件', '下载']
                attachment_extensions = ['.doc', '.pdf', '.xls', '.zip', '.rar', '.docx', '.xlsx', '.ppt', '.pptx']
                
                if any(keyword in link_text for keyword in attachment_keywords):
                    is_attachment = True
                elif any(href.lower().endswith(ext) for ext in attachment_extensions):
                    is_attachment = True
                
                if is_attachment:
                    full_url = urljoin(url, href)
                    attachments.append({
                        "附件名称": link_text if link_text else href.split('/')[-1],
                        "完整真实下载链接": full_url
                    })
            
            result = {
                "正文摘要": text_content[:100],
                "附件列表": attachments
            }
            
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(json.dumps({"错误": "未找到正文容器"}, ensure_ascii=False))
            
    except requests.RequestException as e:
        print(json.dumps({"错误": f"请求失败: {str(e)}"}, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"错误": f"解析失败: {str(e)}"}, ensure_ascii=False))

if __name__ == "__main__":
    main()