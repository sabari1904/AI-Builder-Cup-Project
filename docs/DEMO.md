# Demo video script (under 3 minutes)

**Setup:**
- **Laptop:** `/listen` (full screen), plus `/bank` in another window.
- **Phone 1 (Ramesh):** `/phone`.
- **Phone 2 (Priya):** `/family`.
- **Teammate:** plays the scammer, reading the real ICICI script on a phone call that is put on speaker next to the laptop. Backup: the "Replay" button.

| Time | On screen | Voice-over |
|---|---|---|
| 0:00–0:15 | Black screen, then the real call log from 14 Oct | "Two days ago, one of us got this call. 'ICICI card protection, ₹4,999.' It was a scam. Most people don't spot it. Scams take over a trillion dollars a year, and 4 in 5 times the victim sends the money themselves." |
| 0:15–0:25 | Riposte landing page | "Riposte protects the person, guards the money and strikes back at the scammer." |
| 0:25–1:05 | Listener: call on speaker, transcript streaming, meter climbs Calm → Be careful → SCAM ALERT; reasons appear with exact quotes; "Banks call from 1600 numbers" card; fake website flagged | "Gemini Transcribe Live listens. Gemini Flash reads the caller's intent, and every finding must quote their exact words. The decision is made by fixed rules you can audit. The moment she says the website, Riposte checks it: it's a look-alike of ICICI. Scam alert." |
| 1:05–1:30 | Ramesh's phone: tries to pay ₹4,999, gets "Payment on hold". OTP arrives, covered with a warning | "If Ramesh tries to pay, the bank holds it, because he's on a scam call. An OTP arrives, and it's covered: no bank asks for this." |
| 1:30–1:45 | Priya's phone: urgent alert with the evidence, taps Stop it | "His daughter gets a summary, not a recording, and stops it with one tap. The money never left." |
| 1:45–2:25 | Listener: tap "Hand over to Riposte AI"; the decoy talks ("one minute beta, getting my glasses… spell the UPI ID slowly"); the UPI ID appears on the bank console | "Now we strike back. Gemini Live takes the call as a confused elder and gets the scammer to spell out where the money should go. It only ever gives out planted fake details." |
| 2:25–2:40 | Bank console: intel table, then the tripwire (decoy card used at a jeweller) | "Those details become mule-account leads for the bank. And if the scammer uses the planted card, the bank sees exactly where the scam cashes out." |
| 2:40–2:52 | End call, then the ADK case agent writes the action plan and a cyber-crime complaint draft | "After the call, an ADK agent writes the action plan and the police complaint." |
| 2:52–3:00 | Architecture slide | "Gemini three ways, ADK and Cloud Run. One person protected, one scam account burned, every call." |

**Live "scam us" moment (finale):** invite a judge to play the scammer on the speaker call while the room watches the meter.

## The "why two devices, not one app on my phone" moment

Judges will ask this. Say it before they do &mdash; it reads as foresight, not a gap:

> "You're seeing this on a laptop today because Android and iOS don't let *any* ordinary app listen to a live call &mdash; that needs a system permission only a phone maker or carrier can grant, which is exactly why Google's own scam detection ships on Pixel, not as an app. Here's what Riposte looks like once it can join the call itself, through a carrier feature or a call-merge partner like Twilio: [cut to `/vision`] &mdash; the same alert, the same silent coaching, the same hand-off to our AI, but as an overlay on your own call screen, with a vibration and a sound, not a second device."

`/vision` (linked from the homepage, 💭 card) is a concept mockup &mdash; tap "Start the call" to run a scripted call-screen simulation: a native in-call UI, then the Riposte alert buzzes in and escalates Caution → High → SCAM ALERT exactly like the real risk engine, ending in "Hand to Riposte AI" or minimising to a floating badge. No real call, audio, or AI runs on it; it exists to *show the destination*, not to be tested as a feature.
