# Smart Transit and Crowdsourcing Engine

**Digital Azerbaijan Challenge 2026 · Team 16**

A bus tracking and transit management platform for **Baku**. It is built on the official bus-line data (`buslines.kmz`: **243 line geometries, 239 line numbers**), enriched with **OpenStreetMap**: **1,715 real bus stops**, with OSRM road routing to fill gaps in the line data.

* **One login screen for everyone.** After sign-in, each user is routed to the dashboard for their role: **Passenger** view, or **Operator** view (operators and the master admin).
* **Passengers** find a stop or a bus number, see every bus passing through their stop with a live ETA, and report how crowded a specific bus is (Low / Medium / High).
* **Hybrid crowding engine.** Each bus's crowdedness fuses **passenger reports** with **GPS telemetry**: long stops, slow driving, and a large gap to the bus ahead (bunching). Lines get a crowding level aggregated from their buses. Crowding is always *inferred* from these signals, never injected.
* **Realistic movement.** Buses physically halt at every stop for a dwell time that depends on how many passengers have been waiting there.
* **Operators** receive reports in real time and handle them by hand (nothing closes automatically). They see per-bus and per-line crowding and get *"Additional bus reserve must be allocated"* alerts when a bus stays HIGH for 10 minutes.
* **The master admin** sees all registered users and assigns the **Operator** role.
* **Forgot password.** Users enter their e-mail, receive a 6-digit verification code, and set a new password.

---

## 1. Quick start

### Option A: Docker

```bash
cp .env.example .env      # set ADMIN_PASSWORD, optionally SMTP for real e-mails
docker compose up --build
```

| URL | What |
|---|---|
| http://localhost:8080 | **Login** (all roles) → redirected to the right dashboard |
| http://localhost:8080/passenger.html | Passenger view |
| http://localhost:8080/operator.html | Operator view (operators + master admin) |
| http://localhost:8080/admin.html | Master admin: users & roles |
| http://localhost:8000/docs | Swagger / OpenAPI |

Tests in a container: `docker compose --profile test run --rm tests`

### Option B: Local runtime (Python 3.11+, no Node.js)

```powershell
.\run-local.ps1           # app on http://localhost:8000 (reads .env, falls back to .env.example)
.\run-local.ps1 -Test     # 67 tests
.\run-local.ps1 -Import   # rebuild network data from the KMZ + OpenStreetMap
```

**Accounts.** The master admin is seeded from `ADMIN_EMAIL` / `ADMIN_PASSWORD` (demo values in [`.env.example`](.env.example); if no password is set, a random one is generated and printed to the log once). Everyone else self-registers on the login screen as a passenger, and the master admin can promote any user to operator.

**Password-reset e-mails.** Set `SMTP_HOST` / `SMTP_PORT` / `SMTP_USERNAME` / `SMTP_PASSWORD` / `MAIL_FROM` to send real e-mails. Without SMTP (demo mode), the verification code is written to the **server log** (`[email:console] … Your verification code is 123456`).

> The browser needs internet access for React/Leaflet (unpkg CDN) and the OpenStreetMap tiles. The network data is bundled, so the backend runs offline.

### Demo script (4 minutes)

1. Open http://localhost:8000 and choose **Create an account**. You land in the **Passenger** view.
2. The map starts zoomed out and uncluttered: buses are hidden behind a *"Zoom in to see 548 buses"* hint. Zoom in (or click the hint) and only the buses in that area appear.
3. Search **"28 May"** and pick a stop to see every line and bus passing through it. Buses halted at a stop say *At a stop*. After a couple of stops each bus has a GPS-based level (e.g. *Low · GPS*). Press **Report** → **High** → **Send**: one report alone does **not** change the status. Once three different passengers report the same bus, its level rises (Medium after 3, High after about 4 agreeing passengers). Set `SIM_RUSH_HOUR=always` to demo peak-hour behaviour at any time.
4. **Sign out**, then sign in as the master admin (`.env.example`). You land in the **Operator** view: the report is in the live inbox, and *Crowdedness by bus* lists the reported bus. Click **Start handling** → **Resolve…**.
5. Click **User management** → set the passenger's role to **Operator** → **Save**. That user is signed out; at their next login they land in the operator view.
6. Sign out → **Forgot password?** → enter the e-mail → copy the code from the e-mail (or the server log) → set a new password → sign in.

