# pyright: reportAttributeAccessIssue=false
# pyright: reportPossiblyUnboundVariable=false
import webview
import os
import sys
from PIL import Image, ImageDraw
import requests
import urllib3
from datetime import datetime
import threading

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 🌟 PyObjC 原生 macOS 状态栏支持
try:
    # 加上 type: ignore 屏蔽 Pylance 的动态库静态检查报错
    from Cocoa import (  # type: ignore
        NSStatusBar,
        NSVariableStatusItemLength,
        NSImage,
        NSBundle,
        NSMenu,
        NSMenuItem,
        NSAlert,
        NSImageAlignCenter,
        NSOnState,
        NSOffState,
    )
    from Foundation import NSObject, NSRunLoop, NSDate  # type: ignore
    from AppKit import NSApplication, NSApp, NSImageLoadStatusCompleted  # type: ignore
    from PyObjCTools import AppHelper  # type: ignore
    import objc  # type: ignore

    HAS_PYOBJC = True
except ImportError as e:
    HAS_PYOBJC = False
    print(f"⚠️ PyObjC 导入失败 ({e})，使用 pystray 作为备选方案")
    import pystray

# 🌟 初始化全局日志系统（必须在其他模块导入之前）
from src.logger import setup_logging

setup_logging()

# 引入我们的"总调度室"
from src.api import Api


def get_main_module():
    """获取主模块引用（解决 __main__ 模块名问题）"""
    import sys

    return sys.modules.get("__main__")


# ================= 托盘状态管理 =================
# 全局变量：存储托盘图标实例和状态
_status_item = None  # NSStatusItem 实例
_base_image = None  # 原始图标 NSImage
_has_alert = False
_alert_badge = None  # 红点图层
_unread_count = 0  # 未读公文数量
_last_sync_time = None  # 最近同步时间
_mute_mode = False  # 免打扰模式
_api_instance = None  # API 实例引用
_window_instance = None  # 窗口实例引用
_menu_delegate = None  # 菜单代理
_unread_item = None  # 未读消息菜单项引用
_sync_item = None  # 同步时间菜单项引用
_mute_item = None  # 免打扰菜单项引用

# 🌟 修复 _tray_icon 未绑定报错：在这里补全 Pystray 的全局变量声明
_tray_icon = None  # pystray 实例
_base_icon_256 = None  # pystray 基础图像


def run_on_main_thread(func):
    """确保函数在主线程上执行（macOS UI 操作必须在主线程）"""
    if HAS_PYOBJC:
        try:
            import threading

            if threading.current_thread() is threading.main_thread():
                func()
            else:
                # 使用 PyObjCTools.AppHelper.callAfter 调度到主线程
                AppHelper.callAfter(func)
        except Exception as e:
            print(f"❌ 主线程调度失败: {e}")
            func()
    else:
        func()


def update_tray_status(unread: int = None, sync_time: str = None):  # type:ignore
    """更新托盘状态信息（包含红点触发与文本刷新）"""
    # 🌟 关键修复：使用 __main__ 获取真正的主模块
    main_mod = get_main_module()
    if main_mod is None:
        print("❌ [DEBUG] 无法获取主模块")
        return

    # 🔍 调试日志
    print(
        f"📊 [DEBUG] update_tray_status 被调用: unread={unread}, sync_time={sync_time}"
    )

    if unread is not None:
        main_mod._unread_count = unread
    if sync_time is not None:
        main_mod._last_sync_time = sync_time

    def do_update():
        print(f"📊 [DEBUG] do_update 开始执行, _unread_count={main_mod._unread_count}")
        if HAS_PYOBJC:
            # 1. 动态刷新菜单栏里的文字
            if hasattr(main_mod, "_unread_item") and main_mod._unread_item:
                main_mod._unread_item.setTitle_(f"未读: {main_mod._unread_count}")
                print(f"📊 [DEBUG] 菜单项文字已更新为: 未读: {main_mod._unread_count}")
            else:
                print(f"📊 [DEBUG] _unread_item 不存在或为 None")

            if hasattr(main_mod, "_sync_item") and main_mod._sync_item:
                sync_text = (
                    main_mod._last_sync_time if main_mod._last_sync_time else "--"
                )
                main_mod._sync_item.setTitle_(f"同步: {sync_text}")

            # 2. 🌟 核心修复：根据未读数量控制红点显示/隐藏
            print(f"📊 [DEBUG] 准备更新红点, _unread_count={main_mod._unread_count}")
            if main_mod._unread_count > 0:
                print(f"📊 [DEBUG] 调用 set_tray_alert()")
                set_tray_alert()
            else:
                print(f"📊 [DEBUG] 调用 clear_tray_alert()")
                clear_tray_alert()

            print(
                f"📊 托盘状态已更新: 未读={main_mod._unread_count}, 同步={main_mod._last_sync_time}"
            )

        else:
            # pystray 备选方案同步更新
            if main_mod._tray_icon:
                main_mod._tray_icon.update_menu()

            if main_mod._unread_count > 0:
                set_tray_alert()
            else:
                clear_tray_alert()

            print(
                f"📊 托盘状态已更新: 未读={main_mod._unread_count}, 同步={main_mod._last_sync_time}"
            )

    # 菜单文字更新需要在主线程，红点函数内部也有主线程保护
    run_on_main_thread(do_update)


