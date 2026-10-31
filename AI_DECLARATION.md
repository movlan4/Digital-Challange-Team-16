# AI Usage Declaration

**Project:** Smart Transit and Crowdsourcing Engine
**Event:** Digital Azerbaijan Challenge 2026, Semifinal (4 hours)
**Team:** Team 16
**Date:** 31 October 2026

---

## 1. Summary

Our team used AI as a **pair-programming and engineering assistant** to work faster during a time-limited event. The team set the problem framing, the requirements and the evaluation targets, and reviewed and accepted every deliverable. AI helped with architecture proposals, writing code, writing tests, browser-based QA and drafting documentation. All AI output was checked by running it: the automated test suite and a manual walkthrough of both UIs in a real browser.

## 2. Tools used

| Tool | Provider / model | Used for |
|---|---|---|
| **Claude Code** (Claude desktop app, Code tab) | Anthropic, Claude Opus 5.5 | Architecture, backend and frontend code, unit/API tests, Docker setup, browser QA, documentation drafts |

> *Team: if you also used other tools (e.g. ChatGPT, GitHub Copilot, Cursor), add a row for each one, with the prompts you used, before you submit. Only list tools you actually used.*

## 3. How AI was used, by phase

### 3.1 Architecture selection
* The AI first checked the development machine. It found that **Node.js and Docker were not installed, but Python 3.14 was**. Based on that it suggested **FastAPI** for the backend (allowed by the brief) and a **React frontend with no build step** (React 18 UMD + `htm`), so the prototype could run and be tested on the competition PC and also in Docker.
* It proposed a layered design (thin API routers → `TransitEngine` service layer → pure domain services → SQLite repository), Server-Sent Events for real-time push, and an injectable clock so the 10-minute rule could be tested without waiting.
* **Human decision:** the team accepted the stack, kept the SQLite + in-memory split, and approved the bucket-based definition of "continuous 10 minutes" (2-min buckets, ≥ 60 % HIGH, gaps break the streak). This was chosen as a defensible reading of the brief that handles noisy crowdsourced data.

### 3.2 Coding
* The AI generated the backend modules (`anomaly.py`, `congestion.py`, `simulation.py`, `network.py`, `repository.py`, `engine.py`, API routers) and the two React UIs.
* Iterations driven by review:
  * The congestion fusion weights were changed from 70/30 to **80/20 (crowd/GPS)**, so that unanimous passenger HIGH reports are not overruled by buses that still look free-flowing.
  * The map auto-fit logic was fixed so it refits only when the selected route changes.
  * New-alert notifications were changed to fire only for alerts raised *after* the initial snapshot loads.
  * Timestamps are stored in a fixed-width UTC format so SQL string comparison matches time order.

### 3.3 Testing
* The AI wrote **25 pytest tests**. They cover the anomaly rule (sustained / short / gap / reset / noise / stale / invalid config), the alert lifecycle (no duplicates, auto-resolve), every API endpoint including error codes (404/409/422/429), and an end-to-end flow (API reports over 10.5 simulated minutes → cron → alert → acknowledge).
* The suite was run after every change: **25 passed** in v1, **39** after the v2 rework (section 4.2), **42** after v3 (section 4.3), **57** after v4 (section 4.4), and **67** after the consensus change (section 4.5).
* **Browser QA:** the AI started the server and used both UIs in an embedded browser: route/stop selection, live ETAs, report submission, the rate-limit (429) message, the rush-hour demo raising the alert, and acknowledging it. It also checked the browser console for errors.

### 3.4 Documentation
* The AI drafted `README.md`, this declaration and `TECHNICAL_EXPLANATION.txt`. The team reviewed them for accuracy against the running system.

## 4. Prompts used

