"""Courier capability — Ace's courier transitions here (NOT an agent): deliver a message by
the right channel. A thin router over the real delivery capabilities — email (mail),
desktop notification (notify), or phone push (pushover). Inherits their gates honestly
(returns their result).
"""
from __future__ import annotations

import logging

log = logging.getLogger("utah.courier")


def deliver(message: str, *, via: str = "notify", to: str | None = None,
            subject: str = "Utah", priority: int = 0, target: str | None = None,
            mail_send=None, notify_fn=None, push_send=None) -> dict:
    """Deliver *message* via 'email' (needs ``to``), 'push' (phone, via Pushover), or
    'notify' (desktop). Each channel gates itself if creds/perms are missing. Injectable
    for tests. Never raises."""
    if via == "email" and to:
        if mail_send is None:
            from utah import mail
            mail_send = mail.send
        r = mail_send(to, subject, message)
        return {"via": "email", **r}
    if via == "push":
        if push_send is None:
            from utah.integrations import pushover
            push_send = pushover.send
        r = push_send(message, title=subject, priority=priority, target=target)
        return {"via": "push", **r}
    if notify_fn is None:
        from utah.integrations import notify
        notify_fn = notify.notify
    r = notify_fn(message)
    return {"via": "notify", **r}


__all__ = ["deliver"]
