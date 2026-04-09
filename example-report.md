# Design Review Report

**Board:** Open-source robot controller board (Rev v20)
**KiCad version:** 8.0 (5 hierarchical sheets)
**Date:** 2024-12-12
**Analyzers:** analyze_schematic.py (modern format, full signal analysis)
**Status:** DONE_WITH_CONCERNS

---

## 1. Board Overview

An open-source robot controller board built around the RP2350B microcontroller. The board provides four H-bridge motor driver channels (2x DRV8411A), USB-C device connectivity, an LSM6DSOX 6-DoF IMU, a Raspberry Pi RM2 wireless radio module (WiFi/Bluetooth), external flash (W25Q128JV), PSRAM (APS6404L), WS2812B addressable LED, and extensive I/O through four 20-pin expansion headers plus servo, encoder, line sensor, and distance sensor connectors. Power is supplied through either a barrel jack or USB-C, with an AP63357 buck converter generating the 5V rail and an RT9080 LDO providing 3.3V for the digital domain.

---

## 2. Component Summary

| Type | Count |
|------|-------|
| Resistors | 49 |
| Capacitors | 45 |
| Connectors | 19 |
| Jumpers | 14 |
| Test points | 11 |
| ICs | 9 |
| Transistors | 9 |
| LEDs | 5 |
| Switches | 4 |
| Mounting holes | 4 |
| Fiducials | 4 |
| Inductors | 3 |
| Fuses | 2 |
| Crystal | 1 |
| Diode | 1 |
| **Total** | **184** |

| Stat | Value |
|------|-------|
| Total nets | 160 |
| Total wires | 774 |
| No-connects | 5 |
| Unique parts | 67 |
| Power rails | 11 (3.3V, 5V, 1.1V, VIN, VSYS, VRAW, VBATT, VUSB, 3V3_EN, GPIO46/VIN_MEAS, GND) |
| Sheets | 5 (root, peripherals, connectors, core, power) |
| MPN coverage | 0/164 (0%) |
| BOM lock status | **FAIL** — no MPNs assigned in schematic properties |

### Assembly Complexity

- **Score:** 58/100 (moderate)
- All SMD, no through-hole components
- 89 hard-to-solder components (0201/0402/BGA/QFN)
- Predominant package: 0402 (83 components)
- 38 unique footprints, 9 unique IC footprints

---

## 3. Power Tree

```
External Power Input
  │
  ├── J1 (Barrel Jack) ──── F1 (16V/2.5A PTC fuse)
  │                              │
  │                              └── VBATT
  │                                    │
  │                                    └── Q2 (DMG2305UX P-FET)
  │                                          R31=100k gate pulldown
  │                                          │
  │                                          └── VRAW ──┐
  │                                                     │
  ├── J2 (USB-C) ──── F2 (6V/0.75A PTC fuse)           │
  │                        │                            │
  │                        └── VUSB                     │
  │                              │                      │
  │                              └── Q4 (DMG2305UX) ───┘
  │                                    R33=100k pulldown
  │                                    Q6 (DMG2305UX)
  │                                    Q5 (BCM857BS) ── Power path ORing
  │
  └── VSYS (selected source)
        │
        ├── U4 (AP63357DV-7) — 5V Buck Converter
        │     Vin=3.8–32V, Iout=3.5A
        │     L2=6.8µH, f_sw=500kHz
        │     FB: R8=180k / R9=33k → Vout ≈ 4.94V (with Vref=0.765V)
        │     C23=47pF feedforward
        │     Output: 6× 22µF + 1× 0.1µF (132µF total)
        │     Input: 7 caps (110.2µF total)
        │     PG → POWER_GOOD → D6 (red LED indicator)
        │     │
        │     └── VIN rail (motor driver power)
        │           ├── U7 (DRV8411A) — Motor drivers L + 3
        │           ├── U8 (DRV8411A) — Motor drivers R + 4
        │           └── Q8 (DMG2305UX) → 5V rail (switched)
        │                 R37=100k pulldown, D3 (Red LED indicator)
        │
        └── U5 (RT9080-3.3) — 3.3V LDO (600mA)
              Fixed output, Vin=3.8–6V
              Input: C26=4.7µF
              Output: 2× 4.7µF + 14× 0.1µF (10.8µF total)
              EN → 3V3_EN
              │
              └── 3.3V rail
                    ├── U1 (RP2350B) — MCU
                    ├── U2 (W25Q128JVPIM) — 128Mb Flash
                    ├── U3 (APS6404L-3SQR-ZR) — 64Mb PSRAM
                    ├── U6 (LSM6DSOX) — 6-DoF IMU
                    └── 1.1V core (U1 internal regulator)
```

