# transitlive

Real-time public transit monitoring and route planner (Apr 2022 – Sep 2022).

An interactive Leaflet/OpenStreetMap map shows simulated vehicles moving along configured route geometries. Vehicle
positions and delays are streamed to the browser over WebSockets. Stops can be looked up, arrival estimates come from
sample trip data, and the planner shows route alternatives (direct and one-transfer) with stop-level details.

**Stack:** Python, Flask (+ flask-sock WebSockets), JavaScript, Leaflet.js, OpenStreetMap tiles, PostgreSQL (SQLAlchemy)

> The network (3 routes, 15 stops, timetables) is **fictional**; only the map tiles are real. No live transit feed is used.

## How it works
| Part | Where |
|---|---|
| Route geometries, stops, speeds, headways (config) | `data/network.json` (regenerate with `scripts/make_network.py`) |
| Schema + seeding: stops, routes, route_stops, trips (every headway, both directions, 24 h), stop_times | `app/db.py` |
| Vehicle positions: each running trip is placed along its polyline from the schedule minus its delay | `app/sim.py` |
| Delay: deterministic per trip (0–6 min + slow wobble) plus optional route-wide incident delay | `app/sim.py` |
| Arrivals: running vehicles use live delay, later trips use the schedule | `app/sim.py` |
| Planner: direct + single-transfer itineraries from the timetable, ranked by total time (2 min transfer buffer) | `app/planner.py` |
| REST + `/ws` WebSocket | `app/__init__.py` |
| Map UI | `templates/index.html`, `static/app.js` |

**WebSocket protocol** (`/ws`): the server pushes `{"type":"update","time","vehicles":[…],"alerts":[…],"arrivals":{stop_id,items}|null}`
every second. The client subscribes with `{"action":"subscribe","routes":["R1"],"stop":"C01"}` (routes `null` = all) and reconnects with backoff.

## Run
```bash
docker compose up --build        # PostgreSQL + app → http://localhost:5001
```
or without Docker (SQLite file by default; set `DATABASE_URL` for PostgreSQL):
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python wsgi.py                   # http://localhost:5001
```
`SIM_SPEED=10` fast-forwards the simulation; `SIM_START=08:00` starts the clock at a given time (default: real time of day).

## API
| Method | Path | |
|---|---|---|
| GET | `/api/network` | stops, routes and geometries |
| GET | `/api/stops?q=` | stop lookup by name or id |
| GET | `/api/stops/<id>/arrivals?limit=` | estimated arrivals (live delay or scheduled) |
| GET | `/api/vehicles?routes=R1,R2` | current vehicle positions |
| GET | `/api/plan?from=<stop>&to=<stop>` | route alternatives with legs, geometry and timings |
| POST | `/api/routes/<id>/delay` | `{"seconds": 300}` simulate an incident (0 clears); shows up live on the map |

Try an incident: `curl -X POST localhost:5001/api/routes/R1/delay -H 'Content-Type: application/json' -d '{"seconds":600}'`

## Test
```bash
pytest     # includes an end-to-end WebSocket test against a real server
```