def set_tray_alert():
    """在托盘图标上显示红点提醒"""
    main_mod = get_main_module()

    def do_set_alert():
        if main_mod is None:
            return
        if HAS_PYOBJC:
            if main_mod._status_item is None:
                return
            try:
                # 创建带红点的图标
                alert_image = _create_alert_icon()
                if alert_image:
                    main_mod._status_item.button().setImage_(alert_image)
                    main_mod._has_alert = True
                    print("🔴 托盘红点已显示")
            except Exception as e:
                print(f"❌ 设置托盘红点失败: {e}")
        else:
            # pystray 备选方案
            if main_mod._tray_icon is None or main_mod._base_icon_256 is None:
                return
            try:
                alert_canvas = main_mod._base_icon_256.copy()
                draw = ImageDraw.Draw(alert_canvas)
                red_dot_radius = 8
                canvas_size = alert_canvas.width
                center_x = canvas_size - red_dot_radius - 3
                center_y = red_dot_radius + 3
                draw.ellipse(
                    [
                        center_x - red_dot_radius,
                        center_y - red_dot_radius,
                        center_x + red_dot_radius,
                        center_y + red_dot_radius,
                    ],
                    fill="#FF3B30",
                    outline="#C62828",
                    width=1,
                )
                main_mod._tray_icon.icon = alert_canvas
                main_mod._has_alert = True
            except Exception as e:
                print(f"❌ 设置托盘红点失败: {e}")

    run_on_main_thread(do_set_alert)


def clear_tray_alert():
    """清除托盘图标上的红点提醒"""
    main_mod = get_main_module()

    print(
        f"📊 [DEBUG] clear_tray_alert 被调用, _status_item={main_mod._status_item if main_mod else 'N/A'}, _base_image={main_mod._base_image if main_mod else 'N/A'}"
    )

    def do_clear_alert():
        if main_mod is None:
            return
        print(f"📊 [DEBUG] do_clear_alert 开始执行, HAS_PYOBJC={HAS_PYOBJC}")
        if HAS_PYOBJC:
            if main_mod._status_item is None or main_mod._base_image is None:
                print(
                    f"📊 [DEBUG] 提前返回: _status_item={main_mod._status_item}, _base_image={main_mod._base_image}"
                )
                return
            try:
                main_mod._status_item.button().setImage_(main_mod._base_image)
                main_mod._has_alert = False
                print("⚪ 托盘红点已清除")
            except Exception as e:
                print(f"❌ 清除托盘红点失败: {e}")
        else:
            if main_mod._tray_icon is None or main_mod._base_icon_256 is None:
                return
            try:
                main_mod._tray_icon.icon = main_mod._base_icon_256
                main_mod._has_alert = False
            except Exception as e:
                print(f"❌ 清除托盘红点失败: {e}")

    run_on_main_thread(do_clear_alert)


