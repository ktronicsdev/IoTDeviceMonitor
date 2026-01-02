#!/usr/bin/env python3
import os
import sys
import smtplib
from email.message import EmailMessage

def main():
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASS")

    to_addr = os.getenv("ALERT_TO", "ktronicssolar@gmail.com")

    subject = os.getenv("EMAIL_SUBJECT", "(no subject)")
    body = os.getenv("EMAIL_BODY", "")

    if not smtp_user or not smtp_pass:
        print("Missing SMTP_USER / SMTP_PASS")
        return 2

    msg = EmailMessage()
    msg["From"] = smtp_user
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body)

    with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as s:
        s.ehlo()
        s.starttls()
        s.login(smtp_user, smtp_pass)
        s.send_message(msg)

    print("Email sent to", to_addr)
    return 0

if __name__ == "__main__":
    sys.exit(main())
