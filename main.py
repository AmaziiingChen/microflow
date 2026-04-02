# pyright: reportAttributeAccessIssue=false
# pyright: reportPossiblyUnboundVariable=false
# pyright: reportOptionalMemberAccess=false
import webview
import os
import sys
import logging
from PIL import Image, ImageDraw
import requests
import urllib3
from datetime import datetime
import threading
import time

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
    import AppKit  # type: ignore
    import Foundation  # type: ignore
    import objc  # type: ignore

    HAS_PYOBJC = True
except ImportError as e:
    HAS_PYOBJC = False
    print(f"⚠️ PyObjC 导入失败 ({e})，使用 pystray 作为备选方案")
    import pystray

# 🌟 初始化全局日志系统（必须在其他模块导入之前）
from src.logger import setup_logging

setup_logging()
logger = logging.getLogger(__name__)

# 引入我们的"总调度室"
from src.api import Api


APP_BOOT_TS = time.time()


def get_main_module():
    """获取主模块引用（解决 __main__ 模块名问题）"""
    import sys

    return sys.modules.get("__main__")


def install_global_exception_hooks(api: Api) -> None:
    """安装 Python 全局异常钩子，将致命错误写入匿名遥测。"""
    original_sys_excepthook = sys.excepthook
    original_threading_excepthook = getattr(threading, "excepthook", None)

    def handle_main_exception(exc_type, exc_value, exc_traceback):
        try:
            api.telemetry_service.record_python_error(
                exc_type,
                exc_value,
                exc_traceback,
                is_fatal=True,
                thread_name="main",
            )
            api.telemetry_service.flush(force=True)
        except Exception:
            pass
        original_sys_excepthook(exc_type, exc_value, exc_traceback)

    def handle_thread_exception(args):
        try:
            api.telemetry_service.record_python_error(
                args.exc_type,
                args.exc_value,
                args.exc_traceback,
                is_fatal=False,
                thread_name=getattr(args.thread, "name", ""),
            )
        except Exception:
            pass
        if original_threading_excepthook is not None:
            original_threading_excepthook(args)

    sys.excepthook = handle_main_exception
    if original_threading_excepthook is not None:
        threading.excepthook = handle_thread_exception


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