---

## 2. Data pipeline: KMZ + OpenStreetMap

`backend/tools/import_kmz.py` runs once, and its output (`backend/app/data/network.json`) is committed:

1. **Parse the KMZ.** 243 placemarks (line number, FID and length only). Duplicate numbers are kept as variants (`L1`, `L1-2`).
2. **Repair geometry.** Multi-part lines are chained in travel order. Gaps (> 25 m between parts, > 1 km inside a part) are filled with **OSRM** road geometry built on the OpenStreetMap road network. A fill more than 2.5× longer than the gap is rejected. Result: 13 gaps filled, 9 kept straight.
3. **Stops.** The KMZ has no stop points. OSM bus stops come from **Overpass** and are snapped to every line within 35 m, giving 1,715 stops. Unnamed stops get their OSM street name. If Overpass is unreachable, stops are derived from the line geometry instead.
4. **Simplify** (Douglas-Peucker, 3 m) and name each line *start – turnaround*.

## 3. Architecture

```
            ┌──────────── index.html: unified login (all roles) ────────────┐
            │  POST /api/auth/login → {role, home} → redirect to home       │
            └───────┬───────────────────────┬──────────────────────┬────────┘
                    ▼                       ▼                      ▼
          passenger.html          operator.html                admin.html
          (role passenger)        (operator, admin)            (admin only)
            │ SSE /api/stream       │ SSE /api/operator/stream   │ /api/admin/*
            ▼                       ▼                            ▼
┌──────────────────────────── FastAPI backend ─────────────────────────────┐
│ api/       auth · admin · network · passenger · operator · stream        │
│            deps.py: require_roles(...) → 401 signed out / 403 wrong role  │
│ engine.py  TransitEngine (service layer, business rules, background jobs)│
│ services/  network · simulation (GPS feed, stop dwell) · telemetry (GPS  │
│            crowd estimate) · density (reports + GPS fusion)              │
│            anomaly · auth (users, roles, reset codes) · mailer · events  │
│ repository.py → SQLite: users, user_sessions, password_resets,           │
│                 bus_reports (UNIQUE passenger+submission), bus_alerts    │
└──────────────────────────────────────────────────────────────────────────┘
```

### Key behaviours