def _create_alert_icon():
    """🌟 终极版：支持自定义尺寸和透明度的原生红点图标 (PyObjC)"""
    main_mod = get_main_module()
    if main_mod is None or main_mod._base_image is None:
        return None

    base_image = main_mod._base_image

    try:
        from Cocoa import NSImage, NSColor, NSBezierPath  # type: ignore
        from AppKit import NSCompositingOperationSourceOver  # type: ignore

        # 1. 获取原始图标尺寸
        size = base_image.size()

        # 2. 创建用于绘制的新图像 (保持透明背景)
        new_image = NSImage.alloc().initWithSize_(size)
        new_image.lockFocus()

        # 3. 先把基础图标画上去
        from Cocoa import NSZeroRect, NSZeroPoint  # type: ignore

        base_image.drawAtPoint_fromRect_operation_fraction_(
            NSZeroPoint, NSZeroRect, NSCompositingOperationSourceOver, 1.0
        )

        # =========================================================================
        # 🌟 核心修改区域：在这里调整大小和透明度
        # =========================================================================

        # 1️⃣
        # 原来是 7-9，我们在上一步改为 5.0。
        # 你可以继续增大或减小这个数字（单位是像素）。
        # 例如：改为 4.0（更小）或 6.0（稍微大点）。
        dot_diameter = 8.0

        # 2️⃣
        # 在底部的 NSColor 定义中。

        #
        # 既然改变了直径，我们需要重新计算 Y 轴坐标以保持顶部对齐。
        # 如果你改变了直径，请务必更新这里。
        edge_offset = 0.5  # 离开左/顶边缘的距离
        final_x = edge_offset
        final_y = size.height - dot_diameter - edge_offset  # (图标高度 - 直径 - 偏移量)

        # 4. 创建圆点的矩形区域 ((x, y), (width, height))
        oval_rect = ((final_x, final_y), (dot_diameter, dot_diameter))
        path = NSBezierPath.bezierPathWithOvalInRect_(oval_rect)

        # 5. 填充颜色并设置透明度
        # 🌟 参数格式: colorWithRed_green_blue_alpha_(R, G, B, Alpha)
        # Alpha 的取值范围是 0.0（完全透明）到 1.0（完全不透明）。

        # ：
        # 将最后的 1.0 改为你需要的透明度。
        # 例如：
        # 0.8  - 80% 不透明 (稍微能看到一点背景)
        # 0.5  - 半透明
        # 0.3  - 非常透明，很低调
        NSColor.colorWithRed_green_blue_alpha_(
            0.75, 0.23, 0.19, 1.0
        ).set()  # 👈 🌟 核心修改点

        path.fill()

        # 6. 完成绘制并解锁
        new_image.unlockFocus()
        return new_image

    except Exception as e:
        print(f"❌ 绘制原生自定义红点失败: {e}")
        return base_image  # 失败时回退到无红点图标


def has_tray_alert() -> bool:
    """检查当前是否有红点提醒"""
    main_mod = get_main_module()
    if main_mod is None:
        return False
    return main_mod._has_alert


# ================================================


def get_html_path():
    """动态计算前端页面的绝对路径，为后续 PyInstaller 打包做准备"""
    # 采用你优化的 getattr 方法，完美绕过 Pylance 静态检查
    meipass = getattr(sys, "_MEIPASS", None)

    if meipass:
        base_path = meipass
    else:
        # 正常开发环境下，使用当前文件所在的目录
        base_path = os.path.dirname(os.path.abspath(__file__))

    return os.path.join(base_path, "frontend", "index.html")


def get_icon_path():
    """获取本地 png 图标的绝对路径"""
    # 采用 PyInstaller 打包后的路径兼容方案
    meipass = getattr(sys, "_MEIPASS", None)
    base_path = meipass if meipass else os.path.dirname(os.path.abspath(__file__))

    # 图标文件已移动到 frontend/icons/ 目录
    return os.path.join(base_path, "frontend", "icons", "icon_white.png")