def ensure_pywebview_cocoa_drag_patch():
    """为 pywebview 的 Cocoa 后端补齐顶部原生拖动热区逻辑。"""
    if not HAS_PYOBJC or sys.platform != "darwin":
        return False

    try:
        import webview.platforms.cocoa as cocoa  # type: ignore
    except Exception as e:
        logger.warning(f"加载 pywebview Cocoa 后端失败，无法安装拖动补丁: {e}")
        return False

    if getattr(cocoa, "_microflow_drag_patch_installed", False):
        return True

    if hasattr(cocoa, "_install_native_top_drag_region") and hasattr(
        cocoa, "PYWEBVIEW_ROOT_CONTAINER_ID"
    ):
        logger.info(
            "检测到 pywebview 自带 macOS 拖动实现，改为使用 MicroFlow 自定义版本"
        )

    pywebview_root_container_id = "pywebview-root-container"
    pywebview_drag_strip_id = "pywebview-drag-strip"
    pywebview_default_top_drag_height = 5.0

    try:
        drag_strip_view_cls = objc.lookUpClass("PywebviewWindowDragStripView")
    except objc.error:

        class PywebviewWindowDragStripView(AppKit.NSView):
            def initWithFrame_(self, frame):
                self = super(PywebviewWindowDragStripView, self).initWithFrame_(frame)
                if self is None:
                    return None

                self.setWantsLayer_(True)
                if self.layer() is not None:
                    self.layer().setBackgroundColor_(
                        AppKit.NSColor.clearColor().CGColor()
                    )

                self.setAutoresizingMask_(
                    AppKit.NSViewWidthSizable | AppKit.NSViewMinYMargin
                )
                self._exclusion_rects = []
                return self

            def isOpaque(self):
                return False

            def setExclusionRects_(self, rects):
                self._exclusion_rects = list(rects or [])

            def _point_hits_exclusion_rect(self, point):
                rects = getattr(self, "_exclusion_rects", None) or []
                if not rects:
                    return False

                parent_view = self.superview()
                root_point = (
                    self.convertPoint_toView_(point, parent_view)
                    if parent_view is not None
                    else point
                )
                x = float(root_point.x)
                y = float(root_point.y)
                for rect in rects:
                    try:
                        rect_x = float(rect.get("x", 0.0))
                        rect_y = float(rect.get("y", 0.0))
                        rect_w = float(rect.get("width", 0.0))
                        rect_h = float(rect.get("height", 0.0))
                    except Exception:
                        continue

                    if rect_w <= 0 or rect_h <= 0:
                        continue

                    if (
                        rect_x <= x <= rect_x + rect_w
                        and rect_y <= y <= rect_y + rect_h
                    ):
                        return True

                return False

            def hitTest_(self, point):
                bounds = self.bounds()
                if (
                    point.x < 0
                    or point.y < 0
                    or point.x > bounds.size.width
                    or point.y > bounds.size.height
                ):
                    return None
                if self._point_hits_exclusion_rect(point):
                    return None
                return self

            def acceptsFirstMouse_(self, event):
                return Foundation.YES

            def mouseDownCanMoveWindow(self):
                return Foundation.YES

            def mouseDown_(self, event):
                local_point = self.convertPoint_fromView_(
                    event.locationInWindow(), None
                )
                if self._point_hits_exclusion_rect(local_point):
                    return

                native_window = self.window()
                if native_window is not None and hasattr(
                    native_window, "performWindowDragWithEvent_"
                ):
                    native_window.performWindowDragWithEvent_(event)
                    return

                super(PywebviewWindowDragStripView, self).mouseDown_(event)

        drag_strip_view_cls = PywebviewWindowDragStripView

    def _find_subview_by_identifier(parent_view, identifier):
        if parent_view is None:
            return None

        for subview in parent_view.subviews() or []:
            try:
                current_identifier = subview.identifier()
            except Exception:
                current_identifier = None

            if current_identifier == identifier:
                return subview

        return None

    def _resolve_top_drag_height(pywebview_window):
        height = getattr(pywebview_window, "_macos_top_drag_strip_height", 0) or 0

        try:
            height = float(height)
        except Exception:
            height = 0.0

        if height <= 0:
            return pywebview_default_top_drag_height

        return max(height, 8.0)

    def _normalize_drag_exclusion_rects(pywebview_window):
        raw_rects = getattr(pywebview_window, "_macos_drag_exclusion_rects", None) or []
        normalized_rects = []

        if not isinstance(raw_rects, (list, tuple)):
            return normalized_rects

        for item in raw_rects:
            if not isinstance(item, dict):
                continue

            try:
                rect_x = float(item.get("x", 0.0))
                rect_y = float(item.get("y", 0.0))
                rect_w = float(item.get("width", 0.0))
                rect_h = float(item.get("height", 0.0))
            except Exception:
                continue

            if rect_w <= 0 or rect_h <= 0:
                continue

            normalized_rects.append(
                {
                    "x": rect_x,
                    "y": rect_y,
                    "width": rect_w,
                    "height": rect_h,
                }
            )

        return normalized_rects

    def _install_native_top_drag_region(native_window, webview_view, pywebview_window):
        if native_window is None or webview_view is None or pywebview_window is None:
            return

        if getattr(pywebview_window, "frameless", False):
            return

        drag_height = _resolve_top_drag_height(pywebview_window)
        if drag_height <= 0:
            return

        current_content_view = native_window.contentView()
        if current_content_view is None:
            return

        root_container = None
        if (
            hasattr(current_content_view, "identifier")
            and current_content_view.identifier() == pywebview_root_container_id
        ):
            root_container = current_content_view
        else:
            content_bounds = current_content_view.bounds()
            root_container = AppKit.NSView.alloc().initWithFrame_(
                Foundation.NSMakeRect(
                    0.0,
                    0.0,
                    float(content_bounds.size.width),
                    float(content_bounds.size.height),
                )
            )
            if root_container is None:
                return

            root_container.setIdentifier_(pywebview_root_container_id)
            root_container.setAutoresizingMask_(
                AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable
            )
            root_container.setAutoresizesSubviews_(True)
            root_container.setWantsLayer_(True)
            if root_container.layer() is not None:
                root_container.layer().setBackgroundColor_(
                    AppKit.NSColor.clearColor().CGColor()
                )

            native_window.setContentView_(root_container)

        if webview_view.superview() != root_container:
            if webview_view.superview() is not None:
                webview_view.removeFromSuperview()
            root_container.addSubview_(webview_view)

        root_bounds = root_container.bounds()
        root_width = float(root_bounds.size.width)
        root_height = float(root_bounds.size.height)
        strip_height = min(max(drag_height, 5.0), max(root_height, drag_height))

        layout_mode = get_macos_drag_layout_mode(pywebview_window)
        if layout_mode in {
            MACOS_DRAG_STRIP_LAYOUT_DUAL,
            MACOS_DRAG_STRIP_LAYOUT_SETTINGS_SPLIT,
        }:
            leading_inset = min(MACOS_DRAG_STRIP_LEADING_INSET, max(root_width, 0.0))
            available_width = max(root_width - leading_inset - 24.0, 0.0)
            strip_width = min(MACOS_DRAG_STRIP_DUAL_WIDTH, available_width)
            if strip_width <= 0:
                leading_inset = 0.0
                strip_width = min(root_width, MACOS_DRAG_STRIP_DUAL_WIDTH)
        else:
            leading_inset = min(MACOS_DRAG_STRIP_LEADING_INSET, max(root_width, 0.0))
            trailing_reserved = min(
                MACOS_DRAG_STRIP_TRAILING_RESERVED,
                max(root_width - leading_inset, 0.0),
            )
            strip_width = max(root_width - leading_inset - trailing_reserved, 0.0)

            if strip_width <= 0:
                leading_inset = 0.0
                strip_width = root_width

        drag_strip_frame = Foundation.NSMakeRect(
            leading_inset,
            max(root_height - strip_height, 0.0),
            strip_width,
            strip_height,
        )
        webview_frame = Foundation.NSMakeRect(
            0.0,
            0.0,
            root_width,
            max(root_height, 1.0),
        )

        drag_strip = _find_subview_by_identifier(
            root_container, pywebview_drag_strip_id
        )
        if drag_strip is None:
            drag_strip = drag_strip_view_cls.alloc().initWithFrame_(drag_strip_frame)
            if drag_strip is None:
                return
            drag_strip.setIdentifier_(pywebview_drag_strip_id)
        else:
            drag_strip.setFrame_(drag_strip_frame)

        exclusion_rects = _normalize_drag_exclusion_rects(pywebview_window)
        if hasattr(drag_strip, "setExclusionRects_"):
            drag_strip.setExclusionRects_(exclusion_rects)
        else:
            setattr(drag_strip, "_exclusion_rects", exclusion_rects)

        webview_view.setFrame_(webview_frame)
        webview_view.setAutoresizingMask_(
            AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable
        )

        if drag_strip.superview() != root_container:
            if drag_strip.superview() is not None:
                drag_strip.removeFromSuperview()
            root_container.addSubview_positioned_relativeTo_(
                drag_strip, AppKit.NSWindowAbove, webview_view
            )
        else:
            root_container.addSubview_positioned_relativeTo_(
                drag_strip, AppKit.NSWindowAbove, webview_view
            )

        native_window.makeFirstResponder_(webview_view)

    browser_view_cls = getattr(cocoa, "BrowserView", None)
    if browser_view_cls is None:
        logger.warning("未找到 pywebview BrowserView，跳过拖动补丁")
        return False

    window_delegate_cls = getattr(browser_view_cls, "WindowDelegate", None)
    browser_delegate_cls = getattr(browser_view_cls, "BrowserDelegate", None)

    if window_delegate_cls is None or browser_delegate_cls is None:
        logger.warning("pywebview Cocoa 结构不符合预期，跳过拖动补丁")
        return False

    if not getattr(window_delegate_cls, "_microflow_drag_resize_patched", False):
        original_resize = window_delegate_cls.windowDidResize_

        def patched_window_did_resize(self, notification):
            original_resize(self, notification)
            try:
                instance = browser_view_cls.get_instance(
                    "window", notification.object()
                )
                if instance:
                    _install_native_top_drag_region(
                        instance.window, instance.webview, instance.pywebview_window
                    )
            except Exception:
                pass

        window_delegate_cls.windowDidResize_ = patched_window_did_resize
        setattr(window_delegate_cls, "_microflow_drag_resize_patched", True)

    if not getattr(browser_delegate_cls, "_microflow_drag_nav_patched", False):
        original_finish = browser_delegate_cls.webView_didFinishNavigation_

        def patched_webview_did_finish_navigation(self, webview_view, nav):
            original_finish(self, webview_view, nav)
            try:
                instance = browser_view_cls.get_instance("webview", webview_view)
                if instance:
                    _install_native_top_drag_region(
                        instance.window, webview_view, instance.pywebview_window
                    )
            except Exception:
                pass

        browser_delegate_cls.webView_didFinishNavigation_ = (
            patched_webview_did_finish_navigation
        )
        setattr(browser_delegate_cls, "_microflow_drag_nav_patched", True)

    setattr(cocoa, "WindowDragStripView", drag_strip_view_cls)
    setattr(cocoa, "PYWEBVIEW_ROOT_CONTAINER_ID", pywebview_root_container_id)
    setattr(cocoa, "PYWEBVIEW_DRAG_STRIP_ID", pywebview_drag_strip_id)
    setattr(
        cocoa,
        "PYWEBVIEW_DEFAULT_TOP_DRAG_HEIGHT",
        pywebview_default_top_drag_height,
    )
    setattr(cocoa, "_find_subview_by_identifier", _find_subview_by_identifier)
    setattr(cocoa, "_resolve_top_drag_height", _resolve_top_drag_height)
    setattr(cocoa, "_install_native_top_drag_region", _install_native_top_drag_region)
    setattr(cocoa, "_microflow_drag_patch_installed", True)

    logger.info("已注入 pywebview Cocoa 顶部拖动补丁")
    return True


