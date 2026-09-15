# Supported English (US) requests

Invocation name: **home energy**. This is an Alexa custom skill, so include its invocation name when starting a request.

- **Alexa, open the home energy skill** — immediately reads IGW `reports.status` and displays the energy overview on supported screens. No follow-up request is needed; the invocation name remains **home energy**.
- **Alexa, ask home energy what is the battery charge** — `BatteryIntent`, IGW `reports.battery`.
- **Alexa, ask home energy what is the solar power** — `SolarIntent`, IGW `reports.solar`.
- **Alexa, ask home energy how much solar energy did we produce today** — `SolarTodayIntent`, IGW `reports.solar_today`.
- **Alexa, ask home energy are there any alarms** — `AlarmStatusIntent`, IGW `reports.alarms`.
- **Alexa, ask home energy what is the energy status** — `StatusIntent`, IGW `reports.status`.
- **Help** — describes these requests and keeps the session open.
- **Stop** or **cancel** — ends the session.

After a help prompt, omit the invocation name for the next request. Opening the skill already returns the status report; use a complete request to start another report after the session ends. Exact training phrases are in `skill-package/interactionModels/custom/en-US.json`. Requests to control devices cannot cause writes.