def load_tray_icon_native():
    """
    使用 PyObjC 原生方式加载状态栏图标

    macOS 状态栏图标标准：
    - 逻辑尺寸：18x18 点（Retina 自动 @2x）
    - 支持 PDF 矢量或高分辨率 PNG
    - 模板模式：系统自动适配深浅色主题
    """
    global _base_image

    icon_path = get_icon_path()

    if not os.path.exists(icon_path):
        print(f"⚠️ 找不到托盘图标文件: {icon_path}")
        return None

    try:
        # 使用 NSImage 加载图标，支持 Retina
        image = NSImage.alloc().initWithContentsOfFile_(icon_path)

        if image is None:
            print(f"❌ 无法加载图标: {icon_path}")
            return None

        # 设置逻辑尺寸（macOS 自动处理 Retina）
        image.setSize_((18, 18))

        # 启用模板模式：系统自动根据深浅模式调整颜色
        # 白色图标在浅色模式下会变成黑色，深色模式下保持白色
        image.setTemplate_(True)

        _base_image = image
        return image

    except Exception as e:
        print(f"❌ 托盘图标加载失败: {e}")
        return None


def load_tray_icon():
    """加载托盘图标（pystray 备选方案）"""
    global _base_icon_256

    icon_path = get_icon_path()
    target_size = 36

    if os.path.exists(icon_path):
        try:
            source_img = Image.open(icon_path).convert("RGBA")
            intermediate_size = target_size * 4
            source_img.thumbnail(
                (intermediate_size, intermediate_size), Image.Resampling.LANCZOS
            )
            source_img.thumbnail((target_size, target_size), Image.Resampling.LANCZOS)

            white_img = Image.new("RGBA", source_img.size, (0, 0, 0, 0))
            pixels = source_img.load()
            white_pixels = white_img.load()

            if pixels is not None and white_pixels is not None:
                for y in range(source_img.height):
                    for x in range(source_img.width):
                        # 🌟 修复：告诉 Pylance 强制解包没问题
                        r, g, b, a = pixels[x, y]  # type: ignore
                        white_pixels[x, y] = (255, 255, 255, a)

            _base_icon_256 = white_img.resize((72, 72), Image.Resampling.LANCZOS)
            return white_img

        except Exception as e:
            print(f"❌ 托盘图标处理失败: {e}")
    else:
        print(f"⚠️ 找不到托盘图标文件: {icon_path}")


# ================= PyObjC 菜单代理类 =================
if HAS_PYOBJC:
    # 避免重复定义 Objective-C 类（当模块被重新导入时）
    try:
        TrayMenuDelegate = objc.lookUpClass("TrayMenuDelegate")
    except objc.error:

        class TrayMenuDelegate(NSObject):
            """菜单代理，处理菜单项状态"""

            def menuNeedsUpdate_(self, menu):
                pass

            # 🌟 修复：严格保证只有 (self, sender) 两个参数
            def onShowWindow_(self, sender):
                main_mod = get_main_module()
                import threading

                def do_show():
                    try:
                        if main_mod._window_instance:
                            main_mod._window_instance.show()
                            main_mod._window_instance.restore()
                            main_mod._window_instance.on_top = True
                            main_mod._window_instance.on_top = False
                            main_mod._window_instance.evaluate_js(
                                "if(window.handleTodayClick) window.handleTodayClick();"
                            )
                            main_mod.clear_tray_alert()
                    except Exception as e:
                        print(f"❌ 打开主界面失败: {e}")

                threading.Thread(target=do_show, daemon=True).start()

            def onForceCheck_(self, sender):
                main_mod = get_main_module()
                import threading
                from datetime import datetime

                def do_check():
                    if main_mod._api_instance:
                        try:
                            result = main_mod._api_instance.check_updates(
                                is_manual=True
                            )
                            print(f"🔄 手动触发检查更新: {result}")
                            main_mod._last_sync_time = datetime.now().strftime("%H:%M")
                            main_mod.update_tray_status()
                        except Exception as e:
                            print(f"❌ 触发检查更新失败: {e}")

                threading.Thread(target=do_check, daemon=True).start()

            def onToggleMute_(self, sender):
                main_mod = get_main_module()
                import threading
                from Cocoa import NSOnState, NSOffState  # type: ignore

                main_mod._mute_mode = not main_mod._mute_mode

                if main_mod._api_instance:
                    try:
                        config_dict = main_mod._api_instance.config_service.to_dict()
                        config_dict["muteMode"] = main_mod._mute_mode
                        main_mod._api_instance.config_service.save(config_dict)
                        print(
                            f"免打扰模式: {'开启' if main_mod._mute_mode else '关闭'}"
                        )
                    except Exception as e:
                        print(f"❌ 保存免打扰设置失败: {e}")

                main_mod.update_tray_status()
                # 利用传进来的 sender 直接修改菜单的打钩状态
                sender.setState_(NSOnState if main_mod._mute_mode else NSOffState)

                def notify_frontend():
                    try:
                        if main_mod._window_instance:
                            main_mod._window_instance.evaluate_js(
                                "if(window.refreshConfigFromBackend) window.refreshConfigFromBackend();"
                            )
                    except Exception:
                        pass

                threading.Thread(target=notify_frontend, daemon=True).start()

            def onOpenSettings_(self, sender):
                main_mod = get_main_module()
                import threading

                def do_open():
                    try:
                        if main_mod._window_instance:
                            main_mod._window_instance.show()
                            main_mod._window_instance.restore()
                            main_mod._window_instance.on_top = True
                            main_mod._window_instance.on_top = False
                            main_mod._window_instance.evaluate_js(
                                "if(window.openSettingsFromTray) window.openSettingsFromTray();"
                            )
                    except Exception as e:
                        print(f"❌ 打开设置失败: {e}")

                threading.Thread(target=do_open, daemon=True).start()

            def onQuitApp_(self, sender):
                main_mod = get_main_module()
                import os

                if main_mod._api_instance:
                    main_mod._api_instance.is_running = False
                    main_mod._api_instance.force_quit()
                os._exit(0)