| Requirement | Implementation |
|---|---|
| Buses halt at stops | `BusFleet.tick` stops each bus exactly at the next stop pass and holds it for a dwell time: 12 s + 2.5 s per boarding passenger, capped at 120 s. Passengers accumulate since the line's previous bus, more at hub stops (served by many lines) and ×2.2 at rush hour (Baku time). A late bus therefore waits longer and the bus behind catches up: realistic **bunching**. ETAs include the expected dwell at stops on the way |
| GPS-driven crowd estimate | `telemetry.gps_crowd_score` (pure function): **50 %** dwell time vs. 30 s baseline, **20 %** slowness vs. free-flow 24 km/h, **30 %** gap to the bus ahead vs. even spacing (a bus far behind the previous one picks up everyone waiting). Needs ≥ 2 observed stops; all features come from raw GPS positions, so it works unchanged with a real AVL feed |
| Report consensus (weighted) | **A single report never changes a bus's status.** `DensityEstimator` treats reports as votes: **one vote per passenger** (their latest report in the 15-min window). Each vote's weight halves every 5 min. Reports count only once **≥ 3 different passengers** voted with a combined weight **≥ 1.5** (recent enough); until then they are *pending* and the status comes from GPS alone. **Outliers**, i.e. votes two levels from the weighted median (a lone HIGH among LOWs), are ignored while they carry < 40 % of the weight. The rest are averaged by weight |
| Hybrid fusion | `density.fuse`: the consensus's share grows with its evidence (**confidence = vote weight / 4**, max 1) up to **85 %**, against GPS. Density therefore rises gradually: on a bus whose GPS looks normal, 1–2 HIGH reports change nothing, 3 lift it to Medium, and about 4 to High. Either signal alone if the other is missing; *No data* only when neither exists. Every level carries its source (`reports+gps`, `reports`, `gps`) |
| Line (route) density | Mean hybrid score of the line's buses, with the number of buses at High and the average stop time → `/api/routes/density` and the live `routes` SSE event. The operator map colours every line by it, and passengers see it on line chips and search results |
| Alert rule | The 10-minute rule runs over hybrid density samples (taken every 10 s; 2-min buckets, each ≥ 60 % HIGH). One alert per line; closed only by an operator |
| Unified login + role routing | `POST /api/auth/login` returns `home` for the role (passenger → `/passenger.html`, operator/admin → `/operator.html`). Every page checks `/api/auth/me` and redirects a wrong role to its own dashboard |
| Master admin panel | `admin.html`: list and search users, filter by role, assign **Passenger / Operator**. Guard rails: the admin's own role cannot change and the admin role cannot be assigned. A role change signs the user out, so their next login opens the new dashboard. Roles are read from the database on every request |
| Forgot password | `POST /api/auth/forgot-password` e-mails a 6-digit code. The response is identical for unknown e-mails (no account enumeration), with at most one code per minute. `POST /api/auth/reset-password`: the code is valid for 15 min, allows 5 attempts and works once; only its hash is stored; all sessions are revoked after a reset |
| Map without clutter | Buses and stops render only at **zoom ≥ 15** and only **inside the current viewport** (+15 % margin); markers are created and removed as you pan. Zoomed out, a *"Zoom in to see N buses"* hint replaces them. The passenger line view has a locate button (crosshair icon) that zooms to a bus |
| UI standard | **Montserrat** everywhere (Google Fonts). **Tabler Icons** webfont (MIT) everywhere, no emoji; stops use Tabler's vector `bus-stop` icon on the map and in lists. The bus badge is *[bus icon] line number [bus number in a square]*, with the number formatted from `88-02` to **2** (leading dashes and zeros stripped). The site logo lives at `frontend/assets/logo.png`; replace that file to rebrand. If it is missing, a text fallback is shown |
| Exactly-once reports | Client `submission_id` + `UNIQUE(passenger_id, submission_id)` + `ON CONFLICT DO NOTHING`. Retries return the original report (`duplicate: true`) |
| Reports never auto-close | OPEN → IN_PROGRESS → RESOLVED, changed only by operators; each change records who made it |
| Security | PBKDF2-SHA256 passwords; only a SHA-256 hash of each session token is stored; HttpOnly + SameSite cookie; 5 failed logins → 429 |

## 4. API (summary)

| Method | Path | Who |
|---|---|---|
| POST | `/api/auth/login` · `/register` · `/logout` · `/forgot-password` · `/reset-password`, GET `/me` | public / signed in |
| GET / PATCH | `/api/admin/users?q=&role=` · `/api/admin/users/{id}` `{role}` | master admin |
| GET | `/api/search` · `/api/routes` · `/api/routes/{id}` · `/api/routes/lines` · `/api/stops` · `/api/stops/nearest` · `/api/stops/{id}` · `/api/stops/{id}/arrivals` · `/api/buses` · `/api/stream` | any signed-in user |
| POST / GET | `/api/passenger/reports` (idempotent) | passenger |
| GET / PATCH | `/api/operator/reports` · `/{id}` · `/summary` | operator, admin |
| GET | `/api/operator/density?level=&reported_only=` | operator, admin |
| GET / POST | `/api/operator/alerts` · `/{id}/acknowledge` · `/{id}/resolve` · `/api/operator/anomaly/run` · `/api/operator/stream` | operator, admin |

## 5. Tests

**67 pytest tests**, all passing:

