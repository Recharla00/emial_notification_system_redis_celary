import smtplib
import ssl
from email.message import EmailMessage
from email.utils import format_datetime
from datetime import datetime, timezone

from app.config import get_settings


def send_email(notification):
    settings = get_settings()
    message = EmailMessage()
    message["From"] = str(settings.smtp_from)
    message["To"] = notification.recipient
    message["Subject"] = notification.subject
    message["Date"] = format_datetime(datetime.now(timezone.utc))
    domain = str(settings.smtp_from).split("@", 1)[1]
    message["Message-ID"] = f"<{notification.id}@{domain}>"
    message.set_content(notification.text)
    if notification.html:
        message.add_alternative(notification.html, subtype="html")
    context = ssl.create_default_context()
    if settings.smtp_security == "ssl":
        client = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=30, context=context)
    else:
        client = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30)
    with client:
        if settings.smtp_security == "starttls":
            client.starttls(context=context)
        if settings.smtp_username:
            client.login(settings.smtp_username, settings.smtp_password.get_secret_value())
        client.send_message(message)