### Regulator Verification

| Regulator | Topology | Vref source | Vout est. | Output rail | Status |
|-----------|----------|-------------|-----------|-------------|--------|
| U5 (RT9080-3.3) | LDO | Fixed suffix | 3.30V | 3.3V | OK |
| U4 (AP63357DV-7) | Buck | Heuristic (0.6V) | 3.87V | 5V | **MISMATCH** |

The AP63357 uses a heuristic Vref of 0.6V, producing an estimated output of 3.87V on a rail named "5V" (22.5% deviation). The AP63357 datasheet specifies Vref = 0.765V, which gives Vout = 0.765 x (1 + 180k/33k) = **4.94V** -- consistent with the 5V rail name. This is not a design error but a Vref lookup limitation. Note that U4 outputs to the VIN rail (which feeds the motor drivers), not directly to 5V; the 5V rail is derived via Q8 (power MOSFET switch).

### Power Sequencing

| Regulator | EN source | Behavior |
|-----------|-----------|----------|
| U4 (AP63357) | VIN (always on) | Powers up with input supply |
| U5 (RT9080) | 3V3_EN | Controlled enable |

U4 has a Power Good output (PG) on the POWER_GOOD net, driving D6 (red LED indicator) and exposed on connector J7. No PG-to-EN chain detected between regulators.

---

## 4. Signal Analysis Review

### Voltage Dividers

| R_top | R_bottom | Input | Ratio | Mid-point | Purpose |
|-------|----------|-------|-------|-----------|---------|
| R8 (180k) | R9 (33k) | 5V | 0.155 | U4 FB + C23 | Buck converter feedback |
| R42 (100k) | R43 (100k) | 3.3V | 0.500 | JP8 | Motor L/3 VREF select |
| R44 (100k) | R45 (100k) | 3.3V | 0.500 | JP9 | Motor R/4 VREF select |
| R22 (100k) | R23 (33k) | VIN | 0.248 | JP14 | VIN measurement (ADC) |

The motor VREF dividers (R42/R43 and R44/R45) provide 1.65V to set the DRV8411A current limit. These connect through solder jumpers (JP8/JP9) to allow user override via the expansion headers.

### RC Filters

| Filter | R | C | Cutoff | Type | Purpose |
|--------|---|---|--------|------|---------|
| R21/C31 | 100k | 0.1µF | 15.92 Hz | Low-pass | User button debounce (GPIO36) |
| R1/C13 | 200 | 4.7µF | 169 Hz | Low-pass | ADC VREF filtering |
| R2/C14 | 33 | 4.7µF | 1.03 kHz | Low-pass | RP2350B core supply filtering |

### Crystal Circuit

Y1 (12 MHz) with C17=15pF and C18=15pF load capacitors.
- Effective load: 10.5pF (including ~3pF stray capacitance)
- Target load: 18pF (typical for 12 MHz crystals)
- **Error: -41.7%** — load caps appear undersized

This is flagged as out-of-spec, but the RP2350B has programmable internal load capacitance on its oscillator pins. The external 15pF caps plus internal trim can reach the target. Verify the RP2350B oscillator configuration in firmware.

### Transistor Circuits

9 transistors detected, primarily P-channel MOSFETs (DMG2305UX, 4.2A/20V) used in the power path:

| Ref | Type | Function | Gate pull | Source | Drain |
|-----|------|----------|-----------|--------|-------|
| Q2 | P-FET | Battery path switch | R31=100k | VRAW | VBATT |
| Q4 | P-FET | USB-to-VRAW path | R33=100k | VRAW | VUSB |
| Q6 | P-FET | USB VSYS switch | R35=100k | VSYS | VUSB |
| Q8 | P-FET | 5V rail switch | R37=100k | VSYS | 5V |
| Q9 | P-FET | Power ORing | — | — | — |
| Q5 | PNP dual (BCM857BS) | Power path control | R34=100k | VSYS | Q6 gate |
| Q1, Q3, Q7 | Various | Power path support | — | — | — |

