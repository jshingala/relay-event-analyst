# 60-second demo: offline relay event analysis on a Jetson

Setup, done once while online: `scripts/jetson_setup.sh`, then
`python3 -m rea.evaluation.real_events --fetch` for SEL's example events. Then go offline.

Recording: phone on a tripod, or a screen capture plus a phone shot of the cable. Terminal font 18
pt or larger.

| Time | Shot | Say (about 130 words in total) |
|---|---|---|
| 0:00-0:06 | Close-up: unplug the Ethernet cable from the Jetson; Wi-Fi off | "This is a Jetson Orin. I'm unplugging the network." |
| 0:06-0:14 | `scripts/demo.sh` starts: `ip -brief link` shows no carrier, `rea doctor` prints OFFLINE, core ok, `llama3.2:3b` ok | "Everything here runs on the device: a C++ physics core and a 3-billion-parameter model." |
| 0:14-0:35 | `rea analyze "data/real/sel_webinar_2020/.../Exercise 2.CEV"`; output scrolls | "This is a real SEL-311L event from SEL's event-analysis webinar. The core reads the event report and finds a C-A fault that was gone before this relay tripped: the trip came 21 cycles after the fault current stopped. It also measures the phase rotation: the record is A-C-B and the relay is set A-B-C." |
| 0:35-0:47 | Zoom on VALIDATOR and KEY FACTS | "The model writes the explanation, but every number it uses has to match a fact the physics core produced, or the sentence is dropped. What can't be determined is listed, not guessed." |
| 0:47-0:55 | TIMING line; `tegrastats` in a split pane | "Seconds per event and board power are logged with tegrastats." |
| 0:55-1:00 | Cable still unplugged; repo URL on screen | "No cloud, no network. Code and results are on GitHub." |

Swap in `data/demo/demo_cag_slow_relay.cfg` for a synthetic event with a known answer (relay slower
than its 51 curve predicts).

The analysis has to fit in about 20 seconds of the video. On the 2-vCPU x86 build machine (CPU
only) this event took 126 s, so the shot assumes the Jetson's GPU. Run it once first and read the
TIMING line. If it takes longer than the shot allows, speed that part up in editing and put the
speed-up factor on screen; the TIMING line in the frame shows the real time.

Before recording, check:

- [ ] `rea doctor` reports the model and OFFLINE.
- [ ] One warm-up analysis has run, so the model is loaded (`keep_alive` holds it for 30 min).
- [ ] The power mode is the one you report (`nvpmodel -q`).
- [ ] The narrated output for the chosen event has been read once, and it says what the voice-over
      says.
