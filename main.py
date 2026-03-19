import webview
import os
import sys
import pystray
from PIL import Image, ImageDraw
import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 🌟 初始化全局日志系统（必须在其他模块导入之前）
from src.logger import setup_logging
setup_logging()

# 引入我们的"总调度室"
from src.api import Api

# ================= 托盘红点管理 =================
# 全局变量：存储托盘图标实例
_tray_icon = None
_has_alert = False
_base_icon_256 = None  # 存储超清母版，用于后续超采样画红点

def check_campus_network() -> bool:
    """探测是否处于深圳技术大学校园网环境"""
    try:
        # 探测公文通主页，设置 2 秒极短超时
        requests.head("https://nbw.sztu.edu.cn/list.jsp?urltype=tree.TreeTempUrl&wbtreeid=1029", timeout=2, verify=False)
        return True
    except requests.exceptions.RequestException:
        return False
    


def set_tray_alert():
    """在托盘图标上显示红点提醒（超采样抗锯齿版）"""
    global _has_alert, _tray_icon, _base_icon_256
    if _tray_icon is None or _base_icon_256 is None:
        return

    try:
        # 🌟 修复4：由于 PIL 画圆没有抗锯齿，我们必须在 256x256 的高清母版上画大红点
        alert_canvas_256 = _base_icon_256.copy()
        draw = ImageDraw.Draw(alert_canvas_256)

        red_dot_radius = 28
        center_x = 256 - red_dot_radius - 12
        center_y = red_dot_radius + 12

        draw.ellipse(
            [center_x - red_dot_radius, center_y - red_dot_radius,
             center_x + red_dot_radius, center_y + red_dot_radius],
            fill='#FF3B30',
            outline='#C62828',
            width=3
        )

        # 🌟 超采样缩小：将画满马赛克大红点的 256 画布，使用 LANCZOS 强压到 64x64。
        # 此时像素级边缘会被完美的子像素抗锯齿算法柔化！
        final_alert_64 = alert_canvas_256.resize((64, 64), Image.Resampling.LANCZOS)

        _tray_icon.icon = final_alert_64
        _has_alert = True
        print("🔴 托盘红点已显示")

    except Exception as e:
        print(f"❌ 设置托盘红点失败: {e}")
def clear_tray_alert():
    """
    清除托盘图标上的红点提醒
    """
    global _has_alert
    if _tray_icon is None or _base_icon_256 is None:
        return

    try:
        # 从高清母版重新生成干净的 64x64 图标
        clean_icon_64 = _base_icon_256.resize((64, 64), Image.Resampling.LANCZOS)
        _tray_icon.icon = clean_icon_64
        _has_alert = False
        print("⚪ 托盘红点已清除")
    except Exception as e:
        print(f"❌ 清除托盘红点失败: {e}")

def has_tray_alert() -> bool:
    """检查当前是否有红点提醒"""
    return _has_alert
# ================================================

def get_html_path():
    """动态计算前端页面的绝对路径，为后续 PyInstaller 打包做准备"""
    # 采用你优化的 getattr 方法，完美绕过 Pylance 静态检查
    meipass = getattr(sys, '_MEIPASS', None)
    
    if meipass:
        base_path = meipass
    else:
        # 正常开发环境下，使用当前文件所在的目录
        base_path = os.path.dirname(os.path.abspath(__file__))
    
    return os.path.join(base_path, 'frontend', 'index.html')

def get_icon_path():
    """获取本地 png 图标的绝对路径"""
    # 采用 PyInstaller 打包后的路径兼容方案
    meipass = getattr(sys, '_MEIPASS', None)
    base_path = meipass if meipass else os.path.dirname(os.path.abspath(__file__))

    # 图标文件已移动到 frontend/icons/ 目录
    return os.path.join(base_path, 'frontend', 'icons', 'icon_white.png')

def load_tray_icon():
    """加载并高质量处理状态栏图标（超采样 SSAA 抗锯齿版）"""
    global _base_icon_256

    icon_path = get_icon_path()
    target_canvas_size = 256
    icon_visual_size = 210

    if os.path.exists(icon_path):
        try:
            source_img = Image.open(icon_path).convert("RGBA")

            # 🌟 修复1：使用 thumbnail 保持比例，且抗锯齿算法更优
            source_img.thumbnail((icon_visual_size, icon_visual_size), Image.Resampling.LANCZOS)

            canvas_256 = Image.new("RGBA", (target_canvas_size, target_canvas_size), (0, 0, 0, 0))

            offset_x = (target_canvas_size - source_img.width) // 2
            offset_y = (target_canvas_size - source_img.height) // 2

            # 🌟 修复2：paste 时必须传入第三个参数 source_img 作为 mask，否则透明边缘会发黑发硬！
            canvas_256.paste(source_img, (offset_x, offset_y), source_img)

            # 存储 256x256 高清母版，供后续画红点使用
            _base_icon_256 = canvas_256

            # 🌟 修复3：超采样抗锯齿 (SSAA) - 将 256 浓缩成完美的 64x64
            final_icon_64 = canvas_256.resize((64, 64), Image.Resampling.LANCZOS)

            return final_icon_64
        except Exception as e:
            print(f"❌ 托盘图标处理失败: {e}")
    else:
        print(f"⚠️ 找不到托盘图标文件: {icon_path}")
