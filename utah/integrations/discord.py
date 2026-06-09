"""Discord capability — provision a massively-structured Utah server that MIRRORS
the website (the Black Gold deck) and then surface the join link on the deck.

This is the BYPASS for the hand-built-Discord pain: instead of clicking categories,
channels, roles, topics and permissions into existence one by one (and getting the
bot token / intents / library install wrong), the entire server is declared once as
:data:`BLUEPRINT` and applied IDEMPOTENTLY against the Discord REST API. Run it on an
empty guild and it builds everything; run it again and it only fills the gaps. No
``discord.py`` dependency — raw REST over ``requests`` (already vendored), so nothing
to install and nothing to break.

Doctrine match (same as :mod:`utah.integrations.pushover`):
- Creds live OUTSIDE the repo at ``~/.utah/secrets/discord.json``:
  ``{bot_token, guild_id?, invite_url?}`` (or env ``DISCORD_BOT_TOKEN`` / ``DISCORD_GUILD_ID``).
- Honest gate: no token -> :func:`available` is False and :func:`provision` returns
  ``{gated: True}`` WITHOUT faking anything. Every API failure is documented to the
  failure log.
- The HTTP layer is injectable (``session=`` -> any object with ``.request``) so the
  whole provisioner is unit-testable with zero network and zero real Discord calls.

The structure mirrors the deck's domains (SPINE / LEADS / PROBATE / OUTREACH / MEMORY /
VOICE / ENGINES-TRADING / BRAIN-CHAT / SELF-CODE / AUDIT / EVENT-BUS), so a member who
knows the website knows the server.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field

from utah import failures
from utah.daemon import runtime

log = logging.getLogger("utah.integrations.discord")

SECRET = runtime.UTAH_HOME / "secrets" / "discord.json"
API = "https://discord.com/api/v10"
USER_AGENT = "UtahBot (https://github.com/mthburnsbarber-web/ProjectUtah, 1.0)"

# --- Discord permission bits (only the ones we use) --------------------------
P_VIEW_CHANNEL = 1 << 10
P_SEND_MESSAGES = 1 << 11
P_READ_HISTORY = 1 << 16
P_ADD_REACTIONS = 1 << 6
P_CONNECT = 1 << 20
P_SPEAK = 1 << 21
P_ADMINISTRATOR = 1 << 3
P_MANAGE_CHANNELS = 1 << 4
P_MANAGE_MESSAGES = 1 << 13
P_MENTION_EVERYONE = 1 << 17

# --- Discord channel types ---------------------------------------------------
CH_TEXT = 0
CH_VOICE = 2
CH_CATEGORY = 4
CH_ANNOUNCEMENT = 5
CH_FORUM = 15


class DiscordError(RuntimeError):
    """A Discord REST call failed (non-2xx after retries)."""


# --- the server, declared as data --------------------------------------------
@dataclass(frozen=True)
class Role:
    name: str
    color: int = 0            # 0 = no color (Discord default)
    hoist: bool = False       # show separately in the member list
    permissions: int = 0      # permission bitfield
    mentionable: bool = False


@dataclass(frozen=True)
class Channel:
    name: str
    type: int = CH_TEXT
    topic: str = ""
    readonly: bool = False    # @everyone may view + read history, but not send
    webhook: bool = False     # create a feed webhook the spine can POST to
    private: bool = False      # @everyone cannot even view (operator-only)


@dataclass(frozen=True)
class Category:
    name: str
    channels: tuple[Channel, ...] = ()
    private: bool = False      # operator-only category (hides every child)


@dataclass
class Blueprint:
    name: str = "Project Utah"
    roles: tuple[Role, ...] = ()
    categories: tuple[Category, ...] = ()


# Role names referenced by overwrites below.
R_SOVEREIGN = "Sovereign"
R_OPERATOR = "Operator"
R_ACE = "Ace"
R_INVESTOR = "Investor"
R_MEMBER = "Member"
R_MUTED = "Muted"

#: The whole server, mirroring the Black Gold deck. Order is build order; channel
#: order within a category is display order.
BLUEPRINT = Blueprint(
    name="Project Utah",
    roles=(
        Role(R_SOVEREIGN, color=0xE6B800, hoist=True, permissions=P_ADMINISTRATOR, mentionable=True),
        Role(R_OPERATOR, color=0xF59E0B, hoist=True,
             permissions=(P_MANAGE_CHANNELS | P_MANAGE_MESSAGES | P_MENTION_EVERYONE
                          | P_VIEW_CHANNEL | P_SEND_MESSAGES | P_READ_HISTORY), mentionable=True),
        Role(R_ACE, color=0x10B981, hoist=True,
             permissions=(P_VIEW_CHANNEL | P_SEND_MESSAGES | P_READ_HISTORY
                          | P_MENTION_EVERYONE | P_MANAGE_MESSAGES)),
        Role(R_INVESTOR, color=0x8B5CF6, hoist=True,
             permissions=(P_VIEW_CHANNEL | P_SEND_MESSAGES | P_READ_HISTORY | P_ADD_REACTIONS)),
        Role(R_MEMBER, color=0x3B82F6, hoist=False,
             permissions=(P_VIEW_CHANNEL | P_SEND_MESSAGES | P_READ_HISTORY | P_ADD_REACTIONS)),
        Role(R_MUTED, color=0x6B7280, hoist=False, permissions=0),
    ),
    categories=(
        Category("🏛️ WELCOME", (
            Channel("📜welcome", topic="What Project Utah is and how to read this server. Mirrors the deck's HOW TO READ IT.", readonly=True),
            Channel("📣announcements", type=CH_ANNOUNCEMENT, topic="Official Utah announcements. Read-only.", readonly=True, webhook=True),
            Channel("🚪start-here", topic="New here? React to verify and unlock the server."),
            Channel("🗺️roadmap", topic="Where Utah is going — mirrors 15-roadmap.md.", readonly=True),
            Channel("📐how-to-read-it", topic="The deck legend: every panel, what it means, why it matters.", readonly=True),
        )),
        Category("🧠 THE BRAIN", (
            Channel("💬ask-ace", topic="Ask the grounded brain. Same brain as the deck chat box — answers from memory, says 'I don't know' when it doesn't.", webhook=True),
            Channel("🧩reasoning", topic="The brain's live THINKING stream (deck: THINKING panel).", readonly=True, webhook=True),
            Channel("📚memory", topic="MEMORY ROWS — what Utah has learned and can recall.", readonly=True, webhook=True),
            Channel("🔎knowledge", topic="The KNOWLEDGE corpus: 48 Laws, Think & Grow Rich, Atomic Habits, Cialdini, Greene."),
        )),
        Category("💰 REVENUE", (
            Channel("📈leads", topic="LEADS — no-website small businesses, fresh from the moving frontier. Contact + region.", readonly=True, webhook=True),
            Channel("⚖️probate", topic="PROBATE — statewide filings with heir contact, ARV and property.", readonly=True, webhook=True),
            Channel("📨outreach", topic="OUTREACH — messages sent and replies tracked.", readonly=True, webhook=True),
            Channel("💵ledger", topic="The product LEDGER — counts across every revenue domain.", readonly=True, webhook=True),
        )),
        Category("📊 TRADING", (
            Channel("🔥fires", topic="Engine FIRES — entry, target, stop, the engine that fired (gated on the WealthCharts feed).", readonly=True, webhook=True),
            Channel("🤖engine-roster", topic="The 9-engine roster and per-engine grades (deck: ENGINE ROSTER).", readonly=True),
            Channel("📡wc-feed", topic="WealthCharts feed status (deck: WC FEED). GATED until the feed is live.", readonly=True, webhook=True),
            Channel("💬trading-floor", topic="Community trading chat. Not financial advice."),
        )),
        Category("🦴 SPINE", (
            Channel("❤️heartbeat", topic="BUS HEARTBEAT — the daemon's pulse (deck: BUS HEARTBEAT).", readonly=True, webhook=True),
            Channel("🔌event-bus", topic="BUS EVENTS — the live event stream over the unix socket.", readonly=True, webhook=True),
            Channel("🩺system-status", topic="SYSTEM — cores, load, top processes, runtime (deck: SYSTEM STATUS).", readonly=True, webhook=True),
            Channel("🛡️audit-ledger", topic="AUDIT LEDGER — the durable failure log. Real or empty, never faked.", readonly=True, webhook=True),
        )),
        Category("🛠️ SELF-CODE", (
            Channel("🧬proposals", topic="SELF-CODE proposals — what Ace wants to change (propose-only from the web).", readonly=True, webhook=True),
            Channel("✅merges", topic="Tier-A self-code merges that passed the suite gate and landed on main.", readonly=True, webhook=True),
            Channel("📐utility-scores", topic="Graded UTILITY scores per self-code cycle (deck: UTILITY).", readonly=True),
        )),
        Category("🎙️ VOICE", (
            Channel("🔊voice-log", topic="VOICE — wake / listening / speaking transcript (deck: VOICE panel).", readonly=True, webhook=True),
            Channel("🎧Voice Lounge", type=CH_VOICE, topic="Hang out with voice."),
        )),
        Category("🌐 COMMUNITY", (
            Channel("💬general", topic="General chat for the Utah community."),
            Channel("🤝introductions", topic="Say hi — who you are and what you're building."),
            Channel("🖼️showcase", topic="Show what Utah (or you) shipped."),
            Channel("🆘support", topic="Questions and help."),
            Channel("💡ideas", topic="Feature ideas and feedback for Utah."),
        )),
        Category("🔒 OPERATIONS", (
            Channel("🕹️control-plane", topic="Operator control plane — daemon commands and ops notes.", private=True),
            Channel("📋logs", topic="Verbose operational logs.", private=True, webhook=True),
            Channel("🚨alerts", topic="Critical alerts (same taxonomy as the Pushover critical stream).", private=True, webhook=True),
        ), private=True),
    ),
)


# --- creds -------------------------------------------------------------------
def _load_creds() -> dict | None:
    import os

    creds: dict = {}
    try:
        c = json.loads(SECRET.read_text())
        if isinstance(c, dict):
            creds.update(c)
    except Exception:  # noqa: BLE001 — missing/garbled file is a gate, not a crash
        pass
    # env overrides / fills (deploy seam)
    if os.environ.get("DISCORD_BOT_TOKEN"):
        creds["bot_token"] = os.environ["DISCORD_BOT_TOKEN"]
    if os.environ.get("DISCORD_GUILD_ID"):
        creds["guild_id"] = os.environ["DISCORD_GUILD_ID"]
    return creds or None


def available() -> bool:
    """True when a bot token is present (env or secrets file)."""
    c = _load_creds()
    return bool(c and c.get("bot_token"))


def invite_url() -> str:
    """The configured public invite link, surfaced on the deck. Empty when unset."""
    c = _load_creds() or {}
    return str(c.get("invite_url") or "").strip()


# --- REST client -------------------------------------------------------------
class Discord:
    """Thin idempotent Discord REST v10 client. ``session`` is any object exposing
    ``request(method, url, headers=, json=, timeout=)`` returning a response with
    ``.status_code``, ``.json()``, ``.headers`` and ``.text`` (a ``requests.Session``
    satisfies this; tests inject a fake)."""

    def __init__(self, token: str, session=None):
        self._token = token
        if session is None:
            import requests

            session = requests.Session()
        self._session = session
        self._headers = {
            "Authorization": f"Bot {token}",
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
        }

    def request(self, method: str, path: str, body: dict | None = None, *, _tries: int = 4):
        """One REST call with 429 back-off. Returns parsed JSON (or ``{}`` on 204)."""
        url = path if path.startswith("http") else f"{API}{path}"
        last_exc: Exception | None = None
        for attempt in range(_tries):
            resp = self._session.request(
                method, url, headers=self._headers,
                json=body if body is not None else None, timeout=30,
            )
            code = resp.status_code
            if code == 429:  # rate limited — honor retry_after, then retry
                retry = 1.0
                try:
                    retry = float(resp.json().get("retry_after", 1.0))
                except Exception:  # noqa: BLE001
                    pass
                log.warning("discord 429 on %s %s; sleeping %.2fs", method, path, retry)
                time.sleep(min(retry, 10.0) + 0.1)
                continue
            if code in (500, 502, 503) and attempt < _tries - 1:  # transient — retry
                time.sleep(0.5 * (attempt + 1))
                continue
            if code == 204:
                return {}
            try:
                data = resp.json()
            except Exception:  # noqa: BLE001
                data = {}
            if 200 <= code < 300:
                return data
            last_exc = DiscordError(f"{method} {path} -> {code}: {getattr(resp, 'text', data)!r}")
            break
        if last_exc:
            raise last_exc
        raise DiscordError(f"{method} {path}: exhausted retries")

    # -- identity / guilds --
    def me(self) -> dict:
        return self.request("GET", "/users/@me")

    def my_guilds(self) -> list[dict]:
        return self.request("GET", "/users/@me/guilds") or []

    def create_guild(self, name: str) -> dict:
        """Create a brand-new guild owned by the bot. Only works while the bot is in
        fewer than 10 guilds (Discord rule). Returns the guild object."""
        return self.request("POST", "/guilds", {"name": name})

    # -- roles --
    def roles(self, guild_id: str) -> list[dict]:
        return self.request("GET", f"/guilds/{guild_id}/roles") or []

    def create_role(self, guild_id: str, role: Role) -> dict:
        return self.request("POST", f"/guilds/{guild_id}/roles", {
            "name": role.name, "color": role.color, "hoist": role.hoist,
            "permissions": str(role.permissions), "mentionable": role.mentionable,
        })

    # -- channels --
    def channels(self, guild_id: str) -> list[dict]:
        return self.request("GET", f"/guilds/{guild_id}/channels") or []

    def create_channel(self, guild_id: str, body: dict) -> dict:
        return self.request("POST", f"/guilds/{guild_id}/channels", body)

    def edit_channel(self, channel_id: str, body: dict) -> dict:
        return self.request("PATCH", f"/channels/{channel_id}", body)

    # -- webhooks / messages --
    def webhooks(self, channel_id: str) -> list[dict]:
        return self.request("GET", f"/channels/{channel_id}/webhooks") or []

    def create_webhook(self, channel_id: str, name: str) -> dict:
        return self.request("POST", f"/channels/{channel_id}/webhooks", {"name": name})

    def send_message(self, channel_id: str, content: str, embeds: list | None = None) -> dict:
        body: dict = {"content": content[:2000]}
        if embeds:
            body["embeds"] = embeds
        return self.request("POST", f"/channels/{channel_id}/messages", body)


# --- provisioner -------------------------------------------------------------
@dataclass
class ProvisionReport:
    gated: bool = False
    dry_run: bool = False
    guild_id: str | None = None
    guild_name: str = ""
    created_roles: list[str] = field(default_factory=list)
    existing_roles: list[str] = field(default_factory=list)
    created_categories: list[str] = field(default_factory=list)
    created_channels: list[str] = field(default_factory=list)
    existing_channels: list[str] = field(default_factory=list)
    webhooks: dict[str, str] = field(default_factory=dict)   # channel name -> webhook url
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "gated": self.gated, "dry_run": self.dry_run,
            "guild_id": self.guild_id, "guild_name": self.guild_name,
            "created_roles": self.created_roles, "existing_roles": self.existing_roles,
            "created_categories": self.created_categories,
            "created_channels": self.created_channels, "existing_channels": self.existing_channels,
            "webhooks": self.webhooks, "errors": self.errors,
            "totals": {
                "roles": len(self.created_roles) + len(self.existing_roles),
                "categories": len(self.created_categories),
                "channels": len(self.created_channels) + len(self.existing_channels),
                "webhooks": len(self.webhooks),
            },
        }


def _overwrites(everyone_id: str, role_ids: dict[str, str], ch: Channel) -> list[dict]:
    """Permission overwrites for a channel given the blueprint flags.

    private  -> @everyone can't even view; Operator + Ace can.
    readonly -> @everyone views + reads history but can't send; Ace/Operator can send.
    """
    ow: list[dict] = []
    if ch.private:
        ow.append({"id": everyone_id, "type": 0, "allow": "0", "deny": str(P_VIEW_CHANNEL)})
        for r in (R_OPERATOR, R_ACE, R_SOVEREIGN):
            if r in role_ids:
                ow.append({"id": role_ids[r], "type": 0,
                           "allow": str(P_VIEW_CHANNEL | P_SEND_MESSAGES | P_READ_HISTORY), "deny": "0"})
    elif ch.readonly:
        ow.append({"id": everyone_id, "type": 0,
                   "allow": str(P_VIEW_CHANNEL | P_READ_HISTORY | P_ADD_REACTIONS),
                   "deny": str(P_SEND_MESSAGES)})
        for r in (R_OPERATOR, R_ACE):
            if r in role_ids:
                ow.append({"id": role_ids[r], "type": 0, "allow": str(P_SEND_MESSAGES), "deny": "0"})
    # always keep the Muted role silenced everywhere it can see
    if R_MUTED in role_ids and not ch.private:
        ow.append({"id": role_ids[R_MUTED], "type": 0, "allow": "0",
                   "deny": str(P_SEND_MESSAGES | P_ADD_REACTIONS)})
    return ow


def _norm(name: str) -> str:
    """Discord lowercases + dash-joins text channel names; compare on that basis so
    re-runs match what Discord stored, not what we asked for."""
    return name.strip().lower()


def provision(guild_id: str | None = None, *, create: bool = False,
              dry_run: bool = False, session=None) -> dict:
    """Build (or top up) the Utah server from :data:`BLUEPRINT`, idempotently.

    - ``dry_run`` returns the full plan WITHOUT any network call (works with no token).
    - ``create`` makes a new guild when no ``guild_id`` is known (bot must be in <10).
    - otherwise targets ``guild_id`` (arg -> creds ``guild_id`` -> the bot's sole guild).
    Re-running only creates what's missing and patches topics/overwrites in place.
    """
    rep = ProvisionReport(dry_run=dry_run)

    if dry_run:
        rep.guild_name = BLUEPRINT.name
        rep.existing_roles = []
        rep.created_roles = [r.name for r in BLUEPRINT.roles]
        for cat in BLUEPRINT.categories:
            rep.created_categories.append(cat.name)
            for ch in cat.channels:
                rep.created_channels.append(f"{cat.name} / {ch.name}")
                if ch.webhook:
                    rep.webhooks[ch.name] = "(dry-run)"
        return rep.as_dict()

    creds = _load_creds()
    if not creds or not creds.get("bot_token"):
        failures.record("discord", "gated", "no bot_token in ~/.utah/secrets/discord.json or env")
        rep.gated = True
        return rep.as_dict()

    dc = Discord(creds["bot_token"], session=session)
    guild_id = guild_id or creds.get("guild_id")

    try:
        if not guild_id:
            if create:
                g = dc.create_guild(BLUEPRINT.name)
                guild_id = g["id"]
                log.info("created guild %s (%s)", BLUEPRINT.name, guild_id)
            else:
                gs = dc.my_guilds()
                if len(gs) == 1:
                    guild_id = gs[0]["id"]
                elif not gs:
                    raise DiscordError("bot is in no guild; pass create=True or add the bot to your server")
                else:
                    raise DiscordError(
                        f"bot is in {len(gs)} guilds; set guild_id in secrets to pick one")
        rep.guild_id = str(guild_id)

        _apply(dc, str(guild_id), rep)
    except DiscordError as exc:
        failures.record("discord", "provision_failed", str(exc)[:200])
        rep.errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001 — never crash the caller
        failures.record("discord", "provision_error", f"{type(exc).__name__}: {exc}"[:200])
        rep.errors.append(f"{type(exc).__name__}: {exc}")
    return rep.as_dict()


def _apply(dc: "Discord", guild_id: str, rep: ProvisionReport) -> None:
    """The idempotent body of :func:`provision` (split out so tests can drive it)."""
    # 1) roles
    existing_roles = {r["name"]: r for r in dc.roles(guild_id)}
    role_ids: dict[str, str] = {}
    everyone_id = guild_id  # the @everyone role id == the guild id
    for r in BLUEPRINT.roles:
        if r.name in existing_roles:
            role_ids[r.name] = existing_roles[r.name]["id"]
            rep.existing_roles.append(r.name)
        else:
            made = dc.create_role(guild_id, r)
            role_ids[r.name] = made["id"]
            rep.created_roles.append(r.name)

    # 2) categories + channels (match by normalized name within parent)
    existing = dc.channels(guild_id)
    cats = {c["name"]: c for c in existing if c.get("type") == CH_CATEGORY}
    by_parent: dict[str, dict[str, dict]] = {}
    for c in existing:
        if c.get("type") != CH_CATEGORY:
            by_parent.setdefault(c.get("parent_id") or "", {})[_norm(c["name"])] = c

    for cat in BLUEPRINT.categories:
        cat_ow = _overwrites(everyone_id, role_ids,
                             Channel(cat.name, private=cat.private)) if cat.private else []
        if cat.name in cats:
            cat_id = cats[cat.name]["id"]
        else:
            body = {"name": cat.name, "type": CH_CATEGORY}
            if cat_ow:
                body["permission_overwrites"] = cat_ow
            cat_id = dc.create_channel(guild_id, body)["id"]
            rep.created_categories.append(cat.name)

        present = by_parent.get(cat_id, {})
        for ch in cat.channels:
            ow = _overwrites(everyone_id, role_ids, ch)
            found = present.get(_norm(ch.name))
            if found:
                rep.existing_channels.append(ch.name)
                ch_id = found["id"]
                patch: dict = {}
                if ch.topic and found.get("topic") != ch.topic and ch.type != CH_VOICE:
                    patch["topic"] = ch.topic
                if ow:
                    patch["permission_overwrites"] = ow
                if patch:
                    dc.edit_channel(ch_id, patch)
            else:
                body = {"name": ch.name, "type": ch.type, "parent_id": cat_id}
                if ch.topic and ch.type != CH_VOICE:
                    body["topic"] = ch.topic
                if ow:
                    body["permission_overwrites"] = ow
                ch_id = dc.create_channel(guild_id, body)["id"]
                rep.created_channels.append(ch.name)

            if ch.webhook and ch.type in (CH_TEXT, CH_ANNOUNCEMENT):
                try:
                    hooks = {h["name"]: h for h in dc.webhooks(ch_id)}
                    hook = hooks.get("Utah Feed") or dc.create_webhook(ch_id, "Utah Feed")
                    token = hook.get("token")
                    if token:
                        rep.webhooks[ch.name] = f"{API}/webhooks/{hook['id']}/{token}"
                except DiscordError as exc:
                    rep.errors.append(f"webhook {ch.name}: {exc}")


def post(channel_webhook_url: str, content: str, username: str = "Utah", embeds: list | None = None,
         *, http_post=None) -> bool:
    """Post a message to a channel via its webhook URL (the spine feeds the server
    this way — no gateway connection needed). Returns True on success."""
    payload: dict = {"content": content[:2000], "username": username}
    if embeds:
        payload["embeds"] = embeds
    try:
        if http_post is not None:
            ok = http_post(channel_webhook_url, payload)
            return bool(ok)
        import requests

        r = requests.post(channel_webhook_url, json=payload, timeout=15)
        if r.status_code >= 300:
            failures.record("discord", "webhook_post_failed", f"{r.status_code}: {r.text[:120]}")
            return False
        return True
    except Exception as exc:  # noqa: BLE001
        failures.record("discord", "webhook_post_error", str(exc)[:150])
        return False


def save_webhooks(report: dict) -> None:
    """Persist provisioned webhook URLs to ``~/.utah/secrets/discord_webhooks.json`` so
    the spine feeds can find each channel's feed without re-provisioning."""
    hooks = report.get("webhooks") or {}
    real = {k: v for k, v in hooks.items() if v and v != "(dry-run)"}
    if not real:
        return
    path = runtime.UTAH_HOME / "secrets" / "discord_webhooks.json"
    try:
        existing = json.loads(path.read_text()) if path.exists() else {}
    except Exception:  # noqa: BLE001
        existing = {}
    existing.update(real)
    path.write_text(json.dumps(existing, indent=2))
    try:
        path.chmod(0o600)
    except OSError:
        pass


# --- CLI ---------------------------------------------------------------------
def _main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="python -m utah.integrations.discord",
                                 description="Provision the Utah Discord server from the blueprint.")
    ap.add_argument("command", choices=["provision", "plan", "blueprint", "status"],
                    help="plan/blueprint = dry-run (no token needed); provision = apply; status = creds check")
    ap.add_argument("--create", action="store_true", help="create a new guild if none is known")
    ap.add_argument("--guild", default=None, help="target guild id (overrides creds)")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.command == "status":
        print(json.dumps({"available": available(), "secret": str(SECRET),
                          "secret_exists": SECRET.exists(), "invite_url": invite_url()}, indent=2))
        return 0

    if args.command in ("plan", "blueprint"):
        rep = provision(dry_run=True)
        print(_render_plan(rep))
        return 0

    rep = provision(guild_id=args.guild, create=args.create)
    if rep.get("gated"):
        print("GATED — no bot token. Drop one in", SECRET, "or set DISCORD_BOT_TOKEN, then re-run.")
        print("Plan that WOULD be applied:\n")
        print(_render_plan(provision(dry_run=True)))
        return 2
    save_webhooks(rep)
    print(json.dumps(rep, indent=2))
    return 0 if not rep.get("errors") else 1


def _render_plan(rep: dict) -> str:
    lines = [f"Server: {rep['guild_name']}",
             f"Roles ({rep['totals']['roles']}): " + ", ".join(rep["created_roles"]), ""]
    cur = None
    for entry in rep["created_channels"]:
        cat, ch = entry.split(" / ", 1)
        if cat != cur:
            lines.append(cat)
            cur = cat
        tag = "  📡" if ch in rep["webhooks"] else "  •"
        lines.append(f"{tag} {ch}")
    t = rep["totals"]
    lines += ["", f"Totals: {t['categories']} categories, {t['channels']} channels, "
                  f"{t['roles']} roles, {t['webhooks']} feed webhooks."]
    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    sys.exit(_main())
