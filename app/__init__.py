import json
import os
import time
from pathlib import Path

from flask import Flask, jsonify, render_template, request
from flask_sock import Sock
from simple_websocket import ConnectionClosed

from . import db
from .network import Network
from .planner import Planner
from .sim import SimClock, Simulation, hhmmss

ROOT = Path(__file__).resolve().parent.parent


def create_app(config=None):
    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    (ROOT / "instance").mkdir(exist_ok=True)
    app.config.update(
        DATABASE_URL=os.environ.get("DATABASE_URL", f"sqlite:///{ROOT / 'instance' / 'transit.db'}"),
        NETWORK_FILE=str(ROOT / "data" / "network.json"),
        SIM_SPEED=float(os.environ.get("SIM_SPEED", "1")),
        SIM_START=os.environ.get("SIM_START"),           # "HH:MM"; default is the real time of day
        WS_INTERVAL=1.0,
        CLOCK=None,                                      # tests inject a callable returning seconds since midnight
    )
    app.config.update(config or {})

    engine = db.make_engine(app.config["DATABASE_URL"])
    db.metadata.create_all(engine)
    if db.is_empty(engine):
        db.seed(engine, app.config["NETWORK_FILE"])
    net = Network(engine)
    center = json.loads(Path(app.config["NETWORK_FILE"]).read_text())["center"]

    clock = app.config["CLOCK"]
    if clock is None:
        start = None
        if app.config["SIM_START"]:
            h, m = app.config["SIM_START"].split(":")
            start = int(h) * 3600 + int(m) * 60
        clock = SimClock(app.config["SIM_SPEED"], start)
    sim = Simulation(net, clock)
    planner = Planner(net, clock)
    sock = Sock(app)

    def stop_json(s):
        return {"id": s.id, "name": s.name, "lat": s.lat, "lon": s.lon, "routes": s.routes}

    def alerts():
        return [{"route_id": r, "route_name": net.routes[r].name, "extra_delay_sec": sec}
                for r, sec in sorted(sim.route_delay.items())]

    # ---- pages & static data -----------------------------------------------------------------
    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/health")
    def health():
        return jsonify(status="ok", time=hhmmss(clock()))

    @app.get("/api/network")
    def network():
        return jsonify(center=center, stops=[stop_json(s) for s in net.stops.values()],
                       routes=[{"id": r.id, "name": r.name, "color": r.color, "headway_min": r.headway_min,
                                "geometry": r.geometry, "stops": r.stop_ids} for r in net.routes.values()])

    @app.get("/api/stops")
    def stops():
        return jsonify([stop_json(s) for s in net.search_stops(request.args.get("q"))])

    @app.get("/api/stops/<stop_id>/arrivals")
    def arrivals(stop_id):
        try:
            return jsonify(stop=stop_json(net.stops[stop_id]), time=hhmmss(clock()),
                           arrivals=sim.arrivals(stop_id, limit=request.args.get("limit", 10, type=int)))
        except KeyError:
            return jsonify(error=f"Unknown stop {stop_id}"), 404

    @app.get("/api/vehicles")
    def vehicles():
        routes = request.args.get("routes")
        return jsonify(time=hhmmss(clock()), vehicles=sim.vehicles(set(routes.split(",")) if routes else None),
                       alerts=alerts())

    @app.get("/api/plan")
    def plan():
        a, b = request.args.get("from"), request.args.get("to")
        for sid in (a, b):
            if sid not in net.stops:
                return jsonify(error=f"Unknown stop {sid}"), 404
        if a == b:
            return jsonify(error="Origin and destination must differ"), 400
        return jsonify(time=hhmmss(clock()), options=planner.plan(a, b))

    @app.post("/api/routes/<route_id>/delay")
    def set_delay(route_id):
        """Simulates an incident: adds `seconds` of delay to every vehicle on the route (0 clears it)."""
        seconds = (request.get_json(silent=True) or {}).get("seconds")
        if not isinstance(seconds, int) or not 0 <= seconds <= 3600:
            return jsonify(error="'seconds' must be an integer between 0 and 3600"), 400
        try:
            sim.set_route_delay(route_id, seconds)
        except KeyError:
            return jsonify(error=f"Unknown route {route_id}"), 404
        return jsonify(alerts=alerts())

    # ---- live updates over WebSocket ---------------------------------------------------------
    def update_message(sub):
        msg = {"type": "update", "time": hhmmss(clock()), "alerts": alerts(),
               "vehicles": sim.vehicles(sub["routes"]), "arrivals": None}
        if sub["stop"] in net.stops:
            msg["arrivals"] = {"stop_id": sub["stop"], "items": sim.arrivals(sub["stop"], limit=6)}
        return msg

    @sock.route("/ws")
    def live(ws):
        """Pushes vehicle positions/delays (and arrivals for one stop) every WS_INTERVAL seconds.

        Client -> server: {"action": "subscribe", "routes": ["R1", ...] | null, "stop": "C01" | null}
        """
        sub = {"routes": None, "stop": None}
        interval = app.config["WS_INTERVAL"]
        last = 0.0
        try:
            while True:
                wait = max(0.0, last + interval - time.monotonic())
                raw = ws.receive(timeout=wait)
                if raw:
                    try:
                        data = json.loads(raw)
                    except ValueError:
                        ws.send(json.dumps({"type": "error", "error": "invalid JSON"}))
                        continue
                    if data.get("action") == "subscribe":
                        routes = data.get("routes")
                        sub["routes"] = set(routes) if isinstance(routes, list) and routes else None
                        sub["stop"] = data.get("stop")
                        last = 0.0                      # answer a (re)subscription immediately
                if time.monotonic() - last >= interval:
                    ws.send(json.dumps(update_message(sub)))
                    last = time.monotonic()
        except ConnectionClosed:
            pass

    return app
