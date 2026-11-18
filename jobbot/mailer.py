"""Build and send the application email through Gmail SMTP."""
from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from .config import Config
from .search.base import Lead


class _SafeDict(dict):
    def __missing__(self, key):
        return ""


def email_role(cfg: Config, lead: Lead) -> str:
    """Use the posting's own title when it is clean (job-board APIs); otherwise the matched role."""
    title = (lead.title or "").strip()
    if lead.source != "duckduckgo" and title and len(title) <= 60:
        return title
    return lead.role or cfg.role_titles[0]


def fill(subject: str, body: str, applicant: dict, company: str, role: str, email: str) -> tuple[str, str]:
    """Replace {placeholders} in the subject and body."""
    values = _SafeDict({k: str(v) for k, v in applicant.items()})
    values.update(company=(company or "your organization").strip().rstrip("."), role=role, email=email)
    return subject.format_map(values), body.format_map(values)


def render(cfg: Config, lead: Lead) -> tuple[str, str]:
    return fill(cfg.email_subject, cfg.email_body, cfg.applicant, lead.company,
                email_role(cfg, lead), cfg.gmail_user)


def build_message(cfg: Config, lead: Lead, to_addr: str) -> EmailMessage:
    subject, body = render(cfg, lead)
    if cfg.test_recipient:
        subject = f"[TEST -> {to_addr}] {subject}"
        to_addr = cfg.test_recipient
    msg = EmailMessage()
    msg["From"] = formataddr((cfg.applicant.get("name", ""), cfg.gmail_user))
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain=cfg.gmail_user.split("@")[-1] or None)
    msg.set_content(body)
    if cfg.resume_bytes:
        msg.add_attachment(cfg.resume_bytes, maintype="application", subtype="pdf",
                           filename=f"{cfg.applicant.get('name', 'Resume').replace(' ', '_')}_Resume.pdf")
    return msg


def send(cfg: Config, msg: EmailMessage) -> None:
    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx, timeout=30) as smtp:
        smtp.login(cfg.gmail_user, cfg.gmail_app_password)
        smtp.send_message(msg)
