import webview
import os
import sys
import pystray
from PIL import Image, ImageDraw

# 引入我们的"总调度室"
from src.api import Api

# ================= 托盘红点管理 =================
# 全局变量：存储托盘图标实例和原始图标图像
_tray_icon = None
_original_icon_image = None
_has_alert = False

def set_tray_alert():
    """
    在托盘图标上显示红点提醒
    """
    global _has_alert
    if _tray_icon is None:
        return

    try:
        # 读取原始图标
        icon_path = get_icon_path()
        if not os.path.exists(icon_path):
            return

        # 创建带有红点的图标
        source_img = Image.open(icon_path).convert("RGBA")

        # 缩放到处理尺寸
        target_canvas_size = 44
        icon_visual_size = 36
        resized_icon = source_img.resize((icon_visual_size, icon_visual_size), Image.Resampling.LANCZOS)

        # 创建透明画布并居中贴上图标
        final_icon = Image.new("RGBA", (target_canvas_size, target_canvas_size), (0, 0, 0, 0))
        offset = (target_canvas_size - icon_visual_size) // 2
        final_icon.paste(resized_icon, (offset, offset))

        # 在右上角绘制红点
        draw = ImageDraw.Draw(final_icon)
        red_dot_radius = 6
        red_dot_center = (target_canvas_size - red_dot_radius - 2, red_dot_radius + 2)
        draw.ellipse(
            [red_dot_center[0] - red_dot_radius, red_dot_center[1] - red_dot_radius,
             red_dot_center[0] + red_dot_radius, red_dot_center[1] + red_dot_radius],
            fill='red',
            outline='darkred',
            width=1
        )

        # 更新托盘图标
        _tray_icon.icon = final_icon
        _has_alert = True
        print("🔴 托盘红点已显示")

    except Exception as e:
        print(f"❌ 设置托盘红点失败: {e}")

def clear_tray_alert():
    """
    清除托盘图标上的红点提醒
    """
    global _has_alert
    if _tray_icon is None or _original_icon_image is None:
        return

    try:
        _tray_icon.icon = _original_icon_image
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
    import sys
    import os

    # 采用 PyInstaller 打包后的路径兼容方案
    meipass = getattr(sys, '_MEIPASS', None)
    base_path = meipass if meipass else os.path.dirname(os.path.abspath(__file__))

    # 图标文件已移动到 frontend/icons/ 目录
    return os.path.join(base_path, 'frontend', 'icons', 'icon_white.png')

def load_tray_icon():
    """加载并高质量处理状态栏图标"""
    from PIL import Image
    import os
    
    icon_path = get_icon_path()
    
    # 针对高分屏 (Retina) 优化的标准状态栏尺寸
    # macOS 推荐 22x22 视网膜对应 44x44，这里使用 32x32 是一个很好的平衡点
    # 针对 macOS 高分屏的精确尺寸
    target_canvas_size = 44  # 提供给 macOS 的最终画布尺寸
    icon_visual_size = 36    # 实际图标的视觉尺寸（留出上下左右各 4 像素的 Padding）

    if os.path.exists(icon_path):
        try:
            source_img = Image.open(icon_path).convert("RGBA")
            
            # 1. 先将原图高质量缩放到较小的"视觉尺寸"
            resized_icon = source_img.resize((icon_visual_size, icon_visual_size), Image.Resampling.LANCZOS)
            
            # 2. 创建一个完全透明的 44x44 终极画布
            final_icon = Image.new("RGBA", (target_canvas_size, target_canvas_size), (0, 0, 0, 0))
            
            # 3. 计算居中偏移量并贴上去
            offset = (target_canvas_size - icon_visual_size) // 2
            final_icon.paste(resized_icon, (offset, offset))
            
            return final_icon
        except Exception as e:
            print(f"❌ 托盘图标处理失败: {e}")
            # 优雅降级：如果失败，生成一个全透明的图块，防止程序直接崩溃
            # return Image.new("RGBA", (target_size, target_size), (0, 0, 0, 0))
    else:
        print(f"⚠️ 找不到托盘图标文件: {icon_path}")
        # return Image.new("RGBA", (target_size, target_size), (0, 0, 0, 0))

if __name__ == '__main__':
    # 实例化后端桥接 API
    api = Api()
    
    # 获取前端页面路径
    html_url = f"file://{get_html_path()}"
    
    # 🌟 检测启动参数：如果是开机自启（带了 --minimized 参数），则初始隐藏
    start_minimized = "--minimized" in sys.argv

    # 创建原生窗口 (保留了你设置的 450x750 尺寸和相关属性)
    window = webview.create_window(
        title='通文',
        url=html_url,
        js_api=api,
        width=465,
        height=750,
        min_size=(450, 650),   # 限制最小宽高
        frameless=False,       # 使用原生系统边框
        easy_drag=False,       
        transparent=False,     # 关闭透明，让原生窗口更加稳定
        background_color='#FFFFFF',
        hidden="--minimized" in sys.argv  # 👈 核心：如果是自启则初始隐藏
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
        pystray.MenuItem('详情', on_show_window),
        pystray.MenuItem('退出', on_quit_app)
    )

    # 实例化托盘图标
    icon_image = load_tray_icon()
    tray_icon = pystray.Icon("TongwenMonitor", icon_image, "公文通监控中", tray_menu)

    # 🌟 保存到全局变量，供 api.py 调用
    import main as main_module
    main_module._tray_icon = tray_icon
    main_module._original_icon_image = icon_image

    # 3. 在 Webview 启动前，启动后台守护线程与托盘
    # run_detached() 会在独立的线程中跑托盘图标，不阻塞主 UI 线程
    tray_icon.run_detached()
    
    # 开启后台每 15 分钟一次的抓取轮询 (这里调用了你在 src/api.py 中新写的 start_daemon 方法)
    api.start_daemon(debug_seconds=300)
    # api.start_daemon(interval_minutes=1)
    # # 生产环境（默认 15 分钟）
    # api.start_daemon() 

    # # 极限开发测试环境（每隔 5 秒抓取一次）
    # api.start_daemon(debug_seconds=5)
    # ====================================================================

    # 启动应用
    # 👇 新增：强制获取焦点的启动回调函数
    # 👇 新增：强制获取焦点的启动回调函数
    def on_app_start():
        # 如果不是开机静默自启，则强制向 macOS 索要窗口焦点和鼠标点击权限
        if not start_minimized:
            if window is not None:  # 👈 加上这一行安全护盾，满足 Pylance 的类型检查
                window.restore()
                window.show()

    # 启动应用，并将回调函数注入进去
    webview.start(func=on_app_start, debug=False)