def apply_macos_immersive_window(window):
    """macOS: 配置沉浸式窗口 - 透明标题栏 + 红绿灯按钮"""
    if not HAS_PYOBJC or sys.platform != "darwin" or window is None:
        return

    def _apply():
        try:
            native_window = getattr(window, "native", None)
            if native_window is None:
                return

            # 1. 🌟 全尺寸内容视图（内容延伸到标题栏下方）
            full_size_mask = getattr(
                AppKit, "NSFullSizeContentViewWindowMask", 0
            ) or getattr(AppKit, "NSWindowStyleMaskFullSizeContentView", 0)

            if full_size_mask:
                current_mask = native_window.styleMask()
                native_window.setStyleMask_(current_mask | full_size_mask)

            # 2. 🌟 透明标题栏
            native_window.setTitlebarAppearsTransparent_(True)
            native_window.setTitleVisibility_(AppKit.NSWindowTitleHidden)

            # 3. 🌟 显示红绿灯按钮
            for button_type in (
                AppKit.NSWindowCloseButton,
                AppKit.NSWindowMiniaturizeButton,
                AppKit.NSWindowZoomButton,
            ):
                button = native_window.standardWindowButton_(button_type)
                if button is not None:
                    button.setHidden_(False)

            logger.info("✨ 已启用 macOS 沉浸式窗口")
        except Exception as e:
            logger.warning(f"启用沉浸式窗口失败: {e}")

    run_on_main_thread(_apply)


