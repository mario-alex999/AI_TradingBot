"""Module 07 background worker: one loop that keeps approvals moving without ever deciding anything.

Each tick (default every 15 s):
1. pull new Module 05 candidates in, and close requests whose candidate Module 05 rejected or expired;
2. expire open requests past their deadline, and send "expiring soon" notices;
3. hand APPROVED requests to Module 09 (final validation first);
4. write Module 07 decisions back to Module 05 (``module05.feedback`` outbox events);
5. dispatch outbox events to the configured webhook (n8n), with retries and a dead-letter state;
6. deliver pending email and Telegram notifications, with retries.

A failure in one step is logged and the others still run. The worker never approves, never executes, never contacts MT5.
"""
from __future__ import annotations

import json
import logging
import smtplib
import ssl
import urllib.request
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any, Callable

from sqlalchemy import or_, select

from aurum.approvals.db_models import Notification, NotificationDelivery, OutboxEvent
from aurum.approvals.service import ApprovalService
from aurum.auth.db_models import User

log = logging.getLogger(__name__)
BACKOFF = (30, 60, 120, 300, 900, 1800)          # seconds between delivery attempts


def backoff(attempts: int) -> timedelta:
    return timedelta(seconds=BACKOFF[min(attempts, len(BACKOFF)) - 1] if attempts else BACKOFF[0])


# ---- notification channels -------------------------------------------------------------------------------------------------
class ChannelNotConfigured(RuntimeError):
    pass


class EmailChannel:
    """SMTP with STARTTLS (or TLS). Requires NOTIFY_EMAIL_ENABLED, SMTP_HOST and SMTP_FROM; the password comes from the
    environment only and is never logged."""
    name = "email"

    def __init__(self, settings: Any, smtp_factory: Callable[..., Any] | None = None):
        self.s = settings
        self.factory = smtp_factory or smtplib.SMTP

    def configured(self) -> bool:
        return bool(getattr(self.s, "notify_email_enabled", False) and self.s.smtp_host and self.s.smtp_from)

    def send(self, to: str | None, subject: str, body: str) -> None:
        if not self.configured():
            raise ChannelNotConfigured("Email is not configured (NOTIFY_EMAIL_ENABLED, SMTP_HOST, SMTP_FROM).")
        if not to:
            raise ChannelNotConfigured("The user has no email address.")
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.s.smtp_from, to, subject
        msg.set_content(body)
        with self.factory(self.s.smtp_host, int(self.s.smtp_port), timeout=20) as smtp:
            if self.s.smtp_use_tls:
                smtp.starttls(context=ssl.create_default_context())
            if self.s.smtp_username and self.s.smtp_password is not None:
                smtp.login(self.s.smtp_username, self.s.smtp_password.get_secret_value())
            smtp.send_message(msg)


class TelegramChannel:
    """Telegram Bot API sendMessage to one configured chat. Messages carry no approve button: Telegram can only inform."""
    name = "telegram"

    def __init__(self, settings: Any, opener: Callable[..., Any] | None = None):
        self.s = settings
        self.opener = opener or urllib.request.urlopen

    def configured(self) -> bool:
        return bool(self.s.telegram_bot_token is not None and self.s.telegram_bot_token.get_secret_value() and self.s.telegram_chat_id)

    def send(self, to: str | None, subject: str, body: str) -> None:
        if not self.configured():
            raise ChannelNotConfigured("Telegram is not configured (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID).")
        url = f"https://api.telegram.org/bot{self.s.telegram_bot_token.get_secret_value()}/sendMessage"
        data = json.dumps({"chat_id": self.s.telegram_chat_id, "text": f"{subject}\n\n{body}"[:4000],
                           "disable_web_page_preview": True}).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        try:
            with self.opener(req, timeout=15) as resp:
                if resp.status >= 300:
                    raise RuntimeError(f"Telegram answered HTTP {resp.status}")
        except Exception as exc:  # noqa: BLE001 - never let the token appear in an error message
            raise RuntimeError(f"Telegram send failed: {type(exc).__name__}") from None


