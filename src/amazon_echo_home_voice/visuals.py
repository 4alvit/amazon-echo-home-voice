"""Small, self-contained Alexa visuals built from the same report as speech."""

from copy import deepcopy
from html import escape
import json
import math

from .gateway import STATUSES


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
    "flow": "Power flow",
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
METRICS = {
    "battery": ("battery_soc", "%", "Battery charge"),
    "solar": ("solar_power", "W", "Solar power"),
    "solar_today": ("solar_today", "kWh", "Solar energy today"),
    "load_power": ("load_power", "W", "Configured AC consumption"),
    "grid_power": ("grid_power", "W", "Grid: + import / - export"),
    "battery_power": ("battery_power", "W", "Battery: + charging / - discharging"),
}
ACTIONS = {"refresh": "Refresh", "battery": "Battery", "today": "Today", "details": "Details"}
TOKEN_PREFIX = "home-energy:v1:"


def _button(action):
    return {
        "type": "TouchWrapper", "id": "energy_" + action, "width": "48%", "spacing": 8,
        "onPress": {"type": "SendEvent", "arguments": [action]},
        "item": {"type": "Frame", "backgroundColor": "#29475E", "borderRadius": 8,
                 "paddingLeft": 16, "paddingRight": 16, "paddingTop": 12, "paddingBottom": 12,
                 "item": {"type": "Text", "text": ACTIONS[action], "fontSize": 22,
                          "color": "#FFFFFF", "textAlign": "center"}},
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
                        {"type": "Container", "when": "${payload.energy.interactive}",
                         "spacing": 16, "items": [
                             {"type": "Container", "direction": "row",
                              "items": [_button("refresh"), _button("battery")]},
                             {"type": "Container", "direction": "row", "spacing": 8,
                              "items": [_button("today"), _button("details")]},
                         ]},
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
                                 {"type": "Text", "when": "${data.value != ''}",
                                  "text": "${data.value}", "fontSize": 52,
                                  "fontWeight": "700", "color": "#FFFFFF", "spacing": 8},
                                 {"type": "Text", "when": "${data.age != ''}",
                                  "text": "${data.age}", "fontSize": 18,
                                  "color": "#CAD5E0", "spacing": 6},
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


def user_event_selection(event: dict) -> tuple[str, bool]:
    """Allow only this document's read actions; authorization is still mandatory."""
    request = event["request"]
    arguments, source, token = request.get("arguments"), request.get("source"), request.get("token")
    if (
        not supports_apl(event) or not isinstance(arguments, list) or len(arguments) != 1
        or not isinstance(arguments[0], str) or arguments[0] not in ACTIONS
        or not isinstance(source, dict) or source.get("type") != "TouchWrapper"
        or source.get("handler") != "Press" or source.get("id") != "energy_" + arguments[0]
        or not isinstance(token, str) or not token.startswith(TOKEN_PREFIX)
    ):
        raise PermissionError("Invalid Alexa screen action")
    parts = token[len(TOKEN_PREFIX):].split(":")
    if len(parts) != 2 or parts[0] not in REPORT_TITLES or parts[1] not in {"brief", "full"}:
        raise PermissionError("Invalid Alexa screen context")
    report, mode = parts
    action = arguments[0]
    if action in {"battery", "today"}:
        return ("battery" if action == "battery" else "solar_today"), False
    return report, action == "details" or mode == "full"


def _metric(payload, name):
    """Optional structured fields must be internally valid before screen use."""
    metric_name, unit, _ = METRICS[name]
    metric = payload["metrics"].get(metric_name)
    if not isinstance(metric, dict):
        return None
    status, value, age = metric.get("status"), metric.get("value"), metric.get("age_seconds")
    if not isinstance(status, str) or status not in STATUSES or metric.get("unit") != unit:
        return None
    if name in payload["reports"] and payload["reports"][name]["status"] != status:
        return None
    if age is not None and (type(age) is not int or not 0 <= age <= 2**64 - 1):
        return None
    if status == "fresh":
        try:
            finite_number = type(value) in (int, float) and math.isfinite(value)
        except OverflowError:
            finite_number = False
        if (
            not payload["mqtt_connected"] or not finite_number or age is None
            or (metric_name not in {"grid_power", "battery_power"} and value < 0)
            or (unit == "%" and value > 100)
        ):
            return None
    elif value is not None:
        # Never display stale or unavailable values as readings.
        return None
    return {"status": status, "value": value, "age_seconds": age, "unit": unit}


def _metric_text(metric):
    if metric is None:
        return "", ""
    age = metric["age_seconds"]
    if age is None:
        age_text = "Source receipt age unavailable"
    elif age < 60:
        age_text = f"Source received {age} seconds before this snapshot"
    elif age < 3600:
        age_text = f"Source received {age // 60} minutes before this snapshot"
    elif age < 86400:
        age_text = f"Source received {age // 3600} hours before this snapshot"
    else:
        age_text = f"Source received {age // 86400} days before this snapshot"
    value = metric["value"]
    if value is None:
        return "", age_text
    # Formatting only: thresholds, aggregation, freshness and sign semantics are IGW's.
    unit = metric["unit"]
    if unit == "W" and abs(value) >= 1000:
        value, unit = value / 1000, "kW"
    if value == 0:
        number = "0"
    elif 0.01 <= abs(value) < 1_000_000_000:
        number = format(value, ",.2f").rstrip("0").rstrip(".")
    else:
        number = format(value, ".4g")
    return f"{number} {unit}", age_text


def _text(value: str) -> str:
    # Report text is data, never an expression. Also escape rich-text entities
    # and expression/resource prefixes so APL cannot reinterpret the content.
    return escape(value, quote=False).replace("$", "&#36;").replace("@", "&#64;")


def _size(response: dict) -> int:
    # Match webhook._respond, including JSON's escaping of non-ASCII characters.
    return len(json.dumps(response, separators=(",", ":")).encode("utf-8"))


def _attach(event: dict, response: dict, *, title: str, reports: list[dict],
            card_text: str, footer: str, keep_screen: bool, token: str = "home-energy-message") -> dict:
    body = response["response"]
    if "card" not in body:
        body["card"] = {"type": "Simple", "title": f"Home Energy: {title}", "content": card_text}
        if _size(response) > MAX_RESPONSE_BYTES:
            body["card"]["content"] = LONG_REPORT_TEXT
    if not supports_apl(event):
        return response

    energy = {"title": _text(title), "reports": reports, "footer": _text(footer),
              "interactive": token.startswith(TOKEN_PREFIX)}
    body["directives"] = [{
        "type": "Alexa.Presentation.APL.RenderDocument",
        "token": token,
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


def report_visuals(event: dict, response: dict, name: str, payload: dict, *, detailed=False) -> dict:
    """Use central speech plus validated optional metrics; never infer missing data."""
    names = ("battery", "solar", "solar_today", "alarms") if name == "status" else (name,)
    reports = []
    for report_name in names:
        report = payload["reports"][report_name]
        value, age = _metric_text(_metric(payload, report_name)) if report_name in METRICS else ("", "")
        reports.append({
            "title": REPORT_TITLES[report_name],
            "status": STATUS_LABELS[report["status"]],
            "color": STATUS_COLORS[report["status"]],
            "text": _text(report["text"]),
            "value": value, "age": age,
        })
    if name == "flow":
        for metric_name in ("load_power", "grid_power", "battery_power"):
            metric = _metric(payload, metric_name)
            if metric is None:
                continue
            value, age = _metric_text(metric)
            reports.append({"title": METRICS[metric_name][2],
                            "status": STATUS_LABELS[metric["status"]],
                            "color": STATUS_COLORS[metric["status"]],
                            "text": "", "value": value, "age": age})
    spoken = payload["reports"][name]
    return _attach(
        event, response, title=REPORT_TITLES[name], reports=reports,
        card_text=f"{STATUS_LABELS[spoken['status']]}\n\n{spoken['text']}",
        footer="Snapshot for this request. Tap Refresh or ask again. Source receipt age is not measurement age.",
        keep_screen=True, token=TOKEN_PREFIX + name + (":full" if detailed else ":brief"),
    )


def message_visuals(event: dict, response: dict, *, title: str = "Home Energy",
                    status: str = "", keep_screen: bool = True) -> dict:
    """Replace previous readings with guidance or a safe, current error message."""
    text = response["response"]["outputSpeech"]["text"]
    return _attach(
        event, response, title=title,
        reports=[{"title": title, "status": status, "color": "#CAD5E0", "text": _text(text), "value": "", "age": ""}],
        card_text=text, footer="", keep_screen=keep_screen,
    )
