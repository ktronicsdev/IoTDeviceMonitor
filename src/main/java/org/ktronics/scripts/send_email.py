#!/usr/bin/env python3
import os
import sys
import smtplib
from email.message import EmailMessage

def convert_text_to_html(text_body):
    """Convert plain text alert body to formatted HTML."""

    # Check if this is a build verification email (simple format)
    is_build_email = "Workflow:" in text_body and "Status:" in text_body and "Commit:" in text_body

    # Check if this is an "all systems operational" message
    is_operational = "ALL SYSTEMS OPERATIONAL" in text_body

    # Start HTML with inline CSS
    html = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        body {
            font-family: 'Courier New', monospace;
            background-color: #f5f5f5;
            margin: 0;
            padding: 20px;
        }
        .container {
            max-width: 800px;
            margin: 0 auto;
            background-color: white;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            overflow: hidden;
        }
        .header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 30px;
            text-align: center;
        }
        .header.success {
            background: linear-gradient(135deg, #56ab2f 0%, #a8e063 100%);
        }
        .header.alert {
            background: linear-gradient(135deg, #eb3349 0%, #f45c43 100%);
        }
        .header h1 {
            margin: 0;
            font-size: 24px;
            font-weight: bold;
        }
        .status {
            margin-top: 10px;
            font-size: 16px;
            opacity: 0.95;
        }
        .content {
            padding: 30px;
            line-height: 1.6;
        }
        .section {
            margin: 20px 0;
            padding: 20px;
            background-color: #f8f9fa;
            border-left: 4px solid #667eea;
            border-radius: 4px;
        }
        .section.red {
            border-left-color: #eb3349;
            background-color: #fff5f5;
        }
        .section.orange {
            border-left-color: #f39c12;
            background-color: #fffbf0;
        }
        .section.info {
            border-left-color: #3498db;
            background-color: #f0f8ff;
        }
        .alert-item {
            margin: 15px 0;
            padding: 15px;
            background-color: white;
            border-radius: 4px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }
        .alert-title {
            font-weight: bold;
            font-size: 18px;
            margin-bottom: 8px;
        }
        .alert-details {
            color: #555;
            margin: 5px 0;
        }
        .metric {
            display: inline-block;
            padding: 4px 12px;
            background-color: #e9ecef;
            border-radius: 12px;
            margin: 2px;
            font-size: 14px;
        }
        .actions {
            margin-top: 15px;
        }
        .action-item {
            margin: 8px 0;
            padding-left: 20px;
            color: #333;
        }
        .footer {
            padding: 20px 30px;
            background-color: #f8f9fa;
            text-align: center;
            color: #666;
            font-size: 12px;
            border-top: 1px solid #dee2e6;
        }
        .ignored-list {
            list-style: none;
            padding: 0;
        }
        .ignored-list li {
            padding: 8px 0;
            border-bottom: 1px solid #e9ecef;
        }
        .ignored-list li:last-child {
            border-bottom: none;
        }
    </style>
</head>
<body>
"""

    # Parse the text to extract key information
    lines = text_body.split('\n')

    # Handle build verification emails with simple format
    if is_build_email:
        is_success = "Status: SUCCESS" in text_body
        html += '    <div class="container">\n'
        if is_success:
            html += '        <div class="header success">\n'
            html += '            <h1>✅ Build Successful</h1>\n'
        else:
            html += '        <div class="header alert">\n'
            html += '            <h1>❌ Build Failed</h1>\n'

        html += '        </div>\n'
        html += '        <div class="content">\n'
        html += '            <div class="section info">\n'

        for line in lines:
            line = line.strip()
            if line and line != "":
                html += f'                <p style="margin: 5px 0;">{line}</p>\n'

        html += '            </div>\n'
        html += '        </div>\n'
        html += '        <div class="footer">\n'
        html += '            ShineMonitor IoT Device Monitoring System<br>\n'
        html += '            Automated solar panel monitoring with intelligent anomaly detection\n'
        html += '        </div>\n'
        html += '    </div>\n'
        html += '</body>\n'
        html += '</html>\n'
        return html

    # Determine header type and extract status
    if is_operational:
        html += '    <div class="container">\n'
        html += '        <div class="header success">\n'
        html += '            <h1>✓ ALL SYSTEMS OPERATIONAL</h1>\n'

        # Extract date and plant count
        for line in lines:
            if "Date:" in line and "Total Plants Monitored:" in line:
                parts = line.split('|')
                if len(parts) == 2:
                    date_part = parts[0].replace('║', '').strip()
                    plants_part = parts[1].replace('║', '').strip()
                    html += f'            <div class="status">{date_part} | {plants_part}</div>\n'
                break

        html += '        </div>\n'
        html += '        <div class="content">\n'
        html += '            <p style="text-align: center; color: #28a745; font-size: 18px; font-weight: bold;">No alerts detected. All monitored solar plants are operating within normal parameters.</p>\n'

    else:
        html += '    <div class="container">\n'
        html += '        <div class="header alert">\n'
        html += '            <h1>⚠ ATTENTION REQUIRED</h1>\n'

        # Extract status line
        for line in lines:
            if "Status:" in line and ("CRITICAL" in line or "WARNING" in line):
                status = line.replace('║', '').replace('Status:', '').strip()
                html += f'            <div class="status">{status}</div>\n'
                break

        html += '        </div>\n'
        html += '        <div class="content">\n'

        # Parse alert sections
        in_summary = False
        in_actions = False
        in_details = False
        current_plant = None

        for line in lines:
            line_clean = line.replace('│', '').replace('┌', '').replace('└', '').replace('─', '').strip()

            if "ALERT SUMMARY" in line:
                in_summary = True
                html += '            <div class="section red">\n'
                html += '                <h2 style="margin-top: 0;">Alert Summary</h2>\n'
                continue

            elif "RECOMMENDED ACTIONS" in line:
                in_summary = False
                if in_actions:
                    html += '            </div>\n'
                in_actions = True
                html += '            <div class="section orange">\n'
                html += '                <h2 style="margin-top: 0;">Recommended Actions</h2>\n'
                continue

            elif "DETAILED BREAKDOWN" in line:
                in_actions = False
                if in_details:
                    html += '            </div>\n'
                in_details = True
                html += '            <div class="section info">\n'
                html += '                <h2 style="margin-top: 0;">Detailed Breakdown</h2>\n'
                continue

            if in_summary and line_clean:
                if line_clean.startswith('🔴') or line_clean.startswith('🟠'):
                    if current_plant:
                        html += '                </div>\n'
                    html += '                <div class="alert-item">\n'
                    html += f'                    <div class="alert-title">{line_clean}</div>\n'
                    current_plant = line_clean
                elif "Issue:" in line_clean:
                    html += f'                    <div class="alert-details">{line_clean}</div>\n'
                elif "Normal:" in line_clean or "Current:" in line_clean or "kWh" in line_clean:
                    html += f'                    <div class="alert-details">{line_clean}</div>\n'

            elif in_actions and line_clean:
                if ':' in line_clean and not line_clean[0].isdigit():
                    html += f'                <div style="margin-top: 15px; font-weight: bold;">{line_clean}</div>\n'
                    html += '                <div class="actions">\n'
                elif line_clean[0].isdigit() and '.' in line_clean[:3]:
                    html += f'                    <div class="action-item">✓ {line_clean}</div>\n'

            elif in_details and line_clean:
                if line_clean.startswith('🔴') or line_clean.startswith('🟠'):
                    html += f'                <div style="margin-top: 15px; font-weight: bold;">{line_clean}</div>\n'
                elif line_clean:
                    html += f'                <div style="margin-left: 20px; color: #666;">{line_clean}</div>\n'

        if current_plant or in_summary:
            html += '                </div>\n'
            html += '            </div>\n'
        if in_actions:
            html += '                </div>\n'
            html += '            </div>\n'
        if in_details:
            html += '            </div>\n'

    # Handle ignored plants section
    if "IGNORED PLANTS" in text_body:
        html += '            <div class="section info">\n'
        html += '                <h2 style="margin-top: 0;">Additional Information</h2>\n'
        html += '                <p><strong>Ignored Plants (No production for extended period):</strong></p>\n'
        html += '                <ul class="ignored-list">\n'

        in_ignored = False
        for line in lines:
            if "IGNORED PLANTS" in line:
                in_ignored = True
                continue
            if in_ignored and "•" in line:
                plant_name = line.split('•')[1].strip() if '•' in line else line.strip()
                if plant_name:
                    html += f'                    <li><strong>{plant_name}</strong></li>\n'
            elif in_ignored and "Reason:" in line:
                reason = line.replace('│', '').replace('Reason:', '').strip()
                if reason:
                    html += f'                        <div style="color: #666; font-size: 14px; margin-left: 20px;">{reason}</div>\n'
            elif in_ignored and line.strip().startswith('└'):
                break

        html += '                </ul>\n'
        html += '            </div>\n'

    html += '        </div>\n'
    html += '        <div class="footer">\n'
    html += '            ShineMonitor IoT Device Monitoring System<br>\n'
    html += '            Automated solar panel monitoring with intelligent anomaly detection\n'
    html += '        </div>\n'
    html += '    </div>\n'
    html += '</body>\n'
    html += '</html>\n'

    return html

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

    # Convert plain text to HTML
    html_body = convert_text_to_html(body)

    # Create message with both plain text and HTML alternatives
    msg = EmailMessage()
    msg["From"] = smtp_user
    msg["To"] = to_addr
    msg["Subject"] = subject

    # Set plain text as fallback
    msg.set_content(body)

    # Add HTML version
    msg.add_alternative(html_body, subtype='html')

    with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as s:
        s.ehlo()
        s.starttls()
        s.login(smtp_user, smtp_pass)
        s.send_message(msg)

    print("Email sent to", to_addr)
    return 0

if __name__ == "__main__":
    sys.exit(main())