No flyback diodes detected on any transistor circuit. This is acceptable because the motor outputs are driven by the DRV8411A H-bridges (which have integrated protection), not discrete FETs.

### Protection Devices

| Ref | Value | Type | Protected net |
|-----|-------|------|---------------|
| D5 | DT1042-04SO | ESD TVS (4-ch) | USB_D+, USB_D-, VUSB |
| F2 | 6V/0.75A/1.5A | PTC fuse | USB VBUS input |
| F1 | 16V/2.5A/5.0A | PTC fuse | Barrel jack input |

USB data lines are protected by D5 (DT1042-04SO quad ESD suppressor). Both power input paths (barrel jack and USB) have PTC fuses for overcurrent protection.

### Motor Driver Circuits

Two DRV8411A dual H-bridge motor driver ICs (U7, U8) provide four independent motor channels:

| Driver | Channels | Motors | Current sense | VREF source |
|--------|----------|--------|---------------|-------------|
| U7 | L + 3 | J16, J17 (6-pin) | R24/R25=5.1k, JP10/JP11 | R42/R43 divider via JP8 |
| U8 | R + 4 | J18, J19 (6-pin) | R26/R27=5.1k, JP12/JP13 | R44/R45 divider via JP9 |

Each motor channel has dedicated test points (TP5-TP12) on the H-bridge outputs. Current sense resistors (5.1k) connect through solder jumpers, allowing measurement via the MCU's ADC (GPIO40-43). Fault outputs (MOTOR_L/3_FAULT, MOTOR_R/4_FAULT) are active-low with R48/R49=100k pull-ups.

### Addressable LED Chain

D4 (WS2812B) driven from GPIO37/NEOPIXEL. Single-LED chain with data output (NEOPIXEL_OUT) routed to the expansion header for daisy-chaining external LEDs. Estimated current: 60mA at full white.

### LED Audit

| Ref | Color | Series R | Supply | Estimated I |
|-----|-------|----------|--------|-------------|
| D6 | Red | R47=4.7k | VIN | ~0.6mA |
| D2 | Red | R11=4.7k | 3.3V | ~0.3mA |
| D1 | Blue | R10=2.2k | Radio module | — |
| D3 | Red | R12=10k | 5V | ~0.3mA |

LED currents are conservative across the board. All indicators are low-power status LEDs.

### Decoupling Analysis

| Rail | Cap count | Total µF | Bulk | Bypass | Status |
|------|-----------|----------|------|--------|--------|
| 3.3V | 16 | 10.8 | 2x 4.7µF | 14x 0.1µF | Good |
| 5V | 6 | 132.0 | 6x 22µF | None | **Missing bypass** |
| VIN | 7 | 110.2 | 6x 22µF | 1x 0.1µF | OK |
| VSYS | 1 | 4.7 | 1x 4.7µF | None | **Minimal** |
| 1.1V | 4 | 9.6 | 2x 4.7µF | 2x 0.1µF | Good |

The 5V rail has 132µF of bulk capacitance but no 100nF bypass caps. The VSYS rail feeding the 3.3V LDO has only a single 4.7µF cap.

### Sensor Interface

U6 (LSM6DSOX) — 6-DoF IMU on I2C1 (GPIO38/SDA1, GPIO39/SCL1). Two interrupt lines (IMU_INT1, IMU_INT2) detected but not connected to any MCU GPIO in the schematic. Address jumper JP4 present.

---

## 5. ESD Coverage Audit

**This is the headline finding.** Of 19 connectors audited, **17 have zero ESD protection** and 2 have partial coverage. No connector has full coverage.

