# Model 710 Generator Auto-Start — Dilanka, Polonnaruwa

Automating an existing standby genset so it can be started remotely from the Ktronics
platform, instead of by hand at the panel.

> ## ⚠️ Read this before trusting any figure below
>
> **The module on site is branded "AES 710", not Deep Sea.** Every specification, terminal
> number and configuration item in this document comes from `DEEP-SEA-710-MANUAL.pdf`
> (genuine DSE Model 710 Operators Manual, doc 057-066, Issue 2.1), which is **not** the
> manual for the unit actually fitted.
>
> No "AES 710" product line exists in the market. It is almost certainly one of the many
> **DSE710 form-factor clones** — an industry that exists specifically to make drop-in
> pin-compatible copies, so the pinout is *probably* identical. Probably is not good enough
> to wire from.
>
> **Verify on the actual module before any wiring or quoting:**
>
> | Claim | Confidence | How to verify |
> | --- | --- | --- |
> | B-14 = remote start, switch to negative | High — clones copy the connector legend | Read the module's own rear terminal label |
> | A-1/A-2 supply, A-3 e-stop | High | Same rear label |
> | **Aux output option 7 = "System in auto"** | **Low** | Walk the actual config menu. Clone firmware often has a shorter or reordered option list — and the not-in-auto alerting design depends entirely on this existing |
> | **150 mA standby draw** | **Low** | **Measure it.** This is a DSE spec and it is the headline number in the customer proposal |
> | Rides out 0 V for 50 ms while cranking | **Low** | Clone supply design rarely matches. Assume it does *not* — which makes the hold-up capacitor more important, not less |
> | 3 crank attempts, timers | Medium | Configurable anyway; read the actual values |
>
> The *observed* behaviour needs no manual: the battery goes flat and has to be
> disconnected. That is the customer's own experience and the argument stands on it. Only
> the precise numbers are in question.
>
> Also note the genuine **DSE710 is now obsolete**, superseded by the **DSE6110 MKIII** — relevant
> to the controller-replacement option below. [Soar Technology](https://www.deepseaelectronics.com/distributor/soar-technology-pvt-ltd)
> (Welisara, 011 2232601 / 077 726 0075) is the Sri Lankan DSE distributor and the best
> source for a genuine replacement or the PC configuration software.

---

## 1. Site equipment

| Item | Detail |
| --- | --- |
| Alternator | Nippon Sharyo **NEG-350A**, brushless, 35 kVA @ 50 Hz / 200 V / 101 A, 4-pole, 1500 rpm, PF 0.8 |
| Controller | **"AES 710"** auto start module — a DSE710 form-factor clone (see warning above) |
| Starting battery | 24 V — 2× 12 V Amaron in series |
| Charger | UPE 24-10A, **clipped on with crocodile clips**, manually switched |
| Load transfer | **Manual changeover switch** (no ATS) |
| Engine hours | 2579.8 |

---

## 2. What the customer does today

1. Connect the battery (manual disconnect)
2. Press the **red ⭘ STOP/RESET** key on the 710
3. Press the **✋ MANUAL** key — LED beside it confirms
4. Press the **green `I`** key to begin the start sequence (manual §3.1)
5. Throw the manual changeover by hand

Three of those five steps exist only because the set is being run in **MANUAL mode**,
which by design never starts on its own. The set has an auto-start module that is not
being used as one.

---

## 3. Root cause of the battery disconnect

> ### ⚠️ Correction to the initial assessment
>
> The first read of this job assumed the battery disconnect was hiding a parasitic fault
> to be hunted down. **The manual says otherwise, and the customer's habit is correct
> behaviour.**

DSE710 specification (manual §7):

```text
Typical Standby Current    145mA at 12V.  150mA at 24V
Max. Operating Current     180mA at 12V.  190mA at 24V
DC Supply                  8.0V to 35V Continuous
```

The module draws **150 mA continuously at 24 V just sitting in standby**. That is:

| Period | Drain |
| --- | --- |
| Per day | 3.6 Ah |
| Per week | 25 Ah |
| To half-flatten a 100 Ah bank (50% usable) | **~14 days** |

With no permanently connected charger, the module flattens the starting battery in about
a fortnight. Disconnecting the battery between runs is the only thing keeping those
batteries alive. **The drain is by design — the missing float charger is the actual
defect.**

This is decisive for the whole job: **remote start is impossible while the battery is
disconnected**, because a disconnected module cannot listen for a start signal. Fixing
the charging is not a tidy-up item, it is the precondition for everything else.

### Brownout resilience — the 710 resetting on a weak battery

The concern is real and independent of any deliberate power switching: a tired battery sags hard
during cranking, the module resets, and comes up in an unknown mode. Address it in this order.

**1. It is a battery fault first.** Manual §7:

> **Cranking Dropouts** — Able to survive 0 V for 50 mS, providing supply was at least 10 V before
> dropout and supply recovers to 5 V.

The module is built to ride out cranking. If it is resetting, the battery is collapsing below that
envelope — and a battery that cannot hold the *controller* up through a crank generally cannot
crank a 35 kVA set reliably either. Fitting a button-presser to work around that is treating the
symptom while the real failure keeps growing.

**2. Add diode-isolated hold-up capacitance on the module supply.** Cheap, non-invasive, and it
directly removes the failure mode. Sizing for 190 mA (max operating current) held for 500 ms while
the rail sags from 24 V to the module's 8 V minimum:

```text
C = I·t / ΔV = 0.19 × 0.5 / 15 ≈ 6.3 mF
```

So a **10,000 µF 35 V** electrolytic across A-1/A-2, fed through a series diode so the reservoir
cannot discharge back into the starter. The 710 keeps running through the sag. Verify against the
actual measured crank-sag depth and duration at commissioning.

**3. Make the ESP32 aware of the module's mode — this is the important one.** Configuration items
27–30 assign the four configurable outputs, and the option list includes:

| Value | Function |
| --- | --- |
| 5 | Engine Running |
| 7 | **System in auto** |
| 14 | Common Alarm |
| 15 | Fail to start |

**Option 7 means the controller can tell us whether it is in AUTO.** So after any reset, the ESP32
knows immediately that the module will no longer accept a remote start, and raises an alert instead
of failing silently at 2 a.m. That is the correct behaviour: surface it to a human, do not paper
over it.

Revised output allocation (all four now used):

| Terminal | Assign | Purpose | ESP32 |
| --- | --- | --- | --- |
| A-6 | 5 — Engine Running | run confirmation | GPIO 27 |
| A-7 | 14 — Common Alarm *(already the default)* | any alarm | GPIO 14 |
| A-8 | 7 — System in auto | **will it accept a remote start?** | GPIO 25 |
| A-9 | 15 — Fail to start | definite signal, no timeout guessing | GPIO 33 |

Two notes. A-6 currently defaults to *Preheat Mode 0* and A-8 to *Close Generator* — both are
reassigned here, and Close Generator is free only because the changeover is manual. **If an ATS is
ever added, A-8 must go back to Close Generator** and system-in-auto moves elsewhere. With A-9
giving an explicit *fail to start*, the 90 s inferred timeout in the alerting table becomes a
backstop rather than the primary detection.

**4. Only then consider actuating the panel.** If testing shows the module does *not* restore AUTO
after a power cycle, the options are, in order of preference:

- **Test first — it may be a non-issue.** Put it in AUTO, pull the 2 A supply fuse, restore, and
  see which mode it comes up in. Two minutes, and it decides whether any of this is needed. The
  manual does not document the answer.
- **A contact across the AUTO key only.** Restores mode; never touches STOP/RESET.
  Invasive — it means opening a safety controller and soldering into a 2005-vintage keypad matrix.
- **Replace the controller** with a Modbus module. The genuine DSE710 is obsolete; its successor is
  the **DSE6110 MKIII**, or step up to a DSE4520/7320 or SmartGen HGM6120. Mode, alarms
  and start/stop/reset all over RS485, no hardware hacking, and it solves telemetry at the same
  time. The honest long-term answer if this module keeps misbehaving.

> **Never auto-clear a latched shutdown.** §3.5: *"Shutdowns are latching and stop the Generator.
> The alarm must be cleared, and the fault removed to reset the module."* If *Fail to Start*
> latched because the engine is dry or has no oil pressure, an ESP32 that resets and retries will
> flatten the battery or wreck the engine. Expose reset as an explicit operator action in the app,
> rate-limited, never automatic.

### Rejected: switching the 710's supply with an SSR from the ESP32

Considered and **not recommended**. The idea is to leave the controller unpowered between runs and
switch its supply on only when a start is wanted, eliminating the 150 mA.

**First, the drain is a battery problem, not a power problem.** In energy terms it is negligible:

| | |
| --- | --- |
| Continuous load | 150 mA × 24 V = **3.6 W** |
| Per year | ~31.5 kWh — roughly **Rs 1,500–2,500** |

25 Ah/week only *sounds* alarming because a starting battery is a small reservoir. Once a float
charger is permanently wired, 3.6 W off the mains is nothing. The fix is to stop running the
controller from a disconnected battery — not to stop running the controller.

**Second, it destroys the monitoring you are trying to protect.** Manual §3.4:

> **LOW PLANT BATTERY ALARM** — The modules DC supply is monitored and if it falls below the
> configurable level an alarm is generated.

Plus **BATTERY CHARGE FAILURE** on the same page. The 150 mA is precisely what pays for the module
watching the battery and the charging system. Switch it off to save the battery and you lose the
alarm that tells you the battery is dying — during the idle months when that is the only warning
you would get. The alerting design collapses to nothing for ~99% of the set's life.

**Third, power-up mode is undocumented.** I searched the whole manual: it never states which mode
the 710 enters on power-up. The scheme bets the entire remote-start capability on untested
behaviour — and if it comes up in STOP/RESET (which the customer's existing red-button ritual
hints at), the remote start input is simply ignored and the set never starts.

**Other objections**

- **Never cut power while running.** The fuel solenoid de-energises instantly, stopping the engine
  with no cooldown. The SSR becomes an accidental emergency stop; firmware would need a hard
  interlock against it.
- **New single point of failure in a safety chain.** SSR fails open → the set can never start
  remotely and the controller is dead. DC SSRs also carry leakage current and a forward drop.
- **E-stop and every protection are inert** while the module is unpowered.

**If the real concern is no mains at the generator**, the answer is not power-cycling. In order of
preference: repair the charge alternator so the engine recharges its own battery; or fit a
**30–50 W solar panel and small charge controller on the starting battery** — 3.6 W continuous is
86 Wh/day, which a 30 W panel covers comfortably at Polonnaruwa's irradiance. That keeps the
controller powered *and* survives grid outages, which is exactly when a standby set matters. A
low-voltage disconnect that isolates only below ~23 V is a reasonable last-resort battery
protection, but it is a safety net, not a routine operating mode.

### The charger already on site is not automatically suitable

The customer already owns the **UPE 24-10A "voltage regulated charger"** seen clipped to the
battery. Do not assume it can simply be hard-wired — *voltage regulated* is not the same as
*automatic float*.

The test, before wiring it in permanently: charge the bank to full, leave the charger connected,
and measure terminal voltage after several hours.

| Settles to | Verdict |
| --- | --- |
| ~27.2–27.6 V | True float. Hard-wire it, no new charger needed. |
| Holds 28.8 V+ indefinitely | Constant-voltage bulk only. It will gas flooded cells dry in weeks. **Replace.** |
| Keeps pushing current at full charge | No termination. **Replace.** |

This matters more here than on a normal install, because the whole point of the job is leaving the
charger connected permanently and unattended. A charger that was safe under a human's supervision
for a few hours is not automatically safe left on for months.

### Pre-heat — not worth fitting at this site

The DSE710 supports a pre-heat output and timer (§3.1, §3.2), and it can be driven from a
configurable output. **Recommend against using it**, for three reasons:

- Glow-plug pre-heat matters below roughly 5–10 °C. Sri Lankan ambient at this site never
  approaches that, so the timer would only add delay before every start.
- It is not free: pre-heat runs *before* cranking, so it lengthens the window between "start
  commanded" and "set carrying load" — the opposite of what a standby set wants.
- Room temperature is the wrong variable anyway. What actually degrades a standby diesel here is
  *sitting unused*, and the fix for that is the weekly exercise run, not pre-heat.

**The useful version of the same instinct is enclosure temperature monitoring.** In this climate a
sealed enclosure running at load is far more likely to be too hot than too cold. A DS18B20 on a
spare ESP32 GPIO gives a high-enclosure-temperature warning for a few hundred rupees, and that is a
real failure mode. Worth adding; pre-heat is not.

If the engine turns out to have glow plugs and a customer preference for it, enabling pre-heat is a
configuration change only — no rewiring — so it can be revisited after commissioning.

### Still to check on site

- **Charge alternator output** — run the set, measure at the battery: want 27–28 V. Pin 10
  is `Charge Fail / Excite`; charge-fail trip for a 24 V system is **16 V** (§7). A dead
  charge alternator would mean the set never recharges its own battery even after a run.
- **Battery condition** — 24 h rest, ~25.4 V open circuit, then a crank/load test. The
  cases look corroded and aged. Replace as a **matched pair** if either fails.
- **What the LCD says before the red key is pressed.** If the red press is clearing a
  latched alarm rather than just a power-up reset, that alarm is a real fault and must be
  fixed, not automated around. This information is destroyed on every start at the
  moment — photograph the display *before* touching the key.

---

## 4. Design: remote start on Pin 14

The 710 is a remote start module (§1) — it already does everything needed. It starts when
its Remote Start input is pulled to battery negative while the module is in **AUTO**.

**Connector B, Pin 14 — Remote Start input** (§6.1.2):

```text
14   Remote Start input   0.5mm² (20 AWG)   Requires a contact to plant supply negative.
```

Configuration option **22 — Remote Start** (§5):

```text
0 - Remote start, close to activate     <-- factory default, use this
1 - Remote start, open to activate
```

So: an ESP32 driving an opto-isolated relay, providing a **dry contact between Pin 14 and
plant supply negative (Pin 1)**. Close to start, open to stop. Nothing else is touched.

### Auto sequence once triggered (§3.2)

```text
Remote Start closed
  -> Start Delay timer          (rejects false/transient start signals)
  -> Pre-heat (if configured)
  -> Fuel Solenoid energised
  -> +0.5s Starter Motor engaged
  -> Cranks; up to 3 attempts, else "Fail to Start"
  -> Starter locks out at 20Hz from alternator output
  -> Safety On delay  (low oil pressure / underspeed armed after this)
  -> Warmup timer
  -> Load transfer  [not wired here - manual changeover]

Remote Start opened
  -> Stop delay -> load switch opens -> Cooling timer -> Fuel Solenoid de-energised
```

Two consequences of the manual changeover:

- The set **runs off load** until someone throws the changeover by hand. That is the safe
  arrangement — no back-feed path to the CEB network exists, because a human is still the
  interlock. Keep it that way.
- Don't leave it running off load for long. A lightly loaded diesel wet-stacks.

### Remote Start is a *maintained* signal, not a pulse

The relay must stay closed for the entire run. The firmware must not drop it on a WiFi
reconnect or a watchdog reboot. Failure direction is fail-to-stop, which is correct for a
standby set — and **MANUAL mode on the 710 front panel always overrides the remote
input**, which is the local escape hatch if the ESP32 dies. Document that for the
customer.

---

## 5. Wiring

| From | To | Notes |
| --- | --- | --- |
| Relay contact NO | DSE710 **Pin 14** (Remote Start) | 0.5 mm² / 20 AWG |
| Relay contact COM | DSE710 **Pin 1** (Plant supply negative) | dry contact only — never source voltage into the input |
| ESP32 supply | Battery 24 V via isolated buck | own fuse, separate from the module's 2 A supply fuse |

### Do not disturb

- **Pin 2** — plant supply positive, fused **2 A anti-surge** (§6.1.1)
- **Pin 3** — Emergency stop input, switch to battery **positive**, normally closed,
  **OPEN to STOP**. If no e-stop is fitted a permanent positive must sit on Pin 3.
  Confirm the site e-stop actually works before running unattended.
- **Pin 10** — Charge Fail / Excite. *"Do not connect to ground (battery -ve)"* (§6.1.1)
- **Pin 13** — Sender/switch common. *"must be connected to a sound earth at the engine
  block earth star point. The connection to terminal 13 must not be used for any other
  purpose."* Do not borrow it as the relay's negative — use Pin 1.

### ESP32 power — the part that usually fails

The module itself is hardened against cranking dropouts (§7: *survives 0 V for 50 ms
providing supply was at least 10 V before dropout*). **The ESP32 is not.** The 24 V rail
sags hard while cranking a 35 kVA set, and a naked buck converter will brown-out and
reset the ESP32 at exactly the wrong moment — mid-start, dropping the maintained relay.

- Isolated buck, **9–36 V input range**, sized for the module's 8–35 V supply window
- Input TVS + bulk capacitance for cranking hold-up
- Inline fuse on the feed

### Control hardware — use an industrial unit, not a dev board

A bare ESP32 dev board with a buck module and a relay board is the wrong choice for a machine that
must run unattended for years in a hot enclosure in Polonnaruwa. Jumper wires, a USB connector and
a hand-built opto board are fine on a bench and a liability in a customer's panel.

**Recommended: [NORVI IIOT-AE01-R](https://norvi.io/products/norvi-iiot-esp32-industrial-controller/) — USD 88.21**

| | |
| --- | --- |
| Digital inputs | **8 × opto-isolated, 24 V** sink/source |
| Relay outputs | 6 × 5 A (30 V DC / 250 V AC) |
| Also | 2 × transistor out, RS-485, WiFi + BT, 0.96" OLED, DIN rail, screw terminals |
| Vendor | **Iconic Devices (Pvt) Ltd**, Midigama East, Weligama — Sri Lankan company, founded 2014 |

Why it fits this job almost exactly:

- **The 24 V opto inputs take the genset controller's aux outputs directly.** Those outputs switch
  to battery positive at 24 V, which is precisely what a 24 V industrial DI expects. That deletes
  4 × opto-isolators, 4 × 3.3 kΩ resistors, the relay module, the buck converter, the GPIO
  pull-down, the enclosure and the terminal blocks from the BOM. Against all that plus assembly
  labour, USD 88 is close to a wash — and the result is a sealed, serviceable, replaceable unit.
- **Same ESP32 code.** Arduino or ESP-IDF, no new toolchain.
- **8 DI / 6 relays leaves generous spare I/O** — low-fuel float switch, enclosure temperature,
  and a second relay if remote *stop* is ever wanted separately.
- **The OLED shows status on site** without a laptop, which a technician will thank you for.
- **RS-485 is a free upgrade path** if the 710 is ever replaced with a Modbus controller.

**How Sri Lankan is it, exactly?** The *company* is solidly local and independently corroborated —
Iconic Devices (Pvt) Ltd, Green Cliff 2, Midigama East, Weligama 81700, founded 2014, 10–20 staff,
`.lk` domain, public GitHub org, LinkedIn and Facebook presence. The *manufacturing* claim is their
own marketing: "All design, prototyping, and manufacturing are handled in-house… 100% produced at
our own facility with full traceability." Read that as **designed and assembled in Sri Lanka** —
bare PCBs, ESP32 modules, relays and optos are necessarily imported, since no PCB fab or
semiconductor industry exists here at that scale. Normal for any small electronics firm, not a
criticism.

What it actually buys: warranty and RMA in-country, support in your timezone, no customs or import
lead time, and the ability to phone or visit the people who built it. Note they are in **Weligama
on the south coast**, not Colombo — domestic, but not a same-afternoon collection.

**Cellular variant:** NORVI also does [GSM / 4G LTE models](https://norvi.io/products/cellular-iot-controller-esp32-4g-lte-industrial-controller/),
USD 90–228, WiFi *and* cellular. Worth pricing for Polonnaruwa — a rural generator shed may have no
usable WiFi, and §7 of the proposal currently *assumes* coverage. A SIM removes that dependency on
the customer's network entirely.

**Budget alternative:** Kincony KC868 series — DIN-rail ESP32, relays plus opto inputs, roughly
USD 30–50, ESPHome support. Cheaper, but Chinese import with no local support or warranty.

**Why not a cheap PLC.** A LOGO!, Delta DVP or Haiwell would handle the I/O comfortably and is very
robust — but the I/O here is trivial (1 output, 4 inputs) and the actual problem is *connectivity*.
PLCs have a poor story for pushing to a custom platform; you would end up bolting an ESP32 on as a
gateway anyway, ending with two devices, two failure points and a higher bill. PLCs are for
deterministic machine control; this is an IoT job with incidental I/O.

> **Must confirm before ordering: the supply voltage range.** NORVI publishes "rated 24 V DC" but
> not a minimum. This is the make-or-break spec — during cranking a 24 V system dips hard, and if
> the unit resets, **the maintained remote-start relay opens and the controller aborts the crank**.
> Ask NORVI for the actual input range and brownout threshold. If it is not comfortably below the
> measured crank sag, feed it from a wide-input buck-boost with hold-up capacitance.

#### Do not regulate the genset controller's own supply

Tempting to run one buck-boost feeding both the 710 and the control unit. **Don't.** Per §3.4 the
710 raises its LOW PLANT BATTERY alarm by monitoring *its own DC supply* — feed it a regulated
24 V and that alarm becomes blind to the actual battery state, losing one of the alerts this whole
design depends on.

The 710 gets **diode-isolated hold-up capacitance only** (steady-state voltage preserved, transients
ridden out); the control unit gets its own buck-boost. Note the series diode drops the voltage the
710 reads — use a Schottky (~0.3 V) and set its low-battery threshold accordingly, or an
ideal-diode MOSFET module (~0.02 V) to keep the reading honest.

### Alerting — how it actually works

Every alert is derived from a hard signal off the controller, never inferred from the fact that we
sent a start command.

| Alert | Source | Logic |
| --- | --- | --- |
| Started / stopped | A-6 → opto → GPIO 27 | Edge on the *generator running* output |
| Failed to start | absence of A-6 | Start commanded, no running signal within **90 s** |
| Controller alarm | A-7 → opto → GPIO 14 | Common alarm output asserted |
| Unit offline | platform side | Heartbeat (5 min) missed |

**Why 90 s for fail-to-start:** the DSE710 makes up to **3 crank attempts** with a configurable
rest between each (§3.2). A shorter timeout would fire while the controller is still legitimately
trying. Read the actual crank/rest timers off the module at commissioning and set the timeout to
comfortably exceed `3 × (crank + rest)`.

**Use A-7 for common alarm rather than measuring battery volts on the ESP32.** The controller
already monitors battery voltage, oil pressure, engine temperature, charge failure and e-stop, and
raises a common alarm for all of them. Taking one opto-isolated signal gets every one of those for
free, using the controller's own calibrated thresholds — and it avoids having to measure a 24 V
battery from a board whose ground is deliberately isolated from battery negative.

> **Architecture gap to resolve before quoting delivery.** The existing Ktronics platform is
> GitHub-Actions cron — it *polls* on a schedule. A "failed to start" discovered up to four hours
> later is worthless. Alerts of this kind must be **pushed from the device** (an always-on endpoint
> or broker the ESP32 posts to), with the existing platform used for the dashboard, history and the
> offline-heartbeat check. Don't promise real-time alerting on top of a 4-hourly poller.

### Feedback

Don't fly blind — the app must know whether the set actually started. Best option is a
spare **configurable output** (Pins 6–9, §6.1.1), which are solid-state outputs switching
to **battery positive** at 1.2 A, fed through an opto-isolator into a GPIO. Failing that,
AC-presence sensing on the generator output.

---

## 6. Staged work plan

### Stage 0 — Diagnose, buy nothing

1. Photograph the LCD **before** the red key is pressed
2. Confirm standby draw is ~150 mA and not materially more
3. Battery OCV after rest, then crank/load test
4. Charge alternator output at the battery while running (27–28 V)
5. Prove the e-stop and the oil pressure / temperature shutdowns

### Stage 1 — Remove the manual gates

1. Fix whatever the LCD reveals, if anything
2. **Permanently wire the customer's existing UPE 24-10A charger** on its own MCB, replacing the
   clip-on leads — *but only after the float check below*. Supply a replacement only if it fails.
3. Permanent battery cabling: fuse → 250 A isolator left **ON** as a maintenance-only
   lockout point, crimped lugs, no crocodile clips in the cranking path
4. New matched battery pair if testing fails them
5. **Leave the module in AUTO**

> 🚦 **Gate — do not skip.** The set must sit in AUTO for a full week, untouched, with a
> healthy battery and no alarm, *before* any ESP32 is fitted. Automating an unreliable
> set only makes it fail remotely.

### Stage 2 — Remote start

1. Confirm config option 22 = `0` (close to activate)
2. Fit ESP32 + isolated buck + opto relay; dry contact Pin 14 ↔ Pin 1
3. Wire the run-feedback signal
4. Firmware: maintained close, **max run timer** so it cannot run unattended all night,
   weekly exercise run, alarm + offline alerts into the existing platform
5. Commission per §8 (below)

### Stage 3 — optional, later

True AMF (auto start on mains failure) needs a properly interlocked ATS and a controller
with mains sensing — the 710 has neither. Wire Stage 2 so this is an add-on, not a
rebuild. **Do not automate load transfer without an interlock**; back-feeding the CEB
network is lethal to linemen working the line.

---

## 7. Bill of materials

| Item | Notes |
| --- | --- |
| Automatic 24 V float charger, 5–10 A | **Contingency only** — the customer's UPE 24-10A is reused if it passes the float check |
| MCB for charger supply | |
| Battery isolator, 250 A lever | left ON; maintenance isolation only |
| Inline fuse + holder, battery lugs, cable | replaces the crocodile clips |
| 2× 12 V ~100 Ah batteries | only if Stage 0 testing fails them |
| ESP32 dev board | |
| Isolated buck converter, 9–36 V in → 5 V | with TVS + bulk cap |
| Opto-isolated relay module, 2-ch | dry contact |
| Opto-isolators ×2 for feedback inputs | from configurable outputs A-6 (gen running) and A-7 (common alarm) |
| 3.3 kΩ ½ W resistors ×2 | opto series resistors, ~7 mA at 24 V |
| 10 kΩ resistor | pull-down on GPIO 26 — relay must be open at boot |
| DS18B20 temperature sensor | enclosure over-temperature warning (see pre-heat note) |
| DIN enclosure, glands, terminals, fuse | |
| Low fuel float switch | optional, recommended for unattended running |

Spare connector, if the existing one is brittle: **Connector B (14–21), BL08 PCB
connector 5.08 mm plug, DSE part 007-125** (§6.2).

---

## 8. Commissioning test (manual §8)

1. Select **AUTO**. The set must stay in standby. If it starts immediately, there is a
   signal already present on the Remote Start input.
2. Apply the remote start signal from the app. Start sequence runs, engine comes up to
   speed.
3. Remove the remote start signal. Stop delay → cooling period → shutdown to standby.
4. Repeat with the changeover thrown, confirming load is carried.
5. Leave for a week; confirm battery still floats and no alarms latch.

---

## 9. Safety notes

- **No back-feed path may ever exist.** Manual changeover stays manual until an
  interlocked ATS is fitted.
- **Never relay across the STOP/RESET key**, and never bypass a latched alarm. A restart
  on low oil pressure destroys the engine. The 710 shuts down on fault deliberately, and
  §3.2 notes the set is not permitted to load until all delayed alarms read normal.
- Confirm the e-stop works before any unattended running.
- **Clear the rags off the panel shelf inside the enclosure** before the set runs
  unattended.
- Check fuel level before remote-starting; fit a low-fuel switch if this becomes routine.

---

## 10. Open items

- Nameplate is **200 V** but Sri Lanka is 400/230 V. Confirm how the alternator is
  actually connected and terminated before anyone treats it as a drop-in 400 V source.
- Observed phase currents **L1 23 A / L2 12 A / L3 40 A** — a 28 A spread. Rebalance at
  the DB before it heats the alternator.
- Confirm whether the red STOP/RESET press is clearing a real alarm or is just a
  power-up ritual (Stage 0, item 1).
