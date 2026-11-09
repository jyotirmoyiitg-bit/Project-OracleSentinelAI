"""Plain-text email templates for incident notifications."""

from sentinel.persistence.models import Incident

_SEVERITY_LABELS = {"critical": "CRITICAL", "warning": "WARNING", "info": "INFO"}


def render(incident: Incident, target_display_name: str) -> tuple[str, str]:
    """Render `(subject, body)` for an incident notification."""
    label = _SEVERITY_LABELS.get(incident.severity, incident.severity.upper())
    subject = f"[Oracle Sentinel AI] {label}: {target_display_name} — {incident.message or incident.rule_id}"
    body = (
        f"Severity:   {label}\n"
        f"Database:   {target_display_name}\n"
        f"Rule:       {incident.rule_id}\n"
        f"Message:    {incident.message}\n"
        f"First seen: {incident.first_seen.isoformat()}\n"
        f"Last seen:  {incident.last_seen.isoformat()}\n"
        f"Incident:   {incident.id}\n"
        "\n"
        "This is an automated monitoring notification from Oracle Sentinel AI. "
        "Acknowledge or resolve this incident from the Incident Center.\n"
    )
    return subject, body
