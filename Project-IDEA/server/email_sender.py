import os
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage


@dataclass(frozen=True)
class EmailDelivery:
    delivered: bool
    development_code: str | None = None


class EmailSender:
    def __init__(self):
        self.host = os.getenv("IDEA_SMTP_HOST", "").strip()
        self.port = int(os.getenv("IDEA_SMTP_PORT", "587"))
        self.username = os.getenv("IDEA_SMTP_USERNAME", "").strip()
        self.password = os.getenv("IDEA_SMTP_PASSWORD", "")
        self.sender = os.getenv("IDEA_SMTP_FROM", "").strip()
        self.use_ssl = os.getenv("IDEA_SMTP_USE_SSL", "false").strip().lower() == "true"
        self.use_starttls = os.getenv("IDEA_SMTP_USE_STARTTLS", "false").strip().lower() == "true"
        self.development_mode = os.getenv("IDEA_AUTH_DEVELOPMENT_MODE", "false").strip().lower() == "true"

    def send_verification_code(self, recipient: str, code: str, purpose: str) -> EmailDelivery:
        if self.development_mode:
            return EmailDelivery(delivered=True, development_code=code)
        if not self.host or not self.sender:
            raise RuntimeError("邮件服务尚未配置 SMTP 出口")
        message = EmailMessage()
        message["Subject"] = "Project World 登录验证码"
        message["From"] = self.sender
        message["To"] = recipient
        message.set_content(
            f"你的 Project World 验证码是：{code}\n\n"
            "验证码将在 10 分钟后失效。如非本人操作，请忽略此邮件。"
        )
        if self.use_ssl:
            with smtplib.SMTP_SSL(self.host, self.port, timeout=15) as client:
                if self.username:
                    client.login(self.username, self.password)
                client.send_message(message)
        else:
            with smtplib.SMTP(self.host, self.port, timeout=15) as client:
                if self.use_starttls:
                    client.starttls()
                if self.username:
                    client.login(self.username, self.password)
                client.send_message(message)
        return EmailDelivery(delivered=True)