### 4.1 Main prompt (architecture + full implementation)
> Act as an expert Full-Stack Developer, System Architect, and QA Engineer. We are currently participating in the 4-hour semifinal of the "Digital Azerbaijan Challenge 2026" competition. Our task is to develop a functional prototype of a micro-system named "Smart Transit and Crowdsourcing Engine" to manage public transport congestion during peak hours in Baku.
>
> To maximize our score, you must build the project based on the following evaluation criteria: Functional correctness (35%), Architecture and code quality (20%), Testing and reliability (15%), Purposeful use of AI tools (15%), and Usability and application capabilities (15%).
>
> I need you to provide the following modules and documents fully functional, tested, and ready for deployment (following Clean Code principles):
>
> 1. TECHNOLOGY STACK & ARCHITECTURE: Backend: Node.js/Express or Python/FastAPI (Use Mock data or SQLite). Frontend: React.js (Two separate, simple, and clear interfaces for the Passenger and the Operator). Containerization: Docker (A Docker Compose file is mandatory).
>
> 2. CORE MODULES: Passenger Module (Frontend + API): When a route or stop is selected, display the real-time bus location and ETA (use mock data). Provide a feature for passengers to report the congestion level as "Low / Medium / High". Operator Module & Analytics Engine: Update the congestion list or map across routes in real-time based on incoming crowdsourcing data and GPS coordinates. Anomaly Alert Logic (Crucial): If the backend API records a "High" congestion level for a specific route over a continuous 10-minute period (write a background process/cron to simulate this), automatically generate an alert on the operator panel: "Additional bus reserve must be allocated". Tests: Write at least 3 Unit Tests (using Jest or PyTest) for the 10-minute anomaly detection logic and API endpoints.
>
> 3. DELIVERABLE DOCUMENTS: Readme.md, AI Declaration (Markdown), Technical Explanation Document (Text format).
>
> Execution Plan: Start by providing the project directory structure, the Backend API code, and the Anomaly detection logic. Afterward, provide the Frontend code and the required documentation.

*(Lightly condensed; the full original prompt was used.)*

### 4.2 Second prompt (v2: real KMZ data, auth, per-bus analytics)
> Role & Objective: Act as an expert full-stack developer. I am building a bus tracking and transit management platform. I will provide `.kmz` data containing bus movement trajectories, including geographical paths, stops, and road segments.
> Data & Routing: Integrate all bus stops and routes from the provided data. Build a route simulation for the buses based on this data. Utilize open-source mapping data (e.g., OpenStreetMap) to accurately align car and bus routes where the provided data might be incomplete.
> UI/UX: Replace plain text bus numbers with a visual "Bus Icon + Bus Number" component. Allow users to select their current bus stop to view all buses passing through it. Implement a dual-search function for bus stops and bus numbers. Enable interactive selection of bus stops directly from the map.
> Authentication: Strictly separated Passenger and Operator interfaces. Operator access requires Admin Username and Password; Passenger access requires Email and Password.
> Analytics & Reporting: Calculate the density of individual buses instead of overall route density. Passenger reports must appear on the Operator dashboard in real time. Disable any automated closing of reports. Strict deduplication: one passenger submission registers as exactly one report.

*(Condensed; attached file: `buslines.kmz`.)*

**How AI was used in v2:**
* **Data analysis:** the AI inspected the KMZ (isolated extraction, scripted parsing) and found that it had **243 line geometries but no stop points**, with 7 lines split into parts. This finding changed the plan: stops are taken from **OpenStreetMap (Overpass)** and gaps are filled with **OSRM** road routing. When Overpass timed out from the competition network, the AI added mirrors and a fallback (stops derived from the geometry, named by OSM street). A later run reached Overpass and used the real OSM stops.
* **Design decisions** proposed by the AI and accepted by the team: per-bus density = simulated on-board load fused with that bus's passenger reports; idempotency key + database `UNIQUE` constraint for exactly-once reports; separate cookies/roles for passengers and operators; one alert per line, closed only manually, with a fresh streak required after resolution.
* **Bugs found during AI-driven browser QA and fixed:** an `await` inside a non-async callback (blank operator page); the overcrowding demo not raising an alert, because real recent samples diluted the injected HIGH samples (fixed, with a regression test added).
* **Verification:** 39 pytest tests passing. Full walkthrough in the browser: registration, dual search, stop selection from the map, a report double-click producing one stored report, the report appearing live on the operator dashboard, manual report and alert handling.

### 4.3 Third prompt (v3: real inputs only, unified login, admin panel, map)
> Disable the automated simulation of bus density, user reports, and similar metrics; density and reports should only trigger and update when manually submitted by users. Implement a unified login screen that routes users to their dashboard by role. Create a Master Admin panel to view registered users and assign roles such as "Operator". Develop a "Forgot Password" flow with an e-mailed verification code. Optimize map rendering on both screens; hide individual buses when zoomed out (zoom-based visibility or clustering).

*(Condensed.)*

**How AI was used in v3:**
* **Design choices** proposed by the AI and accepted by the team:
  * Only bus *positions* stay simulated, because no GPS feed exists. Crowdedness becomes `null` until a passenger reports a bus, and reports older than 15 minutes stop counting.
  * Users and roles are merged into one table. Roles are re-read on every request, and a role change signs the user out.
  * Reset codes are hashed, expire, are rate-limited, and the endpoint does not reveal which accounts exist.
  * Mail goes out over SMTP when configured, and to the server log in demo mode.
  * Buses are shown by zoom level with viewport culling, rather than clustered.