class Worker:
    def __init__(self, service: ApprovalService, settings: Any, *, ideas_feedback: Callable[..., str] | None = None,
                 channels: dict[str, Any] | None = None, http_post: Callable[[str, bytes], int] | None = None):
        self.svc, self.settings = service, settings
        self.channels = channels if channels is not None else {"email": EmailChannel(settings), "telegram": TelegramChannel(settings)}
        self.http_post = http_post or _post
        self.feedback = ideas_feedback or (service.ideas.feedback if service.ideas is not None else None)

    def tick(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name, fn in (("sync", self.svc.sync_module05), ("expiry", self.svc.expire_due), ("handoff", self.svc.handoff_due),
                         ("module05_feedback", self.module05_feedback), ("outbox", self.dispatch_outbox),
                         ("notifications", self.deliver_notifications)):
            try:
                out[name] = fn()
            except Exception as exc:  # noqa: BLE001 - one failing step must not stop the others
                log.exception("Module 07 worker step failed", extra={"step": name})
                out[name] = {"error": f"{type(exc).__name__}: {exc}"[:300]}
        return out

    # ---- Module 05 write-back ------------------------------------------------------------------------------------------
    def module05_feedback(self) -> dict[str, int]:
        """Applies ``module05.feedback`` events through Module 05's own service. Marked delivered either way: a refusal by
        Module 05 (for example its gate now fails) is recorded, and never changes the Module 07 decision."""
        if self.feedback is None:
            return {"applied": 0}
        repo, now = self.svc.repo, self.svc.now()
        done = 0
        with repo.read() as s:
            events = s.execute(select(OutboxEvent).where(OutboxEvent.event_type == "module05.feedback",
                                                         OutboxEvent.status == "pending").order_by(OutboxEvent.seq)
                               .limit(50)).scalars().all()
            items = [(e.event_id, e.payload) for e in events]
        for event_id, p in items:
            with repo.read() as s:
                from aurum.approvals.db_models import TradeIdea
                idea = s.get(TradeIdea, p.get("idea_id"))
                ref = idea.source_ref if idea is not None and idea.source == "module05" else None
            result = "not_applicable"
            if ref:
                note = f"Module 07 {p['decision'].lower()} by {p.get('by')} (approval {p.get('approval_id')})."
                if p.get("reason_code"):
                    note += f" Reason: {p['reason_code']}."
                result = self.feedback(ref, p["decision"], f"module07:{p.get('by')}", note)
            with repo.write() as s:
                e = s.execute(select(OutboxEvent).where(OutboxEvent.event_id == event_id)).scalar_one()
                e.status, e.delivered_at, e.attempts, e.last_error = "delivered", now, e.attempts + 1, None if result == "applied" else result
                repo.audit(s, actor="system", actor_kind="system", action="module05.feedback", entity_type="trade_idea",
                           entity_id=str(p.get("idea_id")), idea_id=p.get("idea_id"), approval_id=p.get("approval_id"), at=now,
                           details={"result": result})
            done += 1
        return {"applied": done}

    # ---- outbox -> webhook ---------------------------------------------------------------------------------------------
    def dispatch_outbox(self, limit: int = 100) -> dict[str, int]:
        """POSTs pending events (except internal module05.feedback) to APPROVALS_EVENTS_WEBHOOK_URL. Without a URL, events
        stay in the outbox for consumers that poll /approvals/events; nothing is marked delivered that was not."""
        url = getattr(self.settings, "approvals_events_webhook_url", None)
        if not url:
            return {"sent": 0, "skipped": "no webhook configured; consumers poll /api/v1/approvals/events"}
        repo, now = self.svc.repo, self.svc.now()
        policy = self.svc.policy_obj()
        sent = failed = 0
        with repo.read() as s:
            rows = s.execute(select(OutboxEvent).where(OutboxEvent.status == "pending",
                                                       OutboxEvent.event_type != "module05.feedback",
                                                       or_(OutboxEvent.next_attempt_at.is_(None), OutboxEvent.next_attempt_at <= now))
                             .order_by(OutboxEvent.seq).limit(limit)).scalars().all()
            batch = [(e.event_id, repo.envelope(e)) for e in rows]
        for event_id, env in batch:
            body = json.dumps({k: v for k, v in env.items() if k != "delivery"}, default=str).encode()
            try:
                code = self.http_post(url, body)
                ok, err = 200 <= code < 300, None if 200 <= code < 300 else f"HTTP {code}"
            except Exception as exc:  # noqa: BLE001
                ok, err = False, f"{type(exc).__name__}"
            with repo.write() as s:
                e = s.execute(select(OutboxEvent).where(OutboxEvent.event_id == event_id)).scalar_one()
                e.attempts += 1
                if ok:
                    e.status, e.delivered_at, e.last_error = "delivered", now, None
                    sent += 1
                else:
                    failed += 1
                    e.last_error = err
                    if e.attempts >= policy.notifications.retry_max_attempts:
                        e.status = "dead"
                    else:
                        e.next_attempt_at = now + backoff(e.attempts)
        return {"sent": sent, "failed": failed}

    # ---- notifications ---------------------------------------------------------------------------------------------------
    def deliver_notifications(self, limit: int = 100) -> dict[str, int]:
        repo, now = self.svc.repo, self.svc.now()
        policy = self.svc.policy_obj()
        sent = failed = skipped = 0
        with repo.read() as s:
            rows = s.execute(select(NotificationDelivery, Notification, User.email)
                             .join(Notification, Notification.id == NotificationDelivery.notification_id)
                             .join(User, User.id == NotificationDelivery.user_id)
                             .where(NotificationDelivery.status == "pending",
                                    or_(NotificationDelivery.next_attempt_at.is_(None), NotificationDelivery.next_attempt_at <= now))
                             .order_by(NotificationDelivery.id).limit(limit)).all()
            work = [(d.id, d.channel, n.event_type, n.priority, n.title, n.message, email) for d, n, email in rows]
        for did, channel, etype, priority, title, message, email in work:
            ch = self.channels.get(channel)
            status, err = "sent", None
            email_events = policy.notifications.email_events
            if channel == "email" and email_events and etype not in email_events and priority not in ("critical",):
                status, err = "skipped", "Event type not selected for email in the policy."
            elif ch is None or not ch.configured():
                status, err = "not_configured", f"{channel} is not configured on the server."
            else:
                try:
                    ch.send(email, f"[AURUM] {title}", f"{message}\n\nOpen AURUM to review. This message cannot approve anything.")
                except ChannelNotConfigured as exc:
                    status, err = "not_configured", str(exc)
                except Exception as exc:  # noqa: BLE001
                    status, err = "failed", f"{type(exc).__name__}: {exc}"[:300]
            with repo.write() as s:
                d = s.get(NotificationDelivery, did)
                d.attempts += 1
                d.last_error = err
                if status == "sent":
                    d.status, d.sent_at = "sent", now
                    sent += 1
                elif status == "failed":
                    failed += 1
                    if d.attempts >= policy.notifications.retry_max_attempts:
                        d.status = "dead"
                    else:
                        d.next_attempt_at = now + backoff(d.attempts)
                else:
                    d.status = status
                    skipped += 1
        return {"sent": sent, "failed": failed, "skipped": skipped}


def _post(url: str, body: bytes) -> int:
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", "User-Agent": "aurum-module07"})
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - URL comes from server configuration
        return int(resp.status)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
