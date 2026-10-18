# Riposte

**When a scammer calls, one person is protected and one scam account gets burned.**

Riposte is an AI guardian for phone scams. It listens to a call on speakerphone and uses Gemini to understand what the caller wants. Its bank holds payments started during a risky call, a trusted family contact is alerted with the evidence, it checks with the real family member's phone, it can hand the call to an AI decoy that pulls out the scammer's payment details, and it writes a report ready to submit as a complaint.

Built for the **Google Cloud AI Builder Cup 2026 · BFSI: Intelligent Risk, Fraud & Financial Experiences**. The demo scenario is a real scam call our team received on 14 Oct 2026: a fake "ICICI card protection" offer that led to a look-alike website and a fake app.

## What it does

| | Feature | How |
|---|---|---|
| 🎙️ | **Listens to the call** | The phone is on speaker; a laptop or tablet streams audio to **Gemini Transcribe Live** |
| 🧠 | **Understands the caller** | **Gemini Flash** returns typed findings in a fixed format. Each finding must quote the caller's exact words, or it is discarded |
| ⚖️ | **Decides by fixed rules, not AI** | An explainable risk engine; every point traces back to evidence |
| 🔗 | **Checks spoken links** | "icici cards dot services" → look-alike + domain age + **Google Web Risk** |
| 🏦 | **Bank calls from a mobile number** | TRAI requires banks to use 1600-series numbers |
| 👨‍👩‍👧 | **"Is it really her?"** | Asks the real family member's phone. Beats any voice clone |
| ⏸️ | **Guards the money** | The bank's own risk-based hold on payments started during or after a risky call (60-min cooling-off). Only the account holder can cancel or release. Self kill switch. Graded OTP guard |
| 👪 | **Trusted contact** | Gets live alerts with the evidence. Can call, ask Papa to cancel or freeze (he decides), or call 1930. No control over his account, matching law in India, the US, UK, Singapore and Australia |
| ⚔️ | **Strikes back** | **Gemini Live** decoy plays a confused elder, extracts UPI IDs and accounts, and gives out only planted fake details |
| 🪤 | **Tripwire** | Any use of the planted decoy card shows the bank where the scam cashes out |
| 📝 | **Report** | A **Google ADK** agent with tools writes the action plan and complaint draft. The full call record (evidence, transcript, payments) can be reviewed, saved as PDF and submitted |

## Architecture

```
phone on speaker ))) Listener (browser) ──WS 16 kHz PCM──► Cloud Run (FastAPI)
                                                            ├─ transcribe  Gemini Transcribe Live (rotating sessions)
                                                            ├─ analyze     Gemini Flash (structured, quote-checked) + rule backup
                                                            ├─ risk        fixed rules + floor
                                                            ├─ policy      actions table
                                                            ├─ linkguard   look-alike · RDAP age · Web Risk
                                                            ├─ intel       UPI/account/IFSC/domain extraction
                                                            ├─ guard       BankAdapter (MockBank): hold · release · freeze · tripwire
                                                            ├─ circle      family alerts · presence ping (SSE)
                                                            ├─ strike      Gemini Live decoy
                                                            └─ agents      ADK case agent
Phone app · Family app · Bank console ◄── SSE live events
```

Every module sits behind one interface, so listening via a phone line (Twilio), a carrier or a phone maker later changes only the `capture` adapter. The full design, with each decision and how we tried to break it, is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

**Golden rule:** the AI never decides whether money is stopped. Fixed rules hold payments; AI adds evidence, coaches, verifies and strikes back. If Gemini is slow or down, a rule-based analyser running alongside it keeps protecting the call.

## Run it locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt
echo 'GEMINI_API_KEY=your-key' > .env
tools/dev.sh start                 # http://localhost:8765
```

Open `/setup` to walk through onboarding (choose "protect myself" or "watch over family", add emergency contacts, done in under a minute), or jump straight to `/listen`, `/phone` and `/family` in separate tabs or devices. The bank console (`/bank`) is future scope. On the Listener, press **Replay the real ICICI scam call**, or start listening and play a call on speaker.

## Tests

```bash
cd backend && ../.venv/bin/python -m pytest -q        # unit: risk rules, quote check, link guard, extraction
../.venv/bin/python ../tools/api_test.py              # integration: 26 checks incl. live Gemini decoy
node ../tools/ui/ui_test.mjs                          # real browser: 4 screens end to end
../.venv/bin/python ../tools/stream_test.py ../evals/audio/icici_card_protection.wav   # live audio
```

`tools/make_audio.py` synthesises the test calls with Gemini TTS and degrades them to sound like a phone on speaker.

## Deploy (Cloud Run)

```bash
PROJECT=your-project tools/deploy.sh
```

The script uses one instance (in-memory state), 60-minute WebSockets, no CPU throttling and a warm instance. The Gemini key is stored in Secret Manager.

## Roadmap: listening on the phone itself

Ordinary apps can't hear a phone call on Android or iOS. Capturing call audio needs the `CAPTURE_AUDIO_OUTPUT` permission, which Android grants only to **system apps signed or preinstalled by the phone maker**. iOS offers no such route. So one-device listening needs one of:
1. **A phone maker preloading Riposte**, or building it into their dialer (as Google and Samsung do with their own scam detection).
2. **A carrier running it in the network** (as Airtel does for spam).
3. **The call joining our AI through the phone network** (call merge or forwarding via Twilio or Exotel). This works on any phone today, with no permissions.

The `capture` module is the only part that changes.

## Honest limits

- **Two devices are needed today.** Android and iOS don't let ordinary apps hear a phone call. One-device listening needs a phone-maker or carrier partner.
- **Payment holds need a partner bank** (MockBank here). Family members are alerted and can request; they can't act on another adult's account without a mandate or power of attorney. RBI's April 2026 proposal on trusted-person approval is not yet in force.
- **Extracted details are leads for a bank to verify,** never automatic freezes.
- **No call audio is stored.** Family members see summaries only.
- **State is in memory** (single instance). Firestore can replace the `store` module.
