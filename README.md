# Amazon Echo ↔ Home Assistant voice

Portable patterns so **Amazon Echo / Alexa** can control home devices and speak **stats**, with Home Assistant (or Matter) as the hub of truth.

> Alexa is the microphone. HA owns devices and sensors. No account ids, entity ids, or AWS project wiring belong in this repo — keep those in *your* HA.

## Architecture

1. **Hub**: Home Assistant (preferred) or Matter-capable controller
2. **Voice front-door** (preferred order):
   - HA Alexa / Nabu Casa Cloud expose list
   - Stock Alexa ↔ HA skill link
   - Optional: custom Alexa Skill → authenticated webhook → HA (only if stock linking cannot express the ask)
3. **Stats**: speakable sensors + small HA scripts / TTS (`notify.alexa_media` or equivalent)

## Quick start

1. Give voice-facing entities **plain speech** names (`kitchen lamp`, not `lt_kit_01`).
2. Expose only what should be voice-controlled (Settings → Voice assistants / Nabu Casa).
3. Default-deny locks, garage, alarms unless you explicitly opt in.
4. For multi-step “changes” (modes, ESS, pre-charge), wrap in one HA **script** or **scene** and expose *that*.
5. Link Alexa → discover devices → fix names → test one control + one stats utterance.

## Patterns in this repo

| Path | Purpose |
|------|---------|
| `patterns/helpers/speakable_sensors.yaml` | Template sensor naming for voice |
| `patterns/scripts/announce_stat.yaml` | Script skeleton that speaks one sensor |
| `docs/utterance-catalog.md` | Example say → does table |

Copy into your HA `packages/` (or split config) and rename entities to match your house.

## Safety

- Confirm before enabling voice on security-sensitive entities.
- Prefer scripts with caps / presence checks over raw `set_value` on critical setpoints.
- Custom skill webhooks must authenticate (shared secret / signed JWT). No open internet endpoints.

## License

MIT