* **Tuning from browser QA:** at zoom 14 central Baku still showed about 125 bus markers and dense stops, so the threshold was raised to zoom 15 (about 20 buses in view).
* **Verification:** 42 pytest tests passing. Full walkthrough in the browser: register → passenger view; zoomed-out map shows no buses, zoomed-in shows only nearby buses; a report changes only that bus from "No reports" to "High"; admin login → operator view with the live report; the admin promotes the user; forgot-password with the code from the server log; the promoted user's next login opens the operator view; an operator is redirected away from the admin and passenger pages, and those APIs return 403.

### 4.4 Fourth prompt (v4: final UI standard, stop dwell, hybrid density)
> Implement Montserrat globally; replace the text brand with the attached logo; remove all emoji icons and integrate a professional icon library; use a standard vector "bus stop" icon for stops; format bus index numbers ("-02" → "2") inside a square. Buses must physically halt at stops for a realistic duration. Overhaul the density map and route lists so density fuses passenger reports AND GPS telemetry; factor in GPS behaviour (slower speeds, delays at stops, clustering) to automatically estimate route density.

*(Condensed; the team's logo image was attached and is used as `frontend/assets/logo.png`.)*

**How AI was used in v4:**
* **Library choice:** the AI checked which icon CDNs load from the competition network and which actually include a bus-stop icon. Font Awesome Free has none. **Tabler Icons** (MIT) has `ti-bus-stop`, and all 32 icon names used were verified to exist in the chosen version.
* **Simulation and estimator design** proposed by the AI and accepted by the team:
  * Dwell time is driven by passengers waiting since the line's previous bus, which reproduces bus bunching.
  * A pure GPS crowd score is built from dwell time, speed and headway.
  * The GPS score is fused with passenger reports (70/30), and lines are aggregated from their buses.
  * This reverses v3's "reports only" rule, as the new requirement asked. Crowding is still *inferred* from telemetry, never injected.
* **Calibration:** the AI simulated 60 minutes on the full 243-line network, off-peak and at rush hour. Rush-hour dwells hit the 120 s cap, so demand was reduced, and the dwell baseline was raised so that off-peak traffic reads as mostly LOW.
* **Fixed during browser QA:**
  * The browser served a stale stylesheet. Static files are now sent with `Cache-Control: no-cache` (ETag revalidation) by both FastAPI and nginx.
  * The wide per-bus table caused horizontal page overflow; grid children now get `min-width: 0`.
  * The operator map was missing stop markers; they were added.
* **Verification:** 57 pytest tests passing (15 new, covering stop halting, dwell model, rush-hour clock, ETA, GPS score, fusion, line density and bus-number formatting). The passenger, operator and login screens were checked in the browser.

### 4.5 Fifth prompt (weighted report consensus)
> Currently a single passenger "High" report instantly makes the bus "High". Implement a coefficient/threshold (weighted) system: aggregate multiple reports; the status should only change after a threshold of multiple reports within a timeframe; use a weighted average or point-based coefficients so density increases organically by consensus and single outlier reports are ignored. Do not alter anything else.

**How AI was used:**
* **Design:**
  * One vote per passenger, with recency-weighted votes (half-life 5 min).
  * Threshold: ≥ 3 distinct passengers and ≥ 1.5 vote weight within the 15-min window.
  * Outlier rejection against the weighted median, applied only to a < 40 % minority.
  * A confidence ramp scales the consensus's share of the fusion.
* **Trade-off flagged by the AI and accepted by the team:** with the old fixed 70/30 fusion, no number of agreeing passengers could make a bus HIGH while its GPS looked normal. So the full-confidence share was raised to 85 %. That way about 4 agreeing passengers reach HIGH, while 1–2 change nothing.
* **Scope:** only the density logic changed. The single UI change is the operator table's footnote, which describes this logic and would otherwise be wrong. The API gained additive fields (`reporters`, `consensus_reached`, `reporters_needed`, `report_confidence`, `outlier_reports`).
* **Verification:** 67 pytest tests passing (10 new in `test_consensus.py`). A live check on the running server: on a GPS-LOW bus, HIGH reports from 4 different test passengers gave Low → Low → Medium → High.

### 4.6 Follow-up prompts
> *Team: add any further prompts you used here.*

## 5. Responsibility and verification

* The team is responsible for the submitted code. We understand how it works and can explain every module, including the anomaly algorithm, at the defence.
* No AI output was accepted without running it. Correctness is backed by the automated test suite and by a manual demo of the running system.
* All live data (GPS, passenger reports) is **simulated mock data**. No personal data is collected; passengers are identified only by a random anonymous device ID, used for rate limiting.
* Third-party libraries (FastAPI, Pydantic, Uvicorn, React, htm, Leaflet, OpenStreetMap tiles) are open source and used under their licences.
