from __future__ import annotations

import logging
from typing import Any

from backend.app.services.envconf import load_env

log = logging.getLogger("ps03.whatsapp")


def _load_config() -> dict[str, str]:
    return load_env()


def _normalize_whatsapp(number: str) -> str:
    raw = number.strip()
    if raw.startswith("whatsapp:"):
        return raw
    if not raw.startswith("+"):
        raw = f"+{raw}"
    return f"whatsapp:{raw}"


def _send_twilio_message(body: str, recipient_override: str | None = None) -> dict[str, Any]:
    """Send a WhatsApp message via Twilio using .env credentials."""
    cfg = _load_config()
    sender = cfg.get("TWILIO_WHATSAPP_FROM", "")
    sid = cfg.get("TWILIO_ACCOUNT_SID", "")
    auth_token = cfg.get("TWILIO_AUTH_TOKEN", "")
    api_key = cfg.get("TWILIO_API_KEY", "")
    api_secret = cfg.get("TWILIO_API_SECRET", "")
    dry_run = cfg.get("DRY_RUN", "false").lower() in ("1", "true", "yes")

    raw_recipients = (
        recipient_override.strip()
        if recipient_override and recipient_override.strip()
        else cfg.get("ALERT_RECIPIENTS", "")
    )
    recipients = [
        _normalize_whatsapp(r)
        for r in raw_recipients.split(",")
        if r.strip()
    ]

    if not recipients:
        return {"sent": False, "error": "No ALERT_RECIPIENTS configured."}
    if not sender or not sid or (not auth_token and not (api_key and api_secret)):
        return {"sent": False, "error": "Twilio WhatsApp credentials missing in .env."}

    if dry_run:
        log.info("WhatsApp DRY_RUN message to %s: %s", recipients, body)
        return {"sent": True, "dry_run": True, "recipients": recipients, "sids": []}

    try:
        from twilio.rest import Client

        client = (
            Client(api_key, api_secret, sid)
            if (api_key and api_secret)
            else Client(sid, auth_token)
        )
        from_wa = _normalize_whatsapp(sender)
        sids: list[str] = []
        for to_wa in recipients:
            msg = client.messages.create(from_=from_wa, to=to_wa, body=body[:1580])
            sids.append(msg.sid)
        return {"sent": True, "dry_run": False, "recipients": recipients, "sids": sids}
    except Exception as exc:
        log.warning("WhatsApp send failed: %s", exc)
        return {"sent": False, "error": str(exc), "recipients": recipients}


_TIER_URGENCY = {
    "URGENT": "need attention right now",
    "REVIEW": "need a review today",
    "WATCH": "are worth keeping an eye on",
    "NORMAL": "look steady",
}

_TIER_ACTION = {
    "URGENT": "Please seek emergency care now or call your clinical team immediately. Don't wait.",
    "REVIEW": "Please contact your clinical team today for a review. Don't wait for your next scheduled visit.",
    "WATCH": "Keep a close eye on how you feel and re-check your vitals soon. Contact your team if anything worsens.",
    "NORMAL": "No action needed right now. Continue your routine monitoring.",
}


def _num(value: float | None) -> str:
    if value is None:
        return "—"
    f = float(value)
    return str(int(f)) if f.is_integer() else f"{f:.1f}"


def _vital_phrase(key: str, value: float | None) -> str | None:
    if value is None:
        return None
    n = _num(value)
    if key == "heart_rate":
        word = "high" if value > 100 else ("low" if value < 60 else "steady")
        return f"your heart rate is {word} at {n} bpm"
    if key == "spo2":
        word = "low" if value < 95 else "on target"
        return f"your oxygen is {word} at {n}%"
    if key == "respiratory_rate":
        word = "fast" if value > 20 else ("slow" if value < 12 else "steady")
        return f"your breathing is {word} at {n}/min"
    if key == "temperature":
        if value >= 38:
            return f"your temperature is up at {n}°C (a fever)"
        if value < 36:
            return f"your temperature is low at {n}°C"
        return f"your temperature is normal at {n}°C"
    return None


def _join_clauses(clauses: list[str]) -> str:
    if len(clauses) == 1:
        return clauses[0]
    return ", ".join(clauses[:-1]) + " and " + clauses[-1]