| Connector | Type | Signal nets | Protected | Unprotected | Coverage |
|-----------|------|-------------|-----------|-------------|----------|
| J2 | USB-C | 11 | 3 (D5, F2) | 8 | Partial |
| J1 | Barrel Jack | 2 | 1 (F1) | 1 | Partial |
| J4 | 20-pin header | 16 | 0 | 16 | **None** |
| J5 | 20-pin header | 16 | 0 | 16 | **None** |
| J7 | 20-pin header | 12 | 0 | 12 | **None** |
| J6 | 20-pin header | 11 | 0 | 11 | **None** |
| J16 | Motor (6-pin) | 4 | 0 | 4 | **None** |
| J17 | Motor (6-pin) | 4 | 0 | 4 | **None** |
| J18 | Motor (6-pin) | 4 | 0 | 4 | **None** |
| J19 | Motor (6-pin) | 4 | 0 | 4 | **None** |
| J13 | Qwiic | 3 | 0 | 3 | **None** |
| J14 | Qwiic | 3 | 0 | 3 | **None** |
| J12 | JST (line sensor) | 2 | 0 | 2 | **None** |
| J15 | JST (line sensor) | 2 | 0 | 2 | **None** |
| J3 | Distance sensor | 2 | 0 | 2 | **None** |
| J8, J9, J10, J11 | Servo (3-pin) | 1 each | 0 | 1 each | **None** |

For an educational robotics board where all connectors are user-facing and will be repeatedly plugged/unplugged, this is a significant gap. The motor connectors (J16-J19) and expansion headers (J4-J7) are particularly exposed to ESD events during cable handling.

**Mitigation context:** The DRV8411A motor drivers have integrated ESD protection on their output pins, providing some inherent robustness on the motor channels. GPIO pins on the RP2350B have internal ESD clamp diodes (typically rated for HBM but not full IEC 61000-4-2). For a board at this price point and educational use case, this level of protection may be acceptable, but it should be documented as a known limitation.

---

## 6. Bus Protocol Compliance

### I2C Buses

| Bus | SDA | SCL | Devices | Pull-ups | Status |
|-----|-----|-----|---------|----------|--------|
| I2C0 | GPIO4/SDA0 | GPIO5/SCL0 | U1 only | None | **FAIL** |
| I2C1 | GPIO38/SDA1 | GPIO39/SCL1 | U1, U6 | None | **FAIL** |

**No external I2C pull-up resistors detected** on either bus. I2C1 connects the IMU (U6) to the MCU and requires pull-ups for reliable operation. I2C0 is exposed on the Qwiic connectors (J13/J14) for user expansion.

However, the RP2350B supports internal GPIO pull-ups (typically 50-80k) that can be enabled in firmware. For short bus runs at standard mode (100 kHz), internal pull-ups may suffice. For the IMU on I2C1, the LSM6DSOX also has internal pull-ups that can be enabled. Note that R14 and R15 (2.2k) are present on the IMU I2C1 bus but are connected as address configuration, not as bus pull-ups.

### USB Compliance

| Check | Status |
|-------|--------|
| CC1 pull-down 5.1k (R29) | PASS |
| CC2 pull-down 5.1k (R28) | PASS |
| D+ series resistor (R4) | PASS |
| D- series resistor (R5) | PASS |
| VBUS ESD (D5) | PASS |
| USB ESD IC | PASS |
| VBUS decoupling | **FAIL** |

USB-C CC resistors correctly configure the port as a sink (device). Data line series resistors and ESD protection are present. VBUS decoupling was not detected near the connector.

---

## 7. Design Observations

### Decoupling Gaps

- **U1 (RP2350B):** GPIO46/VIN_MEAS power pin has no local decoupling capacitor. This pin is used for voltage measurement via ADC and connects to a voltage divider (R22/R23), so a dedicated decoupling cap on the ADC reference would improve measurement accuracy.
- **U5 (RT9080-3.3):** 3V3_EN pin has no local decoupling. The EN pin is typically a high-impedance CMOS input and does not require decoupling, so this is informational only.
- **5V rail:** 132µF bulk but no high-frequency bypass (100nF). The motor drivers switch at moderate frequencies and would benefit from local 100nF caps close to the VIN pins.

### Cross-Domain Signal Analysis

21 cross-domain signals detected. 8 motor control signals cross between the 3.3V MCU domain and the VIN motor driver domain:

| Signal group | Count | Level shift needed? |
|--------------|-------|---------------------|
| Motor L/3 control (U1 → U7) | 4 | Yes (flagged) |
| Motor R/4 control (U1 → U8) | 4 | Yes (flagged) |
| Radio SPI (U1 → U9) | 4 | Yes (flagged) |
| Memory QSPI (U1 → U2, U3) | 6 | No (same 3.3V domain) |
| IMU I2C (U1 → U6) | 2 | No (same 3.3V domain) |
| Flash CS (U1 → U2) | 1 | No |