def update_tray_status(unread: int = None, sync_time: str = None):  # type:ignore
    """更新托盘状态信息（包含红点触发与文本刷新）"""
    # 🌟 关键修复：使用 __main__ 获取真正的主模块
    main_mod = get_main_module()
    if main_mod is None:
        logger.debug("无法获取主模块")
        return

    # 🔍 调试日志
    logger.debug(
        "update_tray_status 被调用: unread=%s, sync_time=%s", unread, sync_time
    )

    if unread is not None:
        main_mod._unread_count = unread
    if sync_time is not None:
        main_mod._last_sync_time = sync_time

    def do_update():
        logger.debug("do_update 开始执行, _unread_count=%s", main_mod._unread_count)
        if HAS_PYOBJC:
            # 1. 动态刷新菜单栏里的文字
            if hasattr(main_mod, "_unread_item") and main_mod._unread_item:
                main_mod._unread_item.setTitle_(f"未读: {main_mod._unread_count}")
            else:
                logger.debug("_unread_item 不存在或为 None")

            if hasattr(main_mod, "_sync_item") and main_mod._sync_item:
                sync_text = (
                    main_mod._last_sync_time if main_mod._last_sync_time else "--"
                )
                main_mod._sync_item.setTitle_(f"同步: {sync_text}")

            # 🌟 更新勿扰模式勾选状态
            if hasattr(main_mod, "_mute_item") and main_mod._mute_item:
                from Cocoa import NSOnState, NSOffState  # type: ignore

                main_mod._mute_item.setState_(
                    NSOnState if main_mod._mute_mode else NSOffState
                )

            # 2. 🌟 核心修复：根据未读数量控制红点显示/隐藏
            if main_mod._unread_count > 0:
                set_tray_alert()
            else:
                clear_tray_alert()

            logger.debug(
                "托盘状态已更新: 未读=%s, 同步=%s",
                main_mod._unread_count,
                main_mod._last_sync_time,
            )

        else:
            # pystray 备选方案同步更新
            if main_mod._tray_icon:
                main_mod._tray_icon.update_menu()

            if main_mod._unread_count > 0:
                set_tray_alert()
            else:
                clear_tray_alert()

            logger.debug(
                "托盘状态已更新: 未读=%s, 同步=%s",
                main_mod._unread_count,
                main_mod._last_sync_time,
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
                    logger.debug("托盘红点已显示")
            except Exception as e:
                logger.warning("设置托盘红点失败: %s", e)
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
                logger.warning("设置托盘红点失败: %s", e)

    run_on_main_thread(do_set_alert)


def clear_tray_alert():
    """清除托盘图标上的红点提醒"""
    main_mod = get_main_module()

    logger.debug(
        "clear_tray_alert 被调用, _status_item=%s, _base_image=%s",
        main_mod._status_item if main_mod else "N/A",
        main_mod._base_image if main_mod else "N/A",
    )

    def do_clear_alert():
        if main_mod is None:
            return
        logger.debug("do_clear_alert 开始执行, HAS_PYOBJC=%s", HAS_PYOBJC)
        if HAS_PYOBJC:
            if main_mod._status_item is None or main_mod._base_image is None:
                logger.debug(
                    "提前返回: _status_item=%s, _base_image=%s",
                    main_mod._status_item,
                    main_mod._base_image,
                )
                return
            try:
                main_mod._status_item.button().setImage_(main_mod._base_image)
                main_mod._has_alert = False
                logger.debug("托盘红点已清除")
            except Exception as e:
                logger.warning("清除托盘红点失败: %s", e)
        else:
            if main_mod._tray_icon is None or main_mod._base_icon_256 is None:
                return
            try:
                main_mod._tray_icon.icon = main_mod._base_icon_256
                main_mod._has_alert = False
            except Exception as e:
                logger.warning("清除托盘红点失败: %s", e)

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
    """获取运行时托盘图标路径。macOS 保持白色模板图标，其他平台使用彩色图标。"""
    # 采用 PyInstaller 打包后的路径兼容方案
    meipass = getattr(sys, "_MEIPASS", None)
    base_path = meipass if meipass else os.path.dirname(os.path.abspath(__file__))

    icon_name = "icon_white.png" if sys.platform == "darwin" else "icon.png"
    return os.path.join(base_path, "frontend", "icons", icon_name)


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

            if sys.platform == "darwin":
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

            colored_img = source_img.copy()
            _base_icon_256 = colored_img.resize((72, 72), Image.Resampling.LANCZOS)
            return colored_img

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
                            apply_macos_immersive_window(main_mod._window_instance)
                            schedule_macos_drag_region_refresh(
                                main_mod._window_instance
                            )
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
                        config_dict = main_mod._api_instance._config_service.to_dict()
                        config_dict["muteMode"] = main_mod._mute_mode
                        main_mod._api_instance._config_service.save(config_dict)
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
                                f"if(window.syncMuteModeFromTray) window.syncMuteModeFromTray({str(main_mod._mute_mode).lower()}); if(window.refreshConfigFromBackend) window.refreshConfigFromBackend();"
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
                            apply_macos_immersive_window(main_mod._window_instance)
                            schedule_macos_drag_region_refresh(
                                main_mod._window_instance
                            )
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

    try:
        WindowDragStripView = objc.lookUpClass("WindowDragStripView")
    except objc.error:

        class WindowDragStripView(AppKit.NSView):
            def initWithFrame_(self, frame):
                self = super(WindowDragStripView, self).initWithFrame_(frame)
                if self is None:
                    return None

                self.setWantsLayer_(True)
                if self.layer() is not None:
                    self.layer().setBackgroundColor_(
                        AppKit.NSColor.clearColor().CGColor()
                    )

                if hasattr(self, "setAutoresizingMask_"):
                    self.setAutoresizingMask_(
                        AppKit.NSViewWidthSizable | AppKit.NSViewMinYMargin
                    )
                self._exclusion_rects = []

                return self

            def isOpaque(self):
                return False

            def setExclusionRects_(self, rects):
                self._exclusion_rects = list(rects or [])

            def _point_hits_exclusion_rect(self, point):
                rects = getattr(self, "_exclusion_rects", None) or []
                if not rects:
                    return False

                parent_view = self.superview()
                root_point = (
                    self.convertPoint_toView_(point, parent_view)
                    if parent_view is not None
                    else point
                )
                x = float(root_point.x)
                y = float(root_point.y)
                for rect in rects:
                    try:
                        rect_x = float(rect.get("x", 0.0))
                        rect_y = float(rect.get("y", 0.0))
                        rect_w = float(rect.get("width", 0.0))
                        rect_h = float(rect.get("height", 0.0))
                    except Exception:
                        continue

                    if rect_w <= 0 or rect_h <= 0:
                        continue

                    if (
                        rect_x <= x <= rect_x + rect_w
                        and rect_y <= y <= rect_y + rect_h
                    ):
                        return True

                return False

            def hitTest_(self, point):
                bounds = self.bounds()
                if (
                    point.x < 0
                    or point.y < 0
                    or point.x > bounds.size.width
                    or point.y > bounds.size.height
                ):
                    return None
                if self._point_hits_exclusion_rect(point):
                    return None
                return self

            def mouseDownCanMoveWindow(self):
                return True

            def acceptsFirstMouse_(self, event):
                return True

            def mouseDown_(self, event):
                local_point = self.convertPoint_fromView_(
                    event.locationInWindow(), None
                )
                if self._point_hits_exclusion_rect(local_point):
                    return

                try:
                    native_window = self.window()
                    if native_window is not None and hasattr(
                        native_window, "performWindowDragWithEvent_"
                    ):
                        native_window.performWindowDragWithEvent_(event)
                        return
                except Exception:
                    pass

                super(WindowDragStripView, self).mouseDown_(event)


MACOS_DRAG_STRIP_TAG = 24101
MACOS_ROOT_CONTAINER_TAG = 24102
MACOS_DRAG_STRIP_HEIGHT = 5.0
MACOS_DRAG_STRIP_LEADING_INSET = 84.0
MACOS_DRAG_STRIP_TRAILING_RESERVED = 208.0
MACOS_DRAG_STRIP_DUAL_WIDTH = 252.0
MACOS_DRAG_STRIP_LAYOUT_SINGLE = "single"
MACOS_DRAG_STRIP_LAYOUT_DUAL = "dual"
MACOS_DRAG_STRIP_LAYOUT_SETTINGS_SPLIT = "settings_split"


def get_macos_drag_layout_mode(window) -> str:
    layout_mode = (
        str(
            getattr(window, "_macos_drag_layout_mode", MACOS_DRAG_STRIP_LAYOUT_SINGLE)
            or MACOS_DRAG_STRIP_LAYOUT_SINGLE
        )
        .strip()
        .lower()
    )
    if layout_mode in {
        MACOS_DRAG_STRIP_LAYOUT_DUAL,
        MACOS_DRAG_STRIP_LAYOUT_SETTINGS_SPLIT,
    }:
        return layout_mode
    return MACOS_DRAG_STRIP_LAYOUT_SINGLE


def get_macos_frame_view(native_window):
    if native_window is None:
        return None

    content_view = native_window.contentView()
    if content_view is None:
        return None

    return content_view.superview()


def is_macos_webview_view(view):
    if view is None:
        return False

    try:
        class_name = str(view.className())
    except Exception:
        return False

    return "WKWebView" in class_name or "WebKitHost" in class_name


def find_macos_subview_by_tag(parent_view, tag):
    if parent_view is None:
        return None

    for subview in parent_view.subviews() or []:
        if hasattr(subview, "tag") and subview.tag() == tag:
            return subview

    return None


def find_macos_webview_subview(parent_view):
    if parent_view is None:
        return None

    def _walk(view, depth=0):
        if view is None or depth > 12:
            return None

        for subview in view.subviews() or []:
            if is_macos_webview_view(subview):
                return subview

            nested = _walk(subview, depth + 1)
            if nested is not None:
                return nested

        return None

    return _walk(parent_view)


def get_macos_scroll_views(parent_view):
    if parent_view is None:
        return []

    scroll_views = []
    ns_scroll_view_cls = getattr(AppKit, "NSScrollView", None)

    def _walk(view, depth=0):
        if view is None or depth > 12:
            return

        for subview in view.subviews() or []:
            try:
                if ns_scroll_view_cls is not None and subview.isKindOfClass_(
                    ns_scroll_view_cls
                ):
                    scroll_views.append(subview)
            except Exception:
                try:
                    class_name = str(subview.className())
                except Exception:
                    class_name = ""
                if "NSScrollView" in class_name:
                    scroll_views.append(subview)

            _walk(subview, depth + 1)

    _walk(parent_view)
    return scroll_views


def configure_macos_scroll_views(native_window, webview_view=None):
    if not HAS_PYOBJC or sys.platform != "darwin" or native_window is None:
        return

    overlay_style = getattr(AppKit, "NSScrollerStyleOverlay", None)
    no_border = getattr(AppKit, "NSNoBorder", 0)

    parent_view = webview_view or native_window.contentView()
    scroll_views = get_macos_scroll_views(parent_view)
    if not scroll_views and parent_view is not native_window.contentView():
        scroll_views = get_macos_scroll_views(native_window.contentView())

    for scroll_view in scroll_views:
        try:
            if hasattr(scroll_view, "setAutohidesScrollers_"):
                scroll_view.setAutohidesScrollers_(True)
            if overlay_style is not None and hasattr(scroll_view, "setScrollerStyle_"):
                scroll_view.setScrollerStyle_(overlay_style)
            if hasattr(scroll_view, "setBorderType_"):
                scroll_view.setBorderType_(no_border)
            if hasattr(scroll_view, "setDrawsBackground_"):
                scroll_view.setDrawsBackground_(False)
        except Exception as e:
            logger.debug(f"配置 macOS overlay 滚动条失败: {e}")


def force_install_macos_drag_strip(window):
    if not HAS_PYOBJC or sys.platform != "darwin" or window is None:
        return False

    try:
        import webview.platforms.cocoa as cocoa  # type: ignore
    except Exception as e:
        logger.debug(f"加载 Cocoa 后端失败，无法主动重装拖动热区: {e}")
        return False

    installer = getattr(cocoa, "_install_native_top_drag_region", None)
    browser_view_cls = getattr(cocoa, "BrowserView", None)
    if not callable(installer) or browser_view_cls is None:
        return False

    instance = browser_view_cls.get_instance("pywebview_window", window)
    if instance is None:
        native_window = getattr(window, "native", None)
        if native_window is not None:
            instance = browser_view_cls.get_instance("window", native_window)

    native_window = getattr(window, "native", None)
    webview_view = None
    pywebview_window = window

    if instance is not None:
        native_window = getattr(instance, "window", native_window)
        webview_view = getattr(instance, "webview", None)
        pywebview_window = getattr(instance, "pywebview_window", window)

    if native_window is None:
        return False

    if webview_view is None:
        webview_view = find_macos_webview_subview(native_window.contentView())

    if webview_view is None:
        configure_macos_scroll_views(native_window)
        return False

    try:
        installer(native_window, webview_view, pywebview_window)
        configure_macos_scroll_views(native_window, webview_view)
        return True
    except Exception as e:
        logger.debug(f"主动重装 macOS 拖动热区失败: {e}")
        return False


def get_macos_titlebar_container(native_window):
    if native_window is None:
        return None

    content_view = native_window.contentView()
    if content_view is None:
        return None

    frame_view = get_macos_frame_view(native_window)
    if frame_view is None:
        return None

    subviews = frame_view.subviews()
    if subviews is None or len(subviews) <= 0:
        return frame_view

    titlebar_height = 0.0
    if hasattr(native_window, "titlebarHeight"):
        try:
            titlebar_height = float(native_window.titlebarHeight())
        except Exception:
            titlebar_height = 0.0

    fallback_view = frame_view
    for subview in subviews:
        if subview is None or subview == content_view:
            continue

        class_name = ""
        try:
            class_name = str(subview.className())
        except Exception:
            class_name = ""

        if "Titlebar" in class_name or "Toolbar" in class_name:
            return subview

        try:
            subview_height = float(subview.bounds().size.height)
        except Exception:
            subview_height = 0.0

        if (
            titlebar_height > 0
            and 0 < subview_height <= titlebar_height + 18.0
            and fallback_view is frame_view
        ):
            fallback_view = subview

    return fallback_view


def install_macos_drag_strip(window):
    """macOS: 向 pywebview Cocoa 层声明顶部原生拖动带高度。"""
    if not HAS_PYOBJC or sys.platform != "darwin" or window is None:
        return

    try:
        setattr(window, "_macos_top_drag_strip_height", MACOS_DRAG_STRIP_HEIGHT)
        force_install_macos_drag_strip(window)
    except Exception as e:
        logger.warning(f"声明 macOS 拖动热区失败: {e}")


def schedule_macos_drag_region_refresh(window, delays=None):
    """macOS: 在启动后多次重装原生拖动区域，避免被 pywebview 后续层级变更覆盖。"""
    if not HAS_PYOBJC or sys.platform != "darwin" or window is None:
        return

    refresh_delays = delays or (0.15, 0.45, 0.9, 1.6, 2.8)

    def _schedule(delay_seconds):
        def _runner():
            try:
                import time

                time.sleep(delay_seconds)
                apply_macos_immersive_window(main_mod._window_instance)
            except Exception as e:
                logger.warning(f"延迟刷新 macOS 拖动热区失败: {e}")

        threading.Thread(target=_runner, daemon=True).start()

    for delay in refresh_delays:
        _schedule(delay)


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
    main_mod._mute_mode = api._config_service.get("muteMode", False)

    # 初始化未读数量
    try:
        subscribed_sources = api._config_service.get("subscribedSources", None)
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
            subscribed_sources = api._config_service.get("subscribedSources", None)
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
    install_global_exception_hooks(api)

    # 获取前端页面路径
    html_url = get_html_path()

    # 🌟 检测启动参数：如果是开机自启（带了 --minimized 参数），则初始隐藏
    start_minimized = "--minimized" in sys.argv

    ensure_pywebview_cocoa_drag_patch()

    # 创建原生窗口
    # 🌟 沉浸式窗口配置（使用标准窗口，通过原生API配置）
    window = webview.create_window(
        title="Microflow",
        url=html_url,
        js_api=api,
        width=450,
        height=800,
        min_size=(450, 700),
        frameless=False,  # 🌟 使用标准窗口，保留红绿灯
        easy_drag=False,  # 🌟 禁用自动拖拽
        hidden=start_minimized,
    )

    api._window = window

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
                        apply_macos_immersive_window(main_mod._window_instance)
                        schedule_macos_drag_region_refresh(main_mod._window_instance)
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
                    config_dict = main_mod._api_instance._config_service.to_dict()
                    config_dict["muteMode"] = main_mod._mute_mode
                    main_mod._api_instance._config_service.save(config_dict)
                    print(f"勿扰模式: {'开启' if main_mod._mute_mode else '关闭'}")
                except Exception as e:
                    print(f"❌ 保存勿扰设置失败: {e}")
            update_tray_status()

            def notify_frontend():
                try:
                    if main_mod._window_instance:
                        main_mod._window_instance.evaluate_js(
                            f"if(window.syncMuteModeFromTray) window.syncMuteModeFromTray({str(main_mod._mute_mode).lower()}); if(window.refreshConfigFromBackend) window.refreshConfigFromBackend();"
                        )
                except Exception:
                    pass

            threading.Thread(target=notify_frontend, daemon=True).start()

        def on_open_settings(icon, item):
            main_mod = get_main_module()

            def do_open():
                try:
                    if main_mod._window_instance:
                        apply_macos_immersive_window(main_mod._window_instance)
                        schedule_macos_drag_region_refresh(main_mod._window_instance)
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

        # 初始化 pystray 托盘q
        icon_image = load_tray_icon()
        tray_icon = pystray.Icon(
            "MicroFlow", icon_image, "MicroFlow 监控中", create_tray_menu()
        )

        main_module._tray_icon = tray_icon
        main_module._mute_mode = api._config_service.get("muteMode", False)

        try:
            subscribed_sources = api._config_service.get("subscribedSources", None)
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
                subscribed_sources = api._config_service.get("subscribedSources", None)
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
        apply_macos_immersive_window(main_mod._window_instance)
        schedule_macos_drag_region_refresh(window)
        try:
            api.telemetry_service.track(
                "app_launch",
                {
                    "start_minimized": bool(start_minimized),
                    "platform": sys.platform,
                    "has_pyobjc": bool(HAS_PYOBJC),
                    "cold_start_ms": int(max((time.time() - APP_BOOT_TS) * 1000, 0)),
                },
            )
        except Exception:
            pass
        if not start_minimized:
            if window is not None:
                window.restore()
                window.show()

        # 🌟 启动性能监控窗口
        def launch_performance_monitor():
            try:
                import os
                frontend_path = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)),
                    "frontend",
                    "performance-monitor.html"
                )
                perf_window = webview.create_window(
                    title="性能监控",
                    url=frontend_path,
                    js_api=api,
                    width=900,
                    height=700,
                    x=100,
                    y=100,
                )
                logger.info("📊 性能监控窗口已启动")
            except Exception as e:
                logger.warning(f"启动性能监控窗口失败: {e}")

        threading.Thread(target=launch_performance_monitor, daemon=True).start()

    webview.start(func=on_app_start, debug=True, http_server=True)
