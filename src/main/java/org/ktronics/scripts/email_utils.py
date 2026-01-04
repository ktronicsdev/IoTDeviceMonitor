#!/usr/bin/env python3
"""
Shared email utilities for formatting and sending emails.
Used by both send_email.py and send_customer_emails.py.
"""

import os
import smtplib
from email.message import EmailMessage


def get_smtp_config():
    """Get SMTP configuration from environment variables."""
    return {
        'host': os.getenv("SMTP_HOST", "smtp.gmail.com"),
        'port': int(os.getenv("SMTP_PORT", "587")),
        'user': os.getenv("SMTP_USER"),
        'pass': os.getenv("SMTP_PASS")
    }


def create_html_template(content_html, header_class='success', title=''):
    """
    Create HTML email template with inline CSS.

    Args:
        content_html: HTML content to insert into body
        header_class: CSS class for header (success, alert, weekly)
        title: Header title text
    """
    return f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        body {{
            font-family: 'Courier New', monospace;
            background-color: #f5f5f5;
            margin: 0;
            padding: 20px;
        }}
        .container {{
            max-width: 800px;
            margin: 0 auto;
            background-color: white;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            overflow: hidden;
        }}
        .header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 30px;
            text-align: center;
        }}
        .header.success {{
            background: linear-gradient(135deg, #56ab2f 0%, #a8e063 100%);
        }}
        .header.weekly {{
            background: linear-gradient(135deg, #3498db 0%, #2980b9 100%);
        }}
        .header.alert {{
            background: linear-gradient(135deg, #eb3349 0%, #f45c43 100%);
        }}
        .header h1 {{
            margin: 0;
            font-size: 24px;
            font-weight: bold;
        }}
        .status {{
            margin-top: 10px;
            font-size: 16px;
            opacity: 0.95;
        }}
        .content {{
            padding: 30px;
            line-height: 1.6;
        }}
        .section {{
            margin: 20px 0;
            padding: 20px;
            background-color: #f8f9fa;
            border-left: 4px solid #667eea;
            border-radius: 4px;
        }}
        .section.red {{
            border-left-color: #eb3349;
            background-color: #fff5f5;
        }}
        .section.orange {{
            border-left-color: #f39c12;
            background-color: #fffbf0;
        }}
        .section.info {{
            border-left-color: #3498db;
            background-color: #f0f8ff;
        }}
        .alert-item {{
            margin: 15px 0;
            padding: 15px;
            background-color: white;
            border-radius: 4px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }}
        .alert-title {{
            font-weight: bold;
            font-size: 18px;
            margin-bottom: 8px;
        }}
        .alert-details {{
            color: #555;
            margin: 5px 0;
        }}
        .metric {{
            display: inline-block;
            padding: 4px 12px;
            background-color: #e9ecef;
            border-radius: 12px;
            margin: 2px;
            font-size: 14px;
        }}
        .actions {{
            margin-top: 15px;
        }}
        .action-item {{
            margin: 8px 0;
            padding-left: 20px;
            color: #333;
        }}
        .footer {{
            padding: 20px 30px;
            background-color: #f8f9fa;
            text-align: center;
            color: #666;
            font-size: 12px;
            border-top: 1px solid #dee2e6;
        }}
        .ignored-list {{
            list-style: none;
            padding: 0;
        }}
        .ignored-list li {{
            padding: 8px 0;
            border-bottom: 1px solid #e9ecef;
        }}
        .ignored-list li:last-child {{
            border-bottom: none;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header {header_class}">
            <h1>{title}</h1>
        </div>
        <div class="content">
{content_html}
        </div>
        <div class="footer">
            ShineMonitor IoT Device Monitoring System<br>
            Automated solar panel monitoring with intelligent anomaly detection<br>
            <br>
            For support: ktronicssolar@gmail.com
        </div>
    </div>
</body>
</html>
"""


def convert_plain_text_to_simple_html(text_body):
    """
    Convert plain text to simple HTML wrapped in styled container.
    Used for build emails and simple text messages.
    """
    # Detect message type
    is_build_email = "Workflow:" in text_body and "Status:" in text_body and "Commit:" in text_body
    is_weekly = "WEEKLY SOLAR PRODUCTION REPORT" in text_body
    is_operational = "ALL SYSTEMS OPERATIONAL" in text_body

    # Determine header style
    if is_build_email:
        is_success = "Status: SUCCESS" in text_body
        header_class = 'success' if is_success else 'alert'
        title = '✅ Build Successful' if is_success else '❌ Build Failed'
    elif is_weekly:
        header_class = 'weekly'
        title = '📊 Weekly Solar Production Report'
    elif is_operational:
        header_class = 'success'
        title = '☀️ All Systems Operational'
    else:
        header_class = 'alert'
        title = '⚠️ Solar System Alert'

    # Wrap text in pre tag
    content_html = f'''            <div class="section info">
                <pre style="font-family: 'Courier New', monospace; white-space: pre-wrap; margin: 0;">
{text_body}
                </pre>
            </div>'''

    return create_html_template(content_html, header_class, title)


def send_email_smtp(to_addr, subject, body_text, body_html=None):
    """
    Send email via SMTP with both plain text and HTML versions.

    Args:
        to_addr: Recipient email address
        subject: Email subject
        body_text: Plain text version
        body_html: HTML version (optional, will be auto-generated if None)

    Returns:
        True if sent successfully, False otherwise
    """
    smtp_config = get_smtp_config()

    if not smtp_config['user'] or not smtp_config['pass']:
        print(f"Error: Missing SMTP credentials")
        return False

    try:
        # Generate HTML if not provided
        if body_html is None:
            body_html = convert_plain_text_to_simple_html(body_text)

        # Create message
        msg = EmailMessage()
        msg["From"] = smtp_config['user']
        msg["To"] = to_addr
        msg["Subject"] = subject

        # Set plain text as fallback
        msg.set_content(body_text)

        # Add HTML version
        msg.add_alternative(body_html, subtype='html')

        # Send
        with smtplib.SMTP(smtp_config['host'], smtp_config['port'], timeout=30) as s:
            s.ehlo()
            s.starttls()
            s.login(smtp_config['user'], smtp_config['pass'])
            s.send_message(msg)

        print(f"Email sent to {to_addr}: {subject}")
        return True

    except Exception as e:
        print(f"Failed to send email to {to_addr}: {e}")
        return False