The DRV8411A logic inputs accept 1.2V minimum high level (V_IH) when powered from 3.3V VREF, so the 3.3V GPIOs from the RP2350B are compatible without level shifting. The cross-domain flags are informational because the motor drivers' digital interface runs at VREF voltage, which is set to 1.65V (below the MCU's 3.3V output).

### Connector Ground Distribution

J2 (USB-C): 13 signal pins per ground pin (recommended: 3 or fewer for EMI control). The USB-C receptacle has multiple ground pins in the spec, but only 1 GND connection is made in this schematic. For a USB 2.0 Full Speed device, this is functionally adequate but suboptimal for EMI.

---

## 8. PDN Impedance

| Rail | Cap count | Total µF | Min Z | At frequency |
|------|-----------|----------|-------|-------------|
| 3.3V | 16 | 10.8 | 34.4 mOhm | 7.94 MHz |
| 5V | 6 | 132.0 | 1.8 mOhm | 1.26 MHz |
| VIN | 7 | 110.2 | 2.2 mOhm | 1.26 MHz |
| VSYS | 1 | 4.7 | 138.4 mOhm | 3.16 MHz |
| 1.1V | 4 | 9.6 | 60.9 mOhm | 5.01 MHz |

The 3.3V rail impedance is moderate (34.4 mOhm) despite 16 capacitors because all bypass caps are 0402 package (higher ESR). The 5V and VIN rails have excellent impedance thanks to the 0805 22µF bulk capacitors with low ESR. The VSYS rail is the weakest at 138 mOhm with only a single 4.7µF cap -- this is the input to the 3.3V LDO and should be stiffened.

---

## 9. Sleep Current Audit

### 3.3V Rail (dominant path)

| Component | Type | Current (µA) | Note |
|-----------|------|-------------|------|
| R1 (200) | Series R to ADC_VREF | 16,500 | **Dominant** — always draws through RC filter |
| R2 (33) | Series R to core filter | 100,000 | Worst-case only; normally loaded by U1 core |
| R11 (4.7k) | Pull-up | 702 | LED indicator circuit |
| R7 (10k) | Pull-up | 330 | RUN pin pull-up |
| R19-R49 (100k) | Various pull-ups | ~33 each | Multiple GPIO/fault pull-ups |
| U5 (RT9080) | LDO Iq | ~15 | Always on |

The R1=200 ohm filter resistor to ADC_VREF is a significant sleep current path (16.5mA worst case) because it forms a low-impedance connection from 3.3V to the ADC reference pin. In practice, the ADC_VREF pin is a high-impedance input, so actual current through R1 is minimal (nanoamps). The worst-case figure is misleading here.

### 5V Rail

| Component | Type | Current (µA) | Realistic |
|-----------|------|-------------|-----------|
| R12 (10k) | Pull-up | 500 | 500 |
| R8 (180k) | FB divider top | 28 | 28 |
| D3 (Red LED) | Indicator | 300 | 0 (GPIO off) |
| U4 (AP63357) | Buck Iq | 20 | 0 (can disable via EN) |

---

## 10. Inrush Analysis

| Regulator | Rail | Output caps | Est. inrush | Soft-start | Status |
|-----------|------|-------------|-------------|------------|--------|
| U5 (RT9080) | 3.3V | 10.8µF | 0.071A | 0.5ms | OK |
| U4 (AP63357) | 5V | 132µF | 0.51A | 1.0ms | **Moderate** |

The AP63357 output stage has 132µF of ceramic capacitance, resulting in moderate inrush (0.51A). The AP63357 has internal soft-start (typically 0.64ms), which limits the actual inrush. With a 2.5A PTC fuse (F1) on the barrel jack input, this is within margins.

---

## 11. Suggested Certifications

| Standard | Region | Reason |
|----------|--------|--------|
| FCC Part 15 Subpart B | US | Unintentional radiator (switching converter, wireless module) |
| CISPR 32 / CE EMC Directive | EU | EMC compliance for all electronic devices |

The RM2 wireless module (U9) is a pre-certified module. For the complete product, the host board still requires intentional radiator testing if the module certification conditions are not met (antenna type, ground plane size, etc.).

---

