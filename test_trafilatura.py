import requests
from bs4 import BeautifulSoup

# 把你那几个翻车学院的网址贴进来
urls = {
      # 人工智能学院
#   "人工智能学院-院系新闻": "https://ai.sztu.edu.cn/xwzx/yxxw1.htm",                                                                                                              
#   "人工智能学院-通知公告": "https://ai.sztu.edu.cn/xwzx/tzgg1/qb.htm",

#   # 中德智能制造学院
#   "中德智能制造学院-学院新闻": "https://sgim.sztu.edu.cn/xyxw.htm",
"中德智能制造学院-通知公告": "https://sgim.sztu.edu.cn/list2022.jsp?urltype=tree.TreeTempUrl&wbtreeid=1045",
#   # 新材料与新能源学院
#   "新材料与新能源学院-学院动态": "https://nmne.sztu.edu.cn/xwzx/xydt.htm",
#   "新材料与新能源学院-通知公告": "https://nmne.sztu.edu.cn/xwzx/tzgg.htm",
#   "新材料与新能源学院-讲座通知": "https://nmne.sztu.edu.cn/xwzx/jzt.htm",
#   "新材料与新能源学院-学术动态": "https://nmne.sztu.edu.cn/xwzx/xsd.htm",
#   "新材料与新能源学院-合作交流": "https://nmne.sztu.edu.cn/xwzx/hzj.htm",
#   "新材料与新能源学院-实验平台": "https://nmne.sztu.edu.cn/xwzx/sypt.htm",

#   # 城市交通与物流学院
#   "城市交通与物流学院-学院动态": "https://utl.sztu.edu.cn/xwzx/xydt.htm",
#   "城市交通与物流学院-通知公告": "https://utl.sztu.edu.cn/xwzx/tzgg.htm",

#   # 健康与环境工程学院
#   "健康与环境工程学院-学院动态": "https://hsee.sztu.edu.cn/xydt.htm",
#   "健康与环境工程学院-通知公告": "https://hsee.sztu.edu.cn/tzgg.htm",

#   # 工程物理学院
#   "工程物理学院-新闻动态": "https://cep.sztu.edu.cn/tzgg1/xwdt.htm",
#   "工程物理学院-通知公告": "https://cep.sztu.edu.cn/tzgg1/tzg.htm",

#   # 药学院
#   "药学院-学院新闻": "https://cop.sztu.edu.cn/index/xyxw.htm",
#   "药学院-通知公告": "https://cop.sztu.edu.cn/index/tzgg.htm",

#   # 商学院
#   "商学院-新闻动态": "https://bs.sztu.edu.cn/index/xwdt.htm",
#   "商学院-通知公告": "https://bs.sztu.edu.cn/index/tzgg.htm",
#   "商学院-学术动态": "https://bs.sztu.edu.cn/index/xsdt.htm",
#   "商学院-校园生活": "https://bs.sztu.edu.cn/index/xysh.htm",

#   # 外国语学院
#   "外国语学院-通知公告": "https://sfl.sztu.edu.cn/tzgg.htm",
#   "外国语学院-学院新闻": "https://sfl.sztu.edu.cn/xyxw.htm",

#   # 未来技术学院
#   "未来技术学院-新闻中心": "https://futuretechnologyschool.sztu.edu.cn/xw_hd/xwzx.htm",
#   "未来技术学院-教务通知": "https://futuretechnologyschool.sztu.edu.cn/xw_hd/tzgg1/jw.htm",
#   "未来技术学院-科研通知": "https://futuretechnologyschool.sztu.edu.cn/xw_hd/tzgg1/ky.htm",
#   "未来技术学院-学工通知": "https://futuretechnologyschool.sztu.edu.cn/xw_hd/tzgg1/xg.htm",
#   "未来技术学院-校园通知": "https://futuretechnologyschool.sztu.edu.cn/xw_hd/tzgg1/xy.htm",
#   "未来技术学院-行政通知": "https://futuretechnologyschool.sztu.edu.cn/xw_hd/tzgg1/xz.htm",

#   # 集成电路与光电芯片学院
#   "集成电路与光电芯片学院-通知公告": "https://icoc.sztu.edu.cn/xwzx/tzgg.htm",
#   "集成电路与光电芯片学院-学术成果": "https://icoc.sztu.edu.cn/kxyj/xscg.htm",
#   "集成电路与光电芯片学院-学院新闻": "https://icoc.sztu.edu.cn/xwzx/xyxw.htm",

#   # 创意设计学院
#   "创意设计学院-学院焦点": "https://design.sztu.edu.cn/xydt/xyjd.htm",
#   "创意设计学院-院系新闻": "https://design.sztu.edu.cn/xydt/yxxw.htm",
#   "创意设计学院-通知公告": "https://design.sztu.edu.cn/xydt/tzgg.htm",
#   "创意设计学院-党团工作": "https://design.sztu.edu.cn/xydt/dtgz.htm",
#   "创意设计学院-社会服务": "https://design.sztu.edu.cn/xydt/shfw.htm",
#   "创意设计学院-校园生活": "https://design.sztu.edu.cn/xydt/xysh.htm",
}


headers = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
}

for name, url in urls.items():
    print(f"\n========== 正在扒取【{name}】的底层翻页代码 ==========")
    try:
        res = requests.get(url, headers=headers, timeout=10)
        res.encoding = 'utf-8'
        soup = BeautifulSoup(res.text, 'html.parser')
        
        # 绝大多数 Webplus 系统的分页代码都包含 "下一页" 或 "下页" 文本
        # 我们用这个极其野蛮的特征，直接把包含这几个字的整个框给端出来
        next_btn = soup.find('a', string=lambda t: t and ('下页' in t or '下一页' in t))
        
        if next_btn:
            # 往上跳两级，把整个翻页导航条的 HTML 全打出来
            pagination_container = next_btn.parent.parent
            print(pagination_container.prettify())
        else:
            print("❌ 警告：在这个网页的源码里，根本没有找到带有'下一页'文本的 a 标签！可能使用了 JS 动态加载。")
            
    except Exception as e:
        print(f"请求失败: {e}")