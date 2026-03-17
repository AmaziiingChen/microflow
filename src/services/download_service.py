"""文件下载服务 - 负责附件下载和快照保存"""

import re
import base64
import logging
from typing import Optional, Dict, Any

import requests
import webview
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


class DownloadService:
    """文件下载服务 - 单一职责：文件下载与保存"""

    # Content-Type 到文件扩展名的映射
    CONTENT_TYPE_EXTENSIONS = {
        'application/pdf': '.pdf',
        'msword': '.docx',
        'officedocument.wordprocessingml': '.docx',
        'ms-excel': '.xlsx',
        'officedocument.spreadsheetml': '.xlsx',
    }

    # 默认 Referer
    DEFAULT_REFERER = "https://nbw.sztu.edu.cn/"

    def __init__(self):
        self._session = requests.Session()
        self._setup_session()

    def _setup_session(self) -> None:
        """配置共享 Session 的重试策略和默认请求头"""
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)
        self._session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        })

    def download_attachment(
        self,
        url: str,
        filename: str,
        referer: str = DEFAULT_REFERER
    ) -> Dict[str, Any]:
        """
        下载附件（支持后缀智能补全）

        Args:
            url: 附件下载链接
            filename: 建议的文件名
            referer: Referer 请求头

        Returns:
            {"status": "success/cancelled/error", "message": ...}
        """
        try:
            # 1. 清理文件名中的非法字符
            safe_filename = re.sub(r'[\\/*?:"<>|]', "_", filename).strip()

            # 2. 弹出保存对话框
            save_path = webview.windows[0].create_file_dialog(
                webview.FileDialog.SAVE,
                directory='',
                save_filename=safe_filename
            )

            if not save_path:
                return {"status": "cancelled"}

            # 处理返回值（可能是列表或单个值）
            temp_path = save_path[0] if isinstance(save_path, (list, tuple)) else save_path
            target_path = str(temp_path)

            if not target_path or target_path.lower() == 'none':
                return {"status": "cancelled"}

            logger.info(f"开始下载附件至: {target_path}")

            # 3. 发起下载请求
            headers = {
                "Referer": referer,
                "Accept-Language": "zh-CN,zh;q=0.9"
            }

            response = self._session.get(
                url, stream=True, timeout=30, headers=headers, verify=False
            )

            # 4. 检测是否被服务器拦截（返回 HTML 页面）
            content_type = response.headers.get('Content-Type', '').lower()
            if 'text/html' in content_type:
                return {"status": "error", "message": "服务器拦截下载"}

            # 5. 智能后缀补全
            target_path = self._auto_append_extension(target_path, content_type)

            # 6. 写入文件
            with open(target_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)

            return {"status": "success", "message": "下载完成！"}

        except Exception as e:
            logger.error(f"附件下载失败: {e}")
            return {"status": "error", "message": str(e)}

    def _auto_append_extension(self, path: str, content_type: str) -> str:
        """
        根据 Content-Type 自动补全文件后缀

        Args:
            path: 原始保存路径
            content_type: HTTP 响应的 Content-Type

        Returns:
            可能补全后的路径
        """
        path_lower = path.lower()

        for type_pattern, ext in self.CONTENT_TYPE_EXTENSIONS.items():
            if type_pattern in content_type and not path_lower.endswith(ext):
                new_path = path + ext
                logger.info(f"检测到 {ext.upper()} 类型，自动补全后缀: {new_path}")
                return new_path

        return path

    def save_snapshot(self, b64_data: str, title: str) -> Dict[str, Any]:
        """
        保存快照图片

        Args:
            b64_data: Base64 编码的图片数据（可能包含 data:image/png;base64, 前缀）
            title: 文章标题（用于生成文件名）

        Returns:
            {"status": "success/cancelled/error", "message": ...}
        """
        try:
            # 1. 生成安全的文件名（替换非法字符，不主动截断）
            safe_title = re.sub(r'[\\/*?:"<>|]', "_", title).strip()

            # 2. 文件名长度限制（考虑前缀 "公文快照_" 和后缀 ".png"，共 11 字符）
            # 文件系统最大限制约 255 字符，保留 245 字符给标题
            MAX_FILENAME_LENGTH = 245
            if len(safe_title) > MAX_FILENAME_LENGTH:
                safe_title = safe_title[:MAX_FILENAME_LENGTH]
                logger.info(f"文件名过长，已截断至 {MAX_FILENAME_LENGTH} 字符")

            filename = f"快照_{safe_title}.png"

            # 弹出保存对话框
            save_path = webview.windows[0].create_file_dialog(
                webview.FileDialog.SAVE,
                directory='',
                save_filename=filename
            )

            if not save_path:
                return {"status": "cancelled"}

            temp_path = save_path[0] if isinstance(save_path, (list, tuple)) else save_path
            target_path = str(temp_path)

            if not target_path or target_path.lower() == 'none':
                return {"status": "cancelled"}

            # 3. 解析 Base64 数据
            if "," in b64_data:
                _, encoded = b64_data.split(",", 1)
                image_data = base64.b64decode(encoded)
                with open(target_path, "wb") as f:
                    f.write(image_data)
                return {"status": "success", "message": "快照保存成功！"}
            else:
                return {"status": "error", "message": "图片数据格式异常"}

        except Exception as e:
            logger.error(f"快照保存失败: {e}")
            return {"status": "error", "message": str(e)}