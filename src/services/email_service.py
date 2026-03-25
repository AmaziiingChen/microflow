"""邮件发送服务 - 使用 SMTP 发送带嵌入图片的 HTML 邮件"""

import logging
import os
import smtplib
import base64
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from typing import Dict, Any, List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class EmailService:
    """SMTP 邮件发送服务"""

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
        html_body = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:20px;background:#f6f6f8;">
    <div style="max-width:480px;margin:0 auto;">
        <!-- 快照图片 -->
        <img src="cid:article_snapshot" alt="公文快照" style="max-width:100%;display:block;border-radius:16px;">

        <!-- 可点击的查看原文按钮 -->
        <table role="presentation" cellpadding="0" cellspacing="0" border="0" align="center" style="margin-top:20px;">
            <tr>
                <td>
                    <a href="{article_url}" target="_blank" style="display:inline-block;padding:14px 32px;background:#f2ca44;color:#ffffff;text-decoration:none;font-size:15px;font-weight:600;border-radius:24px;box-shadow:0 4px 12px rgba(242,202,68,0.3);">
                        <span style="white-space:nowrap;">查看原文&nbsp;
                            <svg style="display:inline-block;vertical-align:middle;width:18px;height:18px;" viewBox="0 0 21.5635 19.9456" xmlns="http://www.w3.org/2000/svg">
                                <path d="M1.78027 11.1251L8.70898 11.1398C8.80664 11.1398 8.8457 11.1788 8.8457 11.2765L8.85546 18.1759C8.85546 20.1974 11.4629 20.6369 12.3418 18.7081L19.5 3.11244C20.4424 1.04213 18.8652-0.422718 16.8242 0.505016L1.18456 7.6681C-0.656255 8.50306-0.270513 11.1154 1.78027 11.1251Z" fill="currentColor"/>
                            </svg>
                        </span>
                    </a>
                </td>
            </tr>
        </table>

        <!-- 底部提示 -->
        <p style="text-align:center;color:#9ca3af;font-size:12px;margin-top:16px;">
            此邮件由 MicroFlow 自动推送，请勿直接回复
        </p>
    </div>
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

        title = article_data.get("title", "未知标题")
        source_name = article_data.get("source_name", "")
        category = article_data.get("category", "")
        date = article_data.get("date", "")
        summary = article_data.get("summary", "")
        url = article_data.get("url", "")

        # 提取摘要纯文本预览（去除 HTML 标签）
        import re

        summary_text = re.sub(r"<[^>]+>", "", summary)
        summary_preview = summary_text[:200]

        # 邮件主题
        subject = f"【MicroFlow】：{title[:30]}{'...' if len(title) > 30 else ''}"

        sent_count = 0
        failed = []

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

            # 逐个发送
            for to_addr in to_addrs:
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

                    smtp.sendmail(self.smtp_user, to_addr, msg.as_string())
                    sent_count += 1
                    logger.info(f"📧 邮件发送成功: {to_addr}")

                except Exception as e:
                    failed.append({"email": to_addr, "error": str(e)})
                    logger.warning(f"邮件发送失败 ({to_addr}): {e}")

            # 关闭连接
            smtp.quit()

            return {
                "success": sent_count > 0,
                "sent_count": sent_count,
                "failed": failed,
                "message": f"成功发送 {sent_count}/{len(to_addrs)} 封邮件",
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
            msg["To"] = to_addr
            msg["Subject"] = "【MicroFlow】邮件配置测试"

            html_body = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"></head>
<body style="font-family: sans-serif; padding: 20px;">
    <div style="max-width: 500px; margin: 0 auto; padding: 24px; background: #ffffff; border-radius: 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">
        <h1 style="color: #408ff7; margin: 0 0 16px;">✅ 邮件配置成功</h1>
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

            smtp.sendmail(self.smtp_user, to_addr, msg.as_string())
            smtp.quit()

            logger.info(f"📧 测试邮件发送成功: {to_addr}")
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