def format_alert(
    *,
    patient_name: str,
    patient_id: str,
    tier: str,
    news2_total: int | None,
    vitals: dict[str, float | None],
    narrative: str,
    abnormal: list[str] | None = None,
    risk_level: str | None = None,
) -> str:
    """A plain, human care-team note shared by WhatsApp and Telegram."""
    first = (patient_name or "there").split()[0]
    urgency = _TIER_URGENCY.get(tier, _TIER_URGENCY["WATCH"])
    action = _TIER_ACTION.get(tier, _TIER_ACTION["WATCH"])

    keys = ("heart_rate", "spo2", "respiratory_rate", "temperature")
    flagged = [
        p
        for p in (_vital_phrase(k, vitals.get(k)) for k in (abnormal or []) if k in keys)
        if p
    ]
    clauses = flagged or [p for p in (_vital_phrase(k, vitals.get(k)) for k in keys) if p]

    lines = [f"Hi {first}, this is your VitalTwin care team.", ""]
    if clauses:
        lines.append(f"Your latest readings {urgency}: {_join_clauses(clauses)}.")
    if news2_total is not None:
        risk = f" ({risk_level.lower()})" if risk_level else ""
        lines.append(f"Your NEWS2 score is {news2_total}{risk}.")
    lines += ["", action]
    if narrative and narrative.strip():
        lines += ["", narrative.strip()]
    lines += [
        "",
        "If anything feels worse, call for help straight away.",
        "",
        "— Your VitalTwin care team",
        "(Decision support, not a diagnosis.)",
    ]
    return "\n".join(lines)


_REPORT_LEADINS = {
    "How you are doing": "",
    "What needs attention": "A few things to keep an eye on:",
    "What to do next": "What we suggest:",
    "What is going well": "The good news:",
    "Get help now if you have": "Get help straight away if you notice:",
    "My conditions & medications": "Your conditions and medications:",
    "Recent check-in notices": "Recent check-ins:",
    "Your latest numbers": "Your latest numbers:",
    "Immediate concerns": "What needs attention now:",
    "Clinical context": "Background:",
    "Abnormal NEWS2 parameters": "Readings outside the usual range:",
    "Sustained baseline drift": "Changes from the usual baseline:",
    "Recent clinical alerts": "Recent alerts:",
    "Sensor / device log": "Device notes:",
    "Other systems within range": "Everything else looks within range:",
    "Clinician addendum": "Your clinician's notes:",
    "Ward Census Overview": "Ward overview:",
    "Patient Roster": "Patients:",
}

_TABLE_HEADER_FIRST = {"Vital sign", "Parameter", "Patient", "Timestamp (UTC)"}


def _strip_inline(s: str) -> str:
    return s.replace("**", "").replace("__", "").replace("`", "").strip()


def format_report(*, title: str, summary_text: str) -> str:
    """Turn the report markdown into a plain, human-sounding note."""
    intro = "Here's your health summary." if title.startswith("My ") else "Here's the clinical summary."
    out = ["Hello,", "", intro]
    for raw in summary_text.splitlines():
        s = raw.strip()
        if not s:
            continue
        if s.startswith("# "):
            continue
        if s.startswith("_") and s.endswith("_"):
            continue
        if s.startswith(">"):
            continue
        if s.startswith("- **Generated:") or s.startswith("- **Prepared"):
            continue
        if s.startswith("## "):
            t = s[3:].strip()
            lead = _REPORT_LEADINS.get(t)
            if lead is None:
                lead = _REPORT_LEADINS.get(t.split(" (")[0], t + ":")
            if lead:
                out += ["", lead]
            continue
        if s.startswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if all(set(c) <= set("-: ") for c in cells):
                continue
            if not cells or cells[0] in _TABLE_HEADER_FIRST or len(cells) > 3:
                continue
            line = f"{cells[0]}: {cells[1]}" if len(cells) >= 2 else cells[0]
            if len(cells) == 3 and cells[2]:
                line += f" ({cells[2]})"
            out.append(line)
            continue
        if s.startswith("- ") or s.startswith("* "):
            out.append(_strip_inline(s[2:]))
            continue
        out.append(_strip_inline(s))
    out += [
        "",
        "— Your VitalTwin care team",
        "(This summary supports your care team; it isn't a diagnosis.)",
    ]
    text = "\n".join(out)
    if len(text) > 3500:
        text = text[:3450].rstrip() + "\n…"
    return text


def send_whatsapp_alert(
    *,
    patient_name: str,
    patient_id: str,
    tier: str,
    news2_total: int | None,
    vitals: dict[str, float | None],
    narrative: str,
    recipient_override: str | None = None,
    abnormal: list[str] | None = None,
    risk_level: str | None = None,
) -> dict[str, Any]:
    """Send a WhatsApp alert via Twilio using .env credentials."""
    body = format_alert(
        patient_name=patient_name,
        patient_id=patient_id,
        tier=tier,
        news2_total=news2_total,
        vitals=vitals,
        narrative=narrative,
        abnormal=abnormal,
        risk_level=risk_level,
    )
    return _send_twilio_message(body, recipient_override=recipient_override)


def send_whatsapp_report(
    *,
    title: str,
    summary_text: str,
    recipient_override: str | None = None,
) -> dict[str, Any]:
    """Send a formatted clinical/health report summary via WhatsApp."""
    body = format_report(title=title, summary_text=summary_text)
    return _send_twilio_message(body, recipient_override=recipient_override)