## 12. ERC Warnings

| Warning | Net | Assessment |
|---------|-----|------------|
| No driver | RUN | Expected — R18 pull-up + external reset via J6. Passive network drives the RP2350B RUN pin. |
| No driver | MOTOR_L/3_VREF | Expected — voltage divider output (R42/R43) through jumper JP8. No active driver needed. |
| No driver | MOTOR_R/4_VREF | Expected — voltage divider output (R44/R45) through jumper JP9. |
| No driver | ADC_VREF | Expected — RC filter output (R1/C13). Passive network provides filtered reference. |

All ERC warnings are false positives from passive-only networks driving input pins.

### PWR_FLAG Warnings

7 power rails missing PWR_FLAG symbols: GND, VIN, 1.1V, 5V, VRAW, VUSB, VSYS. KiCad ERC will flag these. Add PWR_FLAG symbols to silence the warnings.

---

## 13. Test Coverage

11 test points covering motor outputs and USB data lines:

| Ref | Net | Function |
|-----|-----|----------|
| TP1 | USB_D+ | USB data debug |
| TP2 | USB_D- | USB data debug |
| TP4 | VBATT | Battery input |
| TP5 | MOTOR_L_OUT+ | Motor L positive |
| TP6 | MOTOR_L_OUT- | Motor L negative |
| TP7 | MOTOR_3_OUT- | Motor 3 negative |
| TP8 | MOTOR_3_OUT+ | Motor 3 positive |
| TP9 | MOTOR_4_OUT+ | Motor 4 positive |
| TP10 | MOTOR_4_OUT- | Motor 4 negative |
| TP11 | MOTOR_R_OUT- | Motor R negative |
| TP12 | MOTOR_R_OUT+ | Motor R positive |

### Key Nets Without Test Points

- **Power rails:** 3.3V, 5V, VIN, VSYS, VRAW, VBATT, VUSB, 1.1V, 3V3_EN, GPIO46/VIN_MEAS (10 rails)
- **I2C buses:** GPIO4/SDA0, GPIO5/SCL0, GPIO38/SDA1, GPIO39/SCL1

No SWD/JTAG debug connector detected. The board exposes SWDCLK and SWDIO signals but lacks a dedicated debug header. Programming is handled via USB (UF2 bootloader).

---

## 14. Issues and Recommendations

| # | Severity | Category | Finding | Recommendation |
|---|----------|----------|---------|----------------|
| 1 | **WARNING** | ESD | 17 of 19 connectors have zero ESD protection. All expansion headers, motor connectors, servo ports, and sensor ports expose MCU GPIOs directly. | Add TVS arrays on high-risk connectors (J4-J7 expansion headers, J16-J19 motor). At minimum, add ESD protection on the Qwiic I2C ports (J13/J14) since these are hot-pluggable. |
| 2 | **WARNING** | Protocol | No external I2C pull-up resistors on either I2C bus. I2C1 (IMU) and I2C0 (Qwiic expansion) rely on internal pull-ups. | Add 2.2k-4.7k pull-ups to 3.3V on SDA1/SCL1 for reliable IMU communication. For Qwiic connectors, pull-ups are typically provided by the connected device. |
| 3 | **WARNING** | Power | 5V rail (132µF) has no 100nF bypass capacitors. All caps are 22µF bulk (0805). Motor driver switching transients need high-frequency bypass. | Add 100nF 0402 caps near U7 and U8 VIN pins. |
| 4 | **WARNING** | Voltage | AP63357 feedback divider (R8=180k/R9=33k) yields 4.94V with correct Vref=0.765V. Analyzer heuristic used 0.6V and flagged 22.5% mismatch. | Verify Vout with actual Vref from AP63357 datasheet. The divider is correctly designed for ~5V output. |
| 5 | Medium | Crystal | Y1 (12 MHz) load capacitor error -41.7% (10.5pF effective vs 18pF target). | Verify RP2350B internal oscillator load cap trim is configured in firmware to compensate. External 15pF + internal trim should reach 18pF. |
| 6 | Medium | Decoupling | VSYS rail has only 1x 4.7µF cap (PDN impedance 138 mOhm). This rail feeds the 3.3V LDO (U5). | Add a 100nF bypass cap on VSYS near U5 input. |
| 7 | Medium | USB | VBUS decoupling not detected near USB-C connector (J2). | Add 4.7µF + 100nF on VUSB close to J2. |
| 8 | Medium | Sensor | IMU interrupt lines (IMU_INT1, IMU_INT2) are not connected to MCU GPIOs. | If motion interrupts are needed, route INT1/INT2 to available GPIOs. |
| 9 | Low | ERC | 7 power rails missing PWR_FLAG symbols. | Add PWR_FLAG to GND, VIN, 1.1V, 5V, VRAW, VUSB, VSYS. |
| 10 | Low | Sourcing | 0% MPN coverage (0/164 components). BOM lock status: FAIL. | Assign MPNs for all active components and critical passives before production. |
| 11 | Low | Assembly | 89 hard-to-solder components (0402 package). Moderate complexity score (58/100). | Appropriate for machine assembly. Document 0402 hand-rework procedures. |
| 12 | Info | Debug | No SWD/JTAG debug header. SWDCLK/SWDIO routed but not broken out to a connector. | Consider adding a Tag-Connect or 10-pin SWD header for production debug. |
| 13 | Info | Ground | USB-C connector J2 has 13 signal pins per ground pin. | Connect additional GND pins on J2 footprint for improved EMI. |
| 14 | Info | BOM | 8 single-use passive values — potential for consolidation. | Review single-use values for standardization opportunities. |

