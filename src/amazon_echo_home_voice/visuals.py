"""Small, self-contained Alexa visuals built from the same report as speech."""

from copy import deepcopy
from html import escape
import json


APL_INTERFACE = "Alexa.Presentation.APL"
# The optional Worker relay has a tighter limit than Alexa's response envelope.
MAX_RESPONSE_BYTES = 16384
LONG_REPORT_TEXT = "This report is too long to display. Listen to the spoken answer."
REPORT_TITLES = {
    "battery": "Battery",
    "solar": "Solar power",
    "solar_today": "Solar energy today",
    "alarms": "Alarms",
    "status": "Energy status",
}
STATUS_LABELS = {
    "fresh": "Current data",
    "stale": "Stale data",
    "unavailable": "Data unavailable",
    "unconfigured": "Not configured",
}
STATUS_COLORS = {
    "fresh": "#65D9E8",
    "stale": "#FFD182",
    "unavailable": "#FFD182",
    "unconfigured": "#CAD5E0",
}


# APL 1.0 uses the entire datasource object through the "payload" parameter.
# Keep the document independent of hosted packages, fonts, and image downloads.
DOCUMENT = {
    "type": "APL",
    "version": "1.0",
    "mainTemplate": {
        "parameters": ["payload"],
        "items": [{
            "type": "Frame", "width": "100vw", "height": "100vh",
            "backgroundColor": "#0C1723",
            "item": {
                "type": "ScrollView", "width": "100%", "height": "100%",
                "item": {
                    "type": "Container", "width": "100%",
                    "paddingLeft": "8vw", "paddingRight": "8vw",
                    "paddingTop": "6vh", "paddingBottom": "6vh",
                    "items": [
                        {"type": "Text", "text": "HOME ENERGY", "fontSize": 20,
                         "color": "#65D9E8"},
                        {"type": "Text", "text": "${payload.energy.title}",
                         "fontSize": 36, "fontWeight": "700", "color": "#FFFFFF",
                         "spacing": 8},
                        {"type": "Container", "data": "${payload.energy.reports}",
                         "spacing": 20, "items": [{
                             "type": "Frame", "backgroundColor": "#192B3B",
                             "borderRadius": 12, "spacing": 16,
                             "paddingLeft": 20, "paddingRight": 20,
                             "paddingTop": 20, "paddingBottom": 20,
                             "item": {"type": "Container", "items": [
                                 {"type": "Text", "text": "${data.title}",
                                  "fontSize": 26, "fontWeight": "700", "color": "#FFFFFF"},
                                 {"type": "Text", "text": "${data.status}",
                                  "fontSize": 18, "color": "${data.color}", "spacing": 6},
                                 {"type": "Text", "text": "${data.text}",
                                  "fontSize": 26, "color": "#FFFFFF", "spacing": 12},
                             ]},
                         }]},
                        {"type": "Text", "text": "${payload.energy.footer}",
                         "fontSize": 18, "color": "#CAD5E0", "spacing": 20},
                    ],
                },
            },
        }],
    },
}


def supports_apl(event: dict) -> bool:
    """A screen or an older Display interface alone does not prove APL support."""
    value = event
    for key in ("context", "System", "device", "supportedInterfaces"):
        if not isinstance(value, dict):
            return False
        value = value.get(key)
    return isinstance(value, dict) and isinstance(value.get(APL_INTERFACE), dict)


def _text(value: str) -> str:
    # Report text is data, never an expression. Also escape rich-text entities
    # and expression/resource prefixes so APL cannot reinterpret the content.
    return escape(value, quote=False).replace("$", "&#36;").replace("@", "&#64;")


def _size(response: dict) -> int:
    # Match webhook._respond, including JSON's escaping of non-ASCII characters.
    return len(json.dumps(response, separators=(",", ":")).encode("utf-8"))


def _attach(event: dict, response: dict, *, title: str, reports: list[dict],
            card_text: str, footer: str, keep_screen: bool) -> dict:
    body = response["response"]
    if "card" not in body:
        body["card"] = {"type": "Simple", "title": f"Home Energy: {title}", "content": card_text}
        if _size(response) > MAX_RESPONSE_BYTES:
            body["card"]["content"] = LONG_REPORT_TEXT
    if not supports_apl(event):
        return response

    energy = {"title": _text(title), "reports": reports, "footer": _text(footer)}
    body["directives"] = [{
        "type": "Alexa.Presentation.APL.RenderDocument",
        "token": "home-energy-report",
        "document": deepcopy(DOCUMENT),
        "datasources": {"energy": energy},
    }]
    # Preserve every spoken word and every freshness label. Unusually large
    # escaped text can exceed the relay limit even within the gateway contract.
    # Prefer a complete screen report to a duplicate app card in that case.
    if _size(response) > MAX_RESPONSE_BYTES and body.get("card", {}).get("type") == "Simple":
        del body["card"]
    if _size(response) > MAX_RESPONSE_BYTES:
        for report in reports:
            report["text"] = LONG_REPORT_TEXT
    if _size(response) > MAX_RESPONSE_BYTES:
        # With a maximum-length non-BMP spoken report, the entire visual layout
        # may not fit. The ordinary card still provides a safe fallback.
        del body["directives"]
        if "card" not in body:
            body["card"] = {"type": "Simple", "title": "Home Energy", "content": LONG_REPORT_TEXT}
        return response
    if keep_screen and body.get("shouldEndSession") is True:
        # Omitting this keeps the screen briefly without opening the microphone.
        del body["shouldEndSession"]
    return response


def report_visuals(event: dict, response: dict, name: str, payload: dict) -> dict:
    """Use validated central report text and freshness; never infer metric values."""
    names = ("battery", "solar", "solar_today", "alarms") if name == "status" else (name,)
    reports = []
    for report_name in names:
        report = payload["reports"][report_name]
        reports.append({
            "title": REPORT_TITLES[report_name],
            "status": STATUS_LABELS[report["status"]],
            "color": STATUS_COLORS[report["status"]],
            "text": _text(report["text"]),
        })
    spoken = payload["reports"][name]
    return _attach(
        event, response, title=REPORT_TITLES[name], reports=reports,
        card_text=f"{STATUS_LABELS[spoken['status']]}\n\n{spoken['text']}",
        footer="Snapshot for this request. Ask again to refresh.", keep_screen=True,
    )


def message_visuals(event: dict, response: dict, *, title: str = "Home Energy",
                    status: str = "", keep_screen: bool = True) -> dict:
    """Replace previous readings with guidance or a safe, current error message."""
    text = response["response"]["outputSpeech"]["text"]
    return _attach(
        event, response, title=title,
        reports=[{"title": title, "status": status, "color": "#CAD5E0", "text": _text(text)}],
        card_text=text, footer="", keep_screen=keep_screen,
    )