if __name__ == '__main__':
    # 🌟 校园网护城河：启动拦截
    if not check_campus_network():
        print("⛔️ 访问受限：未检测到校园网环境。")
        error_html = """
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; text-align: center; margin-top: 20vh; color: #111827;">
            <h2 style="color: #E11D48;">访问受限</h2>
            <p style="color: #4B5563; font-size: 14px;">Microflow（微流） 仅限在深圳技术大学校园网环境下运行。</p>
            <p style="color: #4B5563; font-size: 14px;">请连接校园 WiFi 后重新启动软件。</p>
        </div>
        """
        webview.create_window('网络错误', html=error_html, width=400, height=300)
        webview.start()
        sys.exit(0)
    
    # 网络校验通过，正常启动原本的微流 Microflow逻辑...
    # 实例化后端桥接 API
    api = Api()

    # 获取前端页面路径
    # 移除 file:// 协议，让 pywebview 启用本地 HTTP 服务器，彻底绕过字体跨域拦截
    html_url = get_html_path()
    
    # 🌟 检测启动参数：如果是开机自启（带了 --minimized 参数），则初始隐藏
    start_minimized = "--minimized" in sys.argv

    # 创建原生窗口 (保留了你设置的 450x750 尺寸和相关属性)
    window = webview.create_window(
        title='Microflow',
        url=html_url,
        js_api=api,
        width=470,
        height=750,
        min_size=(470, 750),   # 限制最小宽高
        frameless=False,       # 使用原生系统边框
        easy_drag=False,
        transparent=False,     # 关闭透明，让原生窗口更加稳定
        background_color='#FFFFFF',
        hidden=start_minimized  # 👈 核心：如果是自启则初始隐藏
    )

    api.window = window

    # ================= 以下为新增的"系统托盘与生命周期"逻辑 =================

    # 1. 拦截原生红点关闭事件
    def on_closing():
        """当用户点击系统原生的红色关闭按钮时触发"""
        if window is not None:  # 👈 新增安全判断，消除 Pylance 警告
            window.hide() # 隐藏窗口到后台
        return False  # 返回 False 告诉系统：拦截销毁操作，不要关掉进程！

    # 绑定关闭事件
    if window is not None:      # 👈 新增安全判断，消除 Pylance 警告
        window.events.closing += on_closing

    # 2. 系统托盘交互逻辑
    def on_show_window(icon, item):
        """点击菜单：显示窗口"""
        if window is not None:  # 👈 新增安全判断，消除 Pylance 警告
            window.show()
            window.restore() # 如果被最小化了则恢复居中
            # 用户主动唤醒窗口时，清除托盘红点
            clear_tray_alert()

    def on_quit_app(icon, item):
        """点击菜单：彻底退出"""
        api.is_running = False # 停止后台线程的死循环
        api.force_quit()
        icon.stop()            # 销毁右上角的托盘图标
        os._exit(0)            # 彻底杀掉 Python 进程

    # 构建托盘右键菜单
    tray_menu = pystray.Menu(
        pystray.MenuItem('详情', on_show_window, default=True),  # 🌟 设为默认，点击图标直接显示窗口
        pystray.MenuItem('退出', on_quit_app)
    )

    # 实例化托盘图标
    icon_image = load_tray_icon()
    tray_icon = pystray.Icon("TongwenMonitor", icon_image, "公文通监控中", tray_menu)

    # 🌟 保存到全局变量，供 api.py 调用
    import main as main_module
    main_module._tray_icon = tray_icon
    # _base_icon_256 已在 load_tray_icon() 中设置

    # 3. 在 Webview 启动前，启动后台守护线程与托盘
    # run_detached() 会在独立的线程中跑托盘图标，不阻塞主 UI 线程
    tray_icon.run_detached()

    # 🌟 开启后台轮询抓取（间隔由配置文件动态决定，支持热重载）
    api.start_daemon()
    # ====================================================================

    # 启动应用
    # 👇 新增：强制获取焦点的启动回调函数
    def on_app_start():
        # 如果不是开机静默自启，则强制向 macOS 索要窗口焦点和鼠标点击权限
        if not start_minimized:
            if window is not None:  # 👈 加上这一行安全护盾，满足 Pylance 的类型检查
                window.restore()
                window.show()

    # 启动应用，并将回调函数注入进去，强制开启 HTTP 服务
    webview.start(func=on_app_start, debug=False, http_server=True)