---

## 15. Positive Findings

1. **Comprehensive motor driver design** — 4 independent H-bridge channels with per-channel current sensing, VREF adjustment via solder jumpers, fault outputs with pull-ups, and 8 dedicated test points on motor outputs
2. **Robust power input ORing** — Battery and USB power paths use DMG2305UX P-FETs with BCM857BS dual PNP for proper source selection, preventing back-feeding between inputs
3. **USB-C correctly configured** — CC1/CC2 pull-downs (R28/R29=5.1k) properly identify the board as a USB sink device. Series resistors (R4/R5) on data lines. ESD protection (D5, DT1042-04SO) on D+/D-/VBUS
4. **Generous buck converter output capacitance** — 132µF on the 5V/VIN rail and 110µF on the motor power input provide excellent transient response for motor driver load steps
5. **Well-filtered ADC reference** — R1=200/C13=4.7µF (169 Hz cutoff) provides clean ADC_VREF for accurate voltage measurements. R2=33/C14=4.7µF (1.03 kHz) filters the core supply.
6. **Power Good indicator** — U4 (AP63357) PG output drives D6 (red LED) and is exposed on the expansion header for firmware monitoring
7. **Single ground domain** — 132 components connected to a unified GND net with no split ground issues. Clean return path for all circuits.
8. **Extensive I/O expansion** — Four 20-pin headers (J4-J7) expose motor control, encoder inputs, servo outputs, ADC, I2C, SPI, UART, power, and GPIO signals for educational experimentation
9. **Solder jumper configurability** — 14 solder jumpers (JP1-JP14) allow users to configure I2C addresses, current sense enable, VREF selection, and VIN measurement without board modifications
10. **Test point coverage on all motor outputs** — All 8 H-bridge output nets have dedicated 1.0mm test points for oscilloscope probing during motor tuning

---

## 16. Summary

This robot controller board is a well-designed educational platform with a capable power architecture and thoughtful motor driver implementation. The AP63357 buck converter with generous output capacitance handles the dynamic loads of four motor channels, and the power path ORing between battery and USB is properly implemented with P-FET switches.

The primary concern is **ESD protection coverage**: 17 of 19 connectors have no ESD protection, and this is a board designed to be handled by students who will frequently plug and unplug motors, sensors, and expansion cables. While the RP2350B and DRV8411A have some internal ESD tolerance, dedicated TVS protection on the most-exposed connectors (Qwiic I2C, motor ports, expansion headers) would significantly improve field reliability.

Secondary concerns include missing I2C pull-ups (can be mitigated in firmware via internal pull-ups), missing high-frequency bypass caps on the motor power rail, and the crystal load capacitor sizing (likely compensated by RP2350B internal trim).

The design is functional as-is for educational use. For production hardening, address the ESD gaps on external connectors and add bypass capacitors on the 5V and VSYS rails.

**Verdict: Functional, with ESD and decoupling improvements recommended before high-volume production.**