| File | Covers |
|---|---|
| `test_auth.py` | Sign-in required everywhere; registration creates passengers; **one login routes each role to its dashboard**; validation, duplicates, throttling; **role-based access (401/403)**; **admin lists/searches users and promotes/demotes** (the user is signed out, and the next login opens the new dashboard); admin guard rails; **forgot password**: e-mailed code, wrong code, success revokes sessions, single use, **no account enumeration**, 15-min expiry, 5-attempt lockout, one code per minute |
| `test_consensus.py` | **A single HIGH report changes nothing**, with or without GPS; the status changes only when the 3rd different passenger agrees; repeated reports from one passenger = one vote; a lone outlier is ignored but a 40 % minority is not; stale reports don't reach the threshold; density rises gradually with each agreeing passenger (pending → Medium → High); thresholds are configurable; the fusion share scales with confidence |
| `test_simulation.py` | **Bus halts exactly at the stop coordinates, stays for the whole dwell, then resumes**, and the stop duration is recorded as telemetry; dwell grows with waiting passengers and at rush hour; rush hour follows Baku time; ETA includes dwell; **GPS crowd score** (LOW / MEDIUM / HIGH from dwell, speed and bunching; needs enough data); **fusion weights**; **line density aggregation**; bus number formatting (`88-02` → `2`) |
| `test_anomaly.py` | 10-min rule edge cases; normal off-peak GPS traffic raises no alerts; sustained HIGH → one alert per line that never auto-closes; fresh streak after manual resolution; end to end through the API with 22 different passengers |
| `test_reports.py` | Exactly-once storage (API + DB constraint); rate limit; validation; live push to operators only; manual-only closing; **reports fused with GPS telemetry (reports → reports+gps → gps after the report expires)** |
| `test_network.py` | Dual search; stop view; nearest stop; geometry; density ranking (reported buses first); manual alert handling; integrity of the KMZ network |

## 6. Configuration

| Variable | Default | Meaning |
|---|---|---|
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | admin@transit.az / *random* | Master admin |
| `SMTP_HOST` `SMTP_PORT` `SMTP_USERNAME` `SMTP_PASSWORD` `SMTP_STARTTLS` `MAIL_FROM` | *(empty → log)* | Password-reset e-mail |
| `SECURE_COOKIES` | false | Set to true behind HTTPS |
| `DENSITY_WINDOW_MINUTES` | 15 | How long a passenger report describes a bus (the consensus timeframe) |
| `REPORT_CONSENSUS_MIN_REPORTERS` · `REPORT_CONSENSUS_MIN_WEIGHT` | 3 · 1.5 | Distinct passengers and recent vote weight needed before reports affect the status |
| `REPORT_FULL_CONFIDENCE_WEIGHT` | 4.0 | Vote weight at which the consensus reaches its full 85 % share |
| `ANOMALY_WINDOW_MINUTES` · `ANOMALY_BUCKET_MINUTES` · `ANOMALY_HIGH_RATIO` · `ANOMALY_CHECK_INTERVAL_SECONDS` | 10 · 2 · 0.6 · 10 | Alert rule |
| `REPORT_COOLDOWN_SECONDS` | 60 | Per passenger, per bus |
| `SIMULATION_TICK_SECONDS` · `SIMULATION_SPEED_MULTIPLIER` | 2 · 3 | GPS feed simulator |
| `SIM_RUSH_HOUR` | auto | Rush-hour demand & traffic: `auto` (Baku 07:30–09:30, 17:30–19:30 on weekdays) / `always` / `never` |

## 7. Project structure

```
├── docker-compose.yml · run-local.ps1 · .env.example
├── README.md · AI_DECLARATION.md · TECHNICAL_EXPLANATION.txt
├── backend/
│   ├── app/  main · config · models · engine · repository
│   │   ├── data/network.json
│   │   ├── api/       auth · admin · network · passenger · operator · stream · deps
│   │   └── services/  network · simulation · telemetry · density · anomaly · auth · mailer · events · clock
│   ├── tools/import_kmz.py · tools/source/buslines.kmz
│   └── tests/         42 tests
└── frontend/
    ├── index.html (login) · passenger.html · operator.html · admin.html · css/styles.css
    └── js/  lib · api · hooks · components/(common, TransitMap) · login/ · passenger/ · operator/ · admin/
```

**Data attribution.** Bus lines: provided `buslines.kmz`. Stops, street names and gap routing: © OpenStreetMap contributors (ODbL). Icons: Tabler Icons (MIT). Font: Montserrat (OFL). **The GPS feed (positions, speeds, stop durations) is simulated** because no live AVL feed is available. Crowding is inferred from that telemetry plus real passenger reports; it is never injected.
