#!/usr/bin/env python3
"""
FAC Audit Alert
Monitors the Federal Audit Clearinghouse (fac.gov) for new audits
by state and sends email notifications via Gmail SMTP.
"""

import json
import os
import smtplib
import sys
from datetime import date, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Config (from .env)
# ---------------------------------------------------------------------------
FAC_API_KEY    = os.environ["FAC_API_KEY"]
WATCH_STATE    = os.environ["WATCH_STATE"].upper()          # e.g. "CA"
EMAIL_FROM     = os.environ["EMAIL_FROM"]                   # Gmail address
EMAIL_PASSWORD = os.environ["EMAIL_PASSWORD"]               # Gmail App Password
EMAIL_TO       = os.environ["EMAIL_TO"]                     # recipient(s), comma-separated
SMTP_HOST      = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT      = int(os.getenv("SMTP_PORT", "587"))

STATE_FILE     = Path(os.getenv("STATE_FILE", "last_check.json"))
BASE_URL       = "https://api.fac.gov"
PAGE_SIZE      = 200                                        # max records per request


# ---------------------------------------------------------------------------
# State persistence — tracks the last date we checked
# ---------------------------------------------------------------------------

def load_last_check() -> str:
    """Return the last-checked date string (YYYY-MM-DD), defaulting to yesterday."""
    if STATE_FILE.exists():
        data = json.loads(STATE_FILE.read_text())
        return data.get("last_check_date", _yesterday())
    return _yesterday()


def save_last_check(check_date: str) -> None:
    STATE_FILE.write_text(json.dumps({"last_check_date": check_date}, indent=2))


def _yesterday() -> str:
    return (date.today() - timedelta(days=1)).isoformat()


# ---------------------------------------------------------------------------
# FAC API
# ---------------------------------------------------------------------------

def fetch_new_audits(since_date: str) -> list[dict]:
    """
    Query FAC /general for audits in WATCH_STATE accepted on or after since_date.
    Handles pagination automatically.
    """
    headers = {"X-Api-Key": FAC_API_KEY}
    params = {
        "auditee_state": f"eq.{WATCH_STATE}",
        "fac_accepted_date": f"gte.{since_date}",
        "order": "fac_accepted_date.desc",
        "limit": PAGE_SIZE,
        "offset": 0,
    }

    results = []
    while True:
        resp = requests.get(f"{BASE_URL}/general", headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        page = resp.json()
        if not page:
            break
        results.extend(page)
        if len(page) < PAGE_SIZE:
            break
        params["offset"] += PAGE_SIZE

    return results


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

def build_email(audits: list[dict]) -> MIMEMultipart:
    recipients = [r.strip() for r in EMAIL_TO.split(",")]
    count = len(audits)
    subject = f"[FAC Alert] {count} new audit{'s' if count != 1 else ''} in {WATCH_STATE}"

    # Plain-text body
    lines = [
        f"Federal Audit Clearinghouse — {count} new audit{'s' if count != 1 else ''} for state: {WATCH_STATE}",
        f"Checked at: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "-" * 60,
    ]
    for a in audits:
        lines += [
            f"Auditee:       {a.get('auditee_name', 'N/A')}",
            f"UEI:           {a.get('auditee_uei', 'N/A')}",
            f"Audit Year:    {a.get('audit_year', 'N/A')}",
            f"Period:        {a.get('fy_start_date', 'N/A')} – {a.get('fy_end_date', 'N/A')}",
            f"Accepted Date: {a.get('fac_accepted_date', 'N/A')}",
            f"Audit Type:    {a.get('audit_type', 'N/A')}",
            f"Findings:      {a.get('number_of_findings', 'N/A')}",
            f"Report ID:     {a.get('report_id', 'N/A')}",
            f"FAC URL:       https://app.fac.gov/dissemination/report/pdf/{a.get('report_id', '')}",
            "-" * 60,
        ]

    # HTML body
    rows = ""
    for a in audits:
        report_id = a.get("report_id", "")
        fac_url = f"https://app.fac.gov/dissemination/report/pdf/{report_id}"
        rows += f"""
        <tr>
          <td>{a.get('auditee_name', '')}</td>
          <td>{a.get('auditee_uei', '')}</td>
          <td>{a.get('audit_year', '')}</td>
          <td>{a.get('fac_accepted_date', '')}</td>
          <td>{a.get('audit_type', '')}</td>
          <td>{a.get('number_of_findings', '')}</td>
          <td><a href="{fac_url}">{report_id}</a></td>
        </tr>"""

    html = f"""
    <html><body>
    <h2>FAC Alert &mdash; {count} new audit{'s' if count != 1 else ''} in {WATCH_STATE}</h2>
    <p>Checked at {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
    <table border="1" cellpadding="4" cellspacing="0" style="border-collapse:collapse;font-family:monospace;font-size:13px;">
      <thead style="background:#e0e0e0;">
        <tr>
          <th>Auditee</th><th>UEI</th><th>Year</th>
          <th>Accepted</th><th>Type</th><th>Findings</th><th>Report</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
    </body></html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = EMAIL_FROM
    msg["To"]      = ", ".join(recipients)
    msg.attach(MIMEText("\n".join(lines), "plain"))
    msg.attach(MIMEText(html, "html"))
    return msg, recipients


def send_email(msg: MIMEMultipart, recipients: list[str]) -> None:
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.ehlo()
        server.starttls()
        server.login(EMAIL_FROM, EMAIL_PASSWORD)
        server.sendmail(EMAIL_FROM, recipients, msg.as_string())
    print(f"Email sent to: {', '.join(recipients)}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    last_check = load_last_check()
    today      = date.today().isoformat()

    print(f"Checking FAC for new audits in {WATCH_STATE} since {last_check} ...")

    try:
        audits = fetch_new_audits(last_check)
    except requests.HTTPError as e:
        print(f"API error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(audits)} new audit(s).")

    if audits:
        msg, recipients = build_email(audits)
        send_email(msg, recipients)

    # Only advance the checkpoint after a successful run
    save_last_check(today)
    print(f"Last-check date updated to {today}.")


if __name__ == "__main__":
    main()