def create_native_menu(api, window):
    """创建原生 macOS 菜单"""
    if not HAS_PYOBJC:
        return None, None, None, None, None

    main_mod = get_main_module()

    # 创建菜单代理
    delegate = TrayMenuDelegate.alloc().init()

    menu = NSMenu.alloc().init()
    menu.setDelegate_(delegate)

    # ===== 状态展示层 =====
    unread_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        f"未读: {main_mod._unread_count}", None, ""
    )
    unread_item.setEnabled_(False)  # 保持禁用状态，作为纯文本展示
    menu.addItem_(unread_item)

    sync_text = main_mod._last_sync_time if main_mod._last_sync_time else "--"
    sync_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        f"同步: {sync_text}", None, ""
    )
    sync_item.setEnabled_(False)
    menu.addItem_(sync_item)

    # 分隔线
    menu.addItem_(NSMenuItem.separatorItem())

    # ===== 核心操作层 =====
    # 🌟 修复：字符串也对应改为只带一个冒号的驼峰写法
    home_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "打开主页", "onShowWindow:", ""
    )
    home_item.setTarget_(delegate)
    menu.addItem_(home_item)

    update_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "检查更新", "onForceCheck:", ""
    )
    update_item.setTarget_(delegate)
    menu.addItem_(update_item)

    menu.addItem_(NSMenuItem.separatorItem())

    # ===== 快捷开关层 =====
    mute_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "勿扰模式", "onToggleMute:", ""
    )
    mute_item.setTarget_(delegate)
    from Cocoa import NSOnState, NSOffState  # type: ignore

    if main_mod._mute_mode:
        mute_item.setState_(NSOnState)
    menu.addItem_(mute_item)

    settings_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "偏好设置", "onOpenSettings:", ""
    )
    settings_item.setTarget_(delegate)
    menu.addItem_(settings_item)

    menu.addItem_(NSMenuItem.separatorItem())

    # ===== 退出 =====
    quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "退出应用", "onQuitApp:", ""
    )
    quit_item.setTarget_(delegate)
    menu.addItem_(quit_item)

    return menu, delegate, unread_item, sync_item, mute_item


