"""邮件发送服务 - 使用 SMTP 发送带嵌入图片的 HTML 邮件"""

import logging
import os
import re
import smtplib
import base64
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from typing import Dict, Any, List, Optional
from datetime import datetime
from src.utils.text_cleaner import strip_emoji

logger = logging.getLogger(__name__)


class EmailService:
    """SMTP 邮件发送服务"""

    # 🔐 邮箱地址校验正则常量（HTML5 级别严格校验）
    # 支持子域名、严格限制顶级域名长度（至少2位字母）、过滤非法特殊字符
    EMAIL_REGEX = re.compile(
        r"^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+"
        r"@[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
        r"(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*"
        r"\.[a-zA-Z]{2,}$"
    )
    # RFC 5321 邮箱最大长度
    EMAIL_MAX_LENGTH = 254
    # 免费方案下建议的单次批量发送上限
    MAX_RECIPIENTS_PER_BATCH = 20

    def __init__(
        self, smtp_host: str, smtp_port: int, smtp_user: str, smtp_password: str
    ):
        """
        初始化邮件服务

        Args:
            smtp_host: SMTP 服务器地址
            smtp_port: SMTP 服务器端口
            smtp_user: SMTP 用户名
            smtp_password: SMTP 密码/授权码
        """
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.smtp_user = smtp_user
        self.smtp_password = smtp_password

    @staticmethod
    def _is_valid_email(email: str) -> bool:
        """
        校验单个邮箱地址是否合法

        Args:
            email: 邮箱地址字符串

        Returns:
            bool: 是否合法
        """
        if not email or len(email) > EmailService.EMAIL_MAX_LENGTH:
            return False
        return bool(EmailService.EMAIL_REGEX.match(email))

    @staticmethod
    def _normalize_email(email: str) -> str:
        """
        规范化邮箱地址：去除首尾空格。
        """
        return email.strip() if email else ""

    @staticmethod
    def _chunk_emails(emails: List[str], chunk_size: int) -> List[List[str]]:
        """
        将邮箱列表按指定大小切片。
        """
        if chunk_size <= 0:
            return [emails]
        return [emails[i : i + chunk_size] for i in range(0, len(emails), chunk_size)]

    @staticmethod
    def _filter_valid_emails(emails: List[str]) -> List[str]:
        """
        清洗拦截器：过滤并返回合法的邮箱列表

        Args:
            emails: 原始邮箱列表

        Returns:
            List[str]: 清洗后的合法邮箱列表
        """
        valid_emails = []
        seen = set()
        for email in emails:
            # 剥离首尾空格
            cleaned = EmailService._normalize_email(email)
            # 校验合法性
            if EmailService._is_valid_email(cleaned):
                key = cleaned.lower()
                if key in seen:
                    continue
                seen.add(key)
                valid_emails.append(cleaned)

        # 记录过滤日志
        original_count = len(emails)
        valid_count = len(valid_emails)
        if original_count != valid_count:
            filtered_count = original_count - valid_count
            logger.warning(
                f"📧 邮箱清洗：过滤/去重了 {filtered_count} 个邮箱地址"
            )

        return valid_emails

    def _create_html_email(
        self,
        to_addr: str,
        subject: str,
        title: str,
        source_name: str,
        category: str,
        date: str,
        summary_preview: str,
        image_path: str,
        article_url: str,
    ) -> MIMEMultipart:
        """
        创建带嵌入图片的 HTML 邮件

        Args:
            to_addr: 收件人地址
            subject: 邮件主题
            title: 文章标题
            source_name: 来源名称
            category: 分类
            date: 日期
            summary_preview: 摘要预览文本
            image_path: 快照图片路径
            article_url: 文章原文链接

        Returns:
            MIMEMultipart 邮件对象
        """
        # 创建多部分邮件
        msg = MIMEMultipart("related")
        msg["From"] = self.smtp_user
        msg["To"] = to_addr
        msg["Subject"] = subject

        # 读取图片并嵌入
        with open(image_path, "rb") as f:
            img_data = f.read()

        # 创建图片附件
        image_attachment = MIMEImage(img_data)
        image_attachment.add_header("Content-ID", "<article_snapshot>")
        image_attachment.add_header(
            "Content-Disposition", "inline", filename="snapshot.png"
        )

        # 创建 HTML 正文 - 快照图片 + 可点击按钮
        # 注意：邮件客户端对CSS支持有限，使用 table 布局最可靠
        html_body = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:20px;background:#f6f6f8;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f6f6f8;">
        <tr>
            <td align="center" style="padding:0;">
                <table role="presentation" width="480" cellpadding="0" cellspacing="0" border="0" style="max-width:480px;">
                    <!-- 快照图片 -->
                    <tr>
                        <td align="center" style="padding:0;">
                            <img src="cid:article_snapshot" alt="公文快照" width="480" style="display:block;border-radius:16px;max-width:100%;">
                        </td>
                    </tr>
                    <!-- 查看原文按钮 -->
                    <tr>
                        <td align="center" style="padding-top:20px;">
                            <table role="presentation" cellpadding="0" cellspacing="0" border="0">
                                <tr>
                                    <td style="background:#f2ca44;border-radius:24px;box-shadow:0 4px 12px rgba(242,202,68,0.3);">
                                        <a href="{article_url}" target="_blank" style="display:inline-block;padding:14px 32px;color:#ffffff;text-decoration:none;font-size:15px;font-weight:600;font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif;">
                                            查看原文&nbsp;<img src="data:image/svg+xml;base64,PHN2ZyB2ZXJzaW9uPSIxLjEiIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyIgeG1sbnM6eGxpbms9Imh0dHA6Ly93d3cudzMub3JnLzE5OTkveGxpbmsiIHZpZXdCb3g9IjAgMCAyMS4wNDk0IDE5LjM4MjMiIHdpZHRoPSIxNiIgaGVpZ2h0PSIxNiI+PGc+PHJlY3QgaGVpZ2h0PSIxOS4zODIzIiBvcGFjaXR5PSIwIiB3aWR0aD0iMjEuMDQ5NCIgeD0iMCIgeT0iMCIvPjxwYXRoIGQ9Ik0xLjYwMjMxIDEwLjY1MzNMOC42MDAzNCAxMC42NzMzQzguNzE1NzggMTAuNjczMyA4Ljc1ODM5IDEwLjcxNTkgOC43NTgzOSAxMC44MzEzTDguNzcxNzEgMTcuNzg5NEM4Ljc3MTcxIDE5LjYwNDcgMTEuMDY2MyAyMC4wMDg2IDExLjg2MzUgMTguMjY2NUwxOS4wNDMgMi42OTkyNUMxOS44ODA1IDAuODYzNTUyIDE4LjQ2ODctMC40MDkzMzEgMTYuNjcyOSAwLjQxMTc1OUwxLjA0NTcxIDcuNjAxNTFDLTAuNTgwMDM5IDguMzQyMjYtMC4yNDU4NDIgMTAuNjQzNSAxLjYwMjMxIDEwLjY1MzNaIiBmaWxsPSJ3aGl0ZSIvPjwvZz48L3N2Zz4="" style="display:inline-block;vertical-align:middle;">
                                        </a>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    <!-- 底部提示 -->
                    <tr>
                        <td align="center" style="padding-top:16px;">
                            <p style="color:#9ca3af;font-size:12px;font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif;margin:0;">
                                此邮件由 MicroFlow 自动推送，请勿直接回复
                            </p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>"""

        # 添加 HTML 正文
        html_part = MIMEText(html_body, "html", "utf-8")
        msg.attach(html_part)

        # 添加图片附件
        msg.attach(image_attachment)

        return msg

    def send_article_notification(
        self, to_addrs: List[str], article_data: Dict[str, Any], image_path: str
    ) -> Dict[str, Any]:
        """
        发送文章通知邮件

        Args:
            to_addrs: 收件人地址列表
            article_data: 文章数据
            image_path: 快照图片路径

        Returns:
            {"success": bool, "sent_count": int, "failed": list, "message": str}
        """
        if not to_addrs:
            return {
                "success": False,
                "sent_count": 0,
                "failed": [],
                "message": "收件人列表为空",
            }

        # 🛡️ 清洗拦截器：过滤非法邮箱地址
        valid_addrs = self._filter_valid_emails(to_addrs)
        if not valid_addrs:
            return {
                "success": False,
                "sent_count": 0,
                "failed": to_addrs,
                "message": "所有邮箱地址格式非法，已阻断发送",
            }

        title = article_data.get("title", "未知标题")
        source_name = article_data.get("source_name", "")
        category = article_data.get("category", "")
        date = article_data.get("date", "")
        summary = article_data.get("summary", "")
        url = article_data.get("url", "")

        # 提取摘要纯文本预览（去除 HTML 标签）
        summary_text = re.sub(r"<[^>]+>", "", summary)
        summary_preview = strip_emoji(summary_text[:200])

        # 邮件主题
        subject = f"【MicroFlow】：{title[:30]}{'...' if len(title) > 30 else ''}"

        sent_count = 0
        failed = []
        smtp = None

        def send_single_message(to_addr: str, retries: int = 3) -> bool:
            nonlocal sent_count
            last_error = None
            for retry in range(retries):
                try:
                    msg = self._create_html_email(
                        to_addr=to_addr,
                        subject=subject,
                        title=title,
                        source_name=source_name,
                        category=category,
                        date=date,
                        summary_preview=summary_preview,
                        image_path=image_path,
                        article_url=url,
                    )

                    send_result = smtp.sendmail(
                        self.smtp_user, [to_addr], msg.as_string()
                    )
                    if send_result:
                        raise smtplib.SMTPRecipientsRefused(send_result)
                    sent_count += 1
                    logger.info(f"📧 邮件发送成功: {to_addr}")
                    return True

                except Exception as e:
                    last_error = e
                    if retry < retries - 1:
                        wait_time = retry + 1
                        logger.info(
                            f"📧 邮件发送失败，{wait_time} 秒后重试 ({retry+1}/{retries}): {to_addr}"
                        )
                        time.sleep(wait_time)

            failed.append(
                {
                    "email": to_addr,
                    "error": str(last_error) if last_error else "未知错误",
                }
            )
            logger.warning(f"📧 邮件发送失败 ({to_addr}): {last_error}")
            return False

        def send_batch(batch_addrs: List[str]) -> bool:
            batch_header = "Undisclosed recipients:;"
            msg = self._create_html_email(
                to_addr=batch_header,
                subject=subject,
                title=title,
                source_name=source_name,
                category=category,
                date=date,
                summary_preview=summary_preview,
                image_path=image_path,
                article_url=url,
            )

            send_result = smtp.sendmail(self.smtp_user, batch_addrs, msg.as_string())
            if send_result:
                raise smtplib.SMTPRecipientsRefused(send_result)
            logger.info(
                f"📧 批量邮件发送成功: {len(batch_addrs)} 个收件人 ({', '.join(batch_addrs[:3])}{'...' if len(batch_addrs) > 3 else ''})"
            )
            return True

        try:
            # 创建 SMTP 连接
            if self.smtp_port == 465:
                # SSL 连接
                smtp = smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=30)
            else:
                # 普通连接，后续升级 TLS
                smtp = smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=30)
                smtp.starttls()

            # 登录
            smtp.login(self.smtp_user, self.smtp_password)

            batch_size = self.MAX_RECIPIENTS_PER_BATCH
            batches = self._chunk_emails(valid_addrs, batch_size)
            logger.info(
                f"📧 邮件通知进入批量模式: {len(valid_addrs)} 个收件人，按 {batch_size} 个/批发送，共 {len(batches)} 批"
            )

            try:
                for batch_index, batch in enumerate(batches):
                    if batch_index > 0:
                        # 批次之间留极短间隔，兼顾免费模式稳定性和总体速度
                        time.sleep(0.2)

                    batch_attempts = 3
                    last_error = None
                    batch_sent = False

                    for retry in range(batch_attempts):
                        try:
                            batch_sent = send_batch(batch)
                            sent_count += len(batch)
                            break
                        except Exception as e:
                            last_error = e
                            logger.warning(
                                f"📧 批量发送失败 ({batch_index+1}/{len(batches)}), 重试 {retry+1}/{batch_attempts}: {e}"
                            )
                            if retry < batch_attempts - 1:
                                time.sleep(retry + 1)

                    if batch_sent:
                        continue

                    if len(batch) == 1:
                        failed.append({"email": batch[0], "error": str(last_error)})
                        logger.warning(f"📧 单个收件人发送失败: {batch[0]}")
                        continue

                    logger.warning(
                        f"📧 批量发送多次失败，降级为单发: {len(batch)} 个收件人"
                    )
                    for to_addr in batch:
                        send_single_message(to_addr)
            finally:
                # 关闭连接
                try:
                    smtp.quit()
                except Exception:
                    try:
                        smtp.close()
                    except Exception:
                        pass

            return {
                "success": sent_count > 0,
                "sent_count": sent_count,
                "failed": failed,
                "message": f"成功发送 {sent_count}/{len(valid_addrs)} 个有效邮箱",
            }

        except smtplib.SMTPAuthenticationError:
            error_msg = "SMTP 认证失败，请检查用户名和密码/授权码"
            logger.error(error_msg)
            return {
                "success": False,
                "sent_count": 0,
                "failed": to_addrs,
                "message": error_msg,
            }

        except smtplib.SMTPException as e:
            error_msg = f"SMTP 错误: {e}"
            logger.error(error_msg)
            return {
                "success": False,
                "sent_count": 0,
                "failed": to_addrs,
                "message": error_msg,
            }

        except Exception as e:
            error_msg = f"邮件发送异常: {e}"
            logger.error(error_msg)
            return {
                "success": False,
                "sent_count": 0,
                "failed": to_addrs,
                "message": error_msg,
            }

    def send_test_email(self, to_addr: str) -> Dict[str, Any]:
        """
        发送测试邮件

        Args:
            to_addr: 收件人地址

        Returns:
            {"success": bool, "message": str}
        """
        # 🛡️ 邮箱格式校验
        cleaned_addr = to_addr.strip() if to_addr else ""
        if not self._is_valid_email(cleaned_addr):
            return {
                "success": False,
                "message": "邮箱地址格式非法",
            }

        try:
            if self.smtp_port == 465:
                smtp = smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=30)
            else:
                smtp = smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=30)
                smtp.starttls()

            smtp.login(self.smtp_user, self.smtp_password)

            # 创建简单测试邮件
            msg = MIMEMultipart()
            msg["From"] = self.smtp_user
            msg["To"] = cleaned_addr
            msg["Subject"] = "【MicroFlow】邮件配置测试"

            html_body = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"></head>
<body style="font-family: sans-serif; padding: 20px;">
    <div style="max-width: 500px; margin: 0 auto; padding: 24px; background: #ffffff; border-radius: 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">
        <h1 style="color: #408ff7; margin: 0 0 16px;">邮件配置成功</h1>
        <p style="color: #374151; line-height: 1.6;">
            恭喜！您的 MicroFlow 邮件推送配置已生效。
        </p>
        <p style="color: #6b7280; font-size: 13px; margin-top: 16px;">
            测试时间：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        </p>
    </div>
</body>
</html>"""

            msg.attach(MIMEText(html_body, "html", "utf-8"))

            smtp.sendmail(self.smtp_user, cleaned_addr, msg.as_string())
            smtp.quit()

            logger.info(f"📧 测试邮件发送成功: {cleaned_addr}")
            return {"success": True, "message": "测试邮件发送成功"}

        except smtplib.SMTPAuthenticationError:
            return {
                "success": False,
                "message": "SMTP 认证失败，请检查用户名和密码/授权码",
            }
        except smtplib.SMTPException as e:
            return {"success": False, "message": f"SMTP 错误: {e}"}
        except Exception as e:
            return {"success": False, "message": f"发送失败: {e}"}