def run_native_tray(api, window):
    """运行原生 macOS 状态栏"""
    global _status_item, _menu_delegate, _unread_item, _sync_item, _mute_item

    main_mod = get_main_module()

    # 确保 NSApplication 存在
    app = NSApplication.sharedApplication()

    # 创建状态栏项
    status_bar = NSStatusBar.systemStatusBar()
    _status_item = status_bar.statusItemWithLength_(-1)  # NSVariableStatusItemLength

    # 设置图标
    icon = load_tray_icon_native()
    if icon:
        _status_item.button().setImage_(icon)

    # 创建菜单
    menu, delegate, unread_item, sync_item, mute_item = create_native_menu(api, window)
    _menu_delegate = delegate
    _unread_item = unread_item
    _sync_item = sync_item
    _mute_item = mute_item
    _status_item.setMenu_(menu)

    # 保存全局引用
    main_mod._api_instance = api
    main_mod._window_instance = window
    main_mod._mute_mode = api.config_service.get("muteMode", False)

    # 初始化未读数量
    try:
        subscribed_sources = api.config_service.get("subscribedSources", None)
        from src.database import db

        main_mod._unread_count = db.get_unread_count(source_names=subscribed_sources)
    except Exception as e:
        print(f"初始化未读数量失败: {e}")

    print("✅ PyObjC 原生状态栏已启动")

    # 开启后台轮询
    api.start_daemon()

    # 延迟刷新状态
    def init_status():
        import time

        time.sleep(1)
        try:
            subscribed_sources = api.config_service.get("subscribedSources", None)
            from src.database import db

            count = db.get_unread_count(source_names=subscribed_sources)
            sync_time = datetime.now().strftime("%H:%M")
            update_tray_status(unread=count, sync_time=sync_time)
            # 更新菜单项文本
            _unread_item.setTitle_(f"未读: {count}")  # type:ignore
            _sync_item.setTitle_(f"同步: {sync_time}")  # type:ignore
            print(f"📊 托盘初始状态: 未读={count}, 同步={sync_time}")
        except Exception as e:
            print(f"初始化托盘状态失败: {e}")

    threading.Thread(target=init_status, daemon=True).start()


if __name__ == "__main__":
    # 实例化后端桥接 API
    api = Api()

    # 获取前端页面路径
    html_url = get_html_path()

    # 🌟 检测启动参数：如果是开机自启（带了 --minimized 参数），则初始隐藏
    start_minimized = "--minimized" in sys.argv

    # 创建原生窗口
    window = webview.create_window(
        title="Microflow",
        url=html_url,
        js_api=api,
        width=480,
        height=750,
        min_size=(470, 750),
        frameless=False,
        easy_drag=False,
        transparent=False,
        background_color="#FFFFFF",
        hidden=start_minimized,
    )

    api.window = window

    # 保存全局引用
    main_module = get_main_module()

    main_module._api_instance = api
    main_module._window_instance = window

    # 拦截关闭事件
    def on_closing():
        if window is not None:
            window.hide()
        return False

    if window is not None:
        window.events.closing += on_closing

    # ================= 根据平台选择托盘方案 =================
    if HAS_PYOBJC:
        # 使用 PyObjC 原生方案
        print("🚀 使用 PyObjC 原生状态栏")
        run_native_tray(api, window)
    else:
        # 使用 pystray 备选方案
        print("🚀 使用 pystray 状态栏")

        # pystray 菜单回调函数
        def get_unread_text(item):
            main_mod = get_main_module()

            count = main_mod._unread_count or 0
            return f"未读: {count}"

        def get_sync_time_text(item):
            main_mod = get_main_module()

            sync_time = main_mod._last_sync_time
            return f"同步: {sync_time}" if sync_time else "同步: --"

        def mute_checked(item):
            main_mod = get_main_module()

            return main_mod._mute_mode or False

        def on_show_window(icon, item):
            main_mod = get_main_module()

            def do_show():
                try:
                    if main_mod._window_instance:
                        main_mod._window_instance.show()
                        main_mod._window_instance.restore()
                        main_mod._window_instance.on_top = True
                        main_mod._window_instance.on_top = False
                        main_mod._window_instance.evaluate_js(
                            "if(window.handleTodayClick) window.handleTodayClick();"
                        )
                        clear_tray_alert()
                except Exception as e:
                    print(f"❌ 打开主界面失败: {e}")

            threading.Thread(target=do_show, daemon=True).start()

        def on_force_check(icon, item):
            main_mod = get_main_module()
            import threading

            def do_check():
                if main_mod._api_instance:
                    try:
                        result = main_mod._api_instance.check_updates(is_manual=True)
                        print(f"🔄 手动触发检查更新: {result}")
                        main_mod._last_sync_time = datetime.now().strftime("%H:%M")
                        update_tray_status()
                    except Exception as e:
                        print(f"❌ 触发检查更新失败: {e}")

            threading.Thread(target=do_check, daemon=True).start()

        def on_toggle_mute(icon, item):
            main_mod = get_main_module()

            main_mod._mute_mode = not main_mod._mute_mode
            if main_mod._api_instance:
                try:
                    config_dict = main_mod._api_instance.config_service.to_dict()
                    config_dict["muteMode"] = main_mod._mute_mode
                    main_mod._api_instance.config_service.save(config_dict)
                    print(f"勿扰模式: {'开启' if main_mod._mute_mode else '关闭'}")
                except Exception as e:
                    print(f"❌ 保存勿扰设置失败: {e}")
            update_tray_status()

            def notify_frontend():
                try:
                    if main_mod._window_instance:
                        main_mod._window_instance.evaluate_js(
                            "if(window.refreshConfigFromBackend) window.refreshConfigFromBackend();"
                        )
                except Exception:
                    pass

            threading.Thread(target=notify_frontend, daemon=True).start()

        def on_open_settings(icon, item):
            main_mod = get_main_module()

            def do_open():
                try:
                    if main_mod._window_instance:
                        main_mod._window_instance.show()
                        main_mod._window_instance.restore()
                        main_mod._window_instance.on_top = True
                        main_mod._window_instance.on_top = False
                        main_mod._window_instance.evaluate_js(
                            "if(window.openSettingsFromTray) window.openSettingsFromTray();"
                        )
                except Exception as e:
                    print(f"❌ 打开设置失败: {e}")

            threading.Thread(target=do_open, daemon=True).start()

        def on_quit_app(icon, item):
            api.is_running = False
            api.force_quit()
            icon.stop()
            os._exit(0)

        # 创建 pystray 菜单
        def create_tray_menu():
            return pystray.Menu(
                pystray.MenuItem(
                    get_unread_text, lambda icon, item: None, enabled=False
                ),
                pystray.MenuItem(
                    get_sync_time_text, lambda icon, item: None, enabled=False
                ),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("打开主页", on_show_window),
                pystray.MenuItem("检查更新", on_force_check),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("勿扰模式", on_toggle_mute, checked=mute_checked),
                pystray.MenuItem("偏好设置", on_open_settings),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("退出应用", on_quit_app),
            )

        # 初始化 pystray 托盘
        icon_image = load_tray_icon()
        tray_icon = pystray.Icon(
            "MicroFlow", icon_image, "MicroFlow 监控中", create_tray_menu()
        )

        main_module._tray_icon = tray_icon
        main_module._mute_mode = api.config_service.get("muteMode", False)

        try:
            subscribed_sources = api.config_service.get("subscribedSources", None)
            from src.database import db

            main_module._unread_count = db.get_unread_count(
                source_names=subscribed_sources
            )
        except Exception as e:
            print(f"初始化未读数量失败: {e}")

        tray_icon.run_detached()
        api.start_daemon()

        def init_tray_status():
            import time

            time.sleep(1)
            try:
                subscribed_sources = api.config_service.get("subscribedSources", None)
                from src.database import db

                count = db.get_unread_count(source_names=subscribed_sources)
                sync_time = datetime.now().strftime("%H:%M")
                update_tray_status(unread=count, sync_time=sync_time)
                print(f"📊 托盘初始状态: 未读={count}, 同步={sync_time}")
            except Exception as e:
                print(f"初始化托盘状态失败: {e}")

        threading.Thread(target=init_tray_status, daemon=True).start()

    # 启动应用
    def on_app_start():
        if not start_minimized:
            if window is not None:
                window.restore()
                window.show()

    webview.start(func=on_app_start, debug=True, http_server=True)
