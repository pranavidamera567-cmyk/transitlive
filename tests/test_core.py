import json
import threading

import pytest
from simple_websocket import Client
from werkzeug.serving import make_server

from app import create_app, geo
from app.sim import hhmm


def at(h, m=0, s=0):
    return h * 3600 + m * 60 + s


class FakeClock:
    def __init__(self, sec):
        self.sec = sec

    def __call__(self):
        return self.sec


@pytest.fixture()
def clock():
    return FakeClock(at(8))


@pytest.fixture()
def app(tmp_path, clock):
    return create_app({"DATABASE_URL": f"sqlite:///{tmp_path / 't.db'}", "CLOCK": clock, "WS_INTERVAL": 0.2})


@pytest.fixture()
def client(app):
    return app.test_client()


def test_geo_helpers():
    a, b = [41.0, -87.0], [41.0, -86.9]
    assert 8000 < geo.haversine_m(a, b) < 9000
    assert abs(geo.bearing(a, b) - 90) < 0.5
    lat, lon, heading = geo.point_at([a, b], 0.5)
    assert abs(lon - -86.95) < 1e-6 and abs(heading - 90) < 0.5
    assert geo.slice_points([1, 2, 3, 4], 3, 1) == [4, 3, 2]


def test_network_and_stop_lookup(client):
    net = client.get("/api/network").json
    assert len(net["routes"]) == 3 and len(net["stops"]) == 15
    assert all(len(r["geometry"]) >= len(r["stops"]) for r in net["routes"])
    hits = client.get("/api/stops?q=central").json
    assert [s["id"] for s in hits] == ["C01"] and sorted(hits[0]["routes"]) == ["R1", "R2"]
    assert len(client.get("/api/stops?q=").json) == 15
    assert client.get("/api/stops?q=zzz").json == []


def test_vehicles_are_on_their_route_and_move(client, clock):
    first = {v["id"]: v for v in client.get("/api/vehicles").json["vehicles"]}
    assert first and all(v["route_id"] in ("R1", "R2", "R3") for v in first.values())
    assert 43.65 < min(v["lat"] for v in first.values()) and max(v["lat"] for v in first.values()) < 43.70   # central Toronto
    clock.sec += 60
    second = {v["id"]: v for v in client.get("/api/vehicles").json["vehicles"]}
    moved = [i for i in first if i in second and (first[i]["lat"], first[i]["lon"]) != (second[i]["lat"], second[i]["lon"])]
    assert len(moved) >= len(first) // 2
    only = client.get("/api/vehicles?routes=R2").json["vehicles"]
    assert only and {v["route_id"] for v in only} == {"R2"}


def test_vehicle_at_scheduled_start_sits_on_first_stop(client, clock):
    clock.sec = at(8, 0)                                # R1 direction 0 trips start on the 10-minute mark
    v = next(v for v in client.get("/api/vehicles?routes=R1").json["vehicles"] if v["id"] == "R1-0-0480")
    stops = {s["id"]: s for s in client.get("/api/network").json["stops"]}
    assert abs(v["lat"] - stops["N01"]["lat"]) < 0.002 and abs(v["lon"] - stops["N01"]["lon"]) < 0.002
    assert v["headsign"] == "South Terminal"
    assert v["status"] == ("waiting to depart" if v["delay_sec"] > 0 else "in service")


def test_arrivals_sorted_live_and_terminal_excluded(client, clock):
    data = client.get("/api/stops/C01/arrivals?limit=20").json
    etas = [a["eta_sec"] for a in data["arrivals"]]
    assert etas == sorted(etas) and etas and min(etas) >= 0 and max(etas) <= 3600
    assert any(a["live"] for a in data["arrivals"]) and any(not a["live"] for a in client.get("/api/stops/C01/arrivals?limit=20").json["arrivals"])
    assert {a["route_id"] for a in data["arrivals"]} <= {"R1", "R2"}
    # nothing boards at the end of a line in the direction that terminates there
    assert all(a["headsign"] != "North Terminal" for a in client.get("/api/stops/N01/arrivals").json["arrivals"])
    assert client.get("/api/stops/NOPE/arrivals").status_code == 404


def test_incident_delay_shifts_arrivals_and_vehicles(client, clock):
    base = client.get("/api/stops/N05/arrivals?limit=20").json["arrivals"]
    live_base = {a["trip_id"]: a for a in base if a["live"] and a["route_id"] == "R1"}
    assert live_base
    assert client.post("/api/routes/R1/delay", json={"seconds": 300}).json["alerts"][0]["route_id"] == "R1"
    after = {a["trip_id"]: a for a in client.get("/api/stops/N05/arrivals?limit=20").json["arrivals"]}
    for trip_id, a in live_base.items():
        if trip_id in after and after[trip_id]["live"]:
            assert after[trip_id]["eta_sec"] - a["eta_sec"] in range(295, 306)   # +300s (± wobble drift)
    assert all(v["delay_sec"] >= 300 for v in client.get("/api/vehicles?routes=R1").json["vehicles"])
    client.post("/api/routes/R1/delay", json={"seconds": 0})
    assert client.get("/api/vehicles").json["alerts"] == []
    assert client.post("/api/routes/R1/delay", json={"seconds": -5}).status_code == 400
    assert client.post("/api/routes/XX/delay", json={"seconds": 5}).status_code == 404


def test_planner_direct_and_transfer(client):
    direct = client.get("/api/plan?from=N01&to=N06").json["options"]
    assert direct[0]["transfers"] == 0 and direct[0]["legs"][0]["route_id"] == "R1"
    assert direct[0]["label"] == "Fastest"
    leg = direct[0]["legs"][0]
    assert leg["stops_between"] == 5 and leg["path"][0] == [43.692, -79.39] and leg["path"][-1] == [43.652, -79.39]
    assert leg["arrive_in_sec"] > leg["depart_in_sec"] >= 0

    trip = client.get("/api/plan?from=W01&to=N01").json["options"]            # blue line -> transfer at Central -> red line north
    assert trip and trip[0]["transfers"] == 1
    assert [l["route_id"] for l in trip[0]["legs"]] == ["R2", "R1"] and trip[0]["legs"][0]["to"]["id"] == "C01"
    assert trip[0]["legs"][1]["depart_in_sec"] >= trip[0]["legs"][0]["arrive_in_sec"] + 120

    both = client.get("/api/plan?from=N03&to=N05").json["options"]            # served by R1 and R3: alternatives
    assert {o["legs"][0]["route_id"] for o in both} >= {"R1", "R3"}
    assert [o["total_sec"] for o in both] == sorted(o["total_sec"] for o in both)

    assert client.get("/api/plan?from=N01&to=N01").status_code == 400
    assert client.get("/api/plan?from=N01&to=XX").status_code == 404


def test_hhmm():
    assert hhmm(at(8, 5)) == "08:05" and hhmm(86400 + 60) == "00:01"


def test_websocket_streams_and_honours_subscription(app):
    server = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        ws = Client.connect(f"ws://127.0.0.1:{server.server_port}/ws")
        first = json.loads(ws.receive(timeout=3))
        assert first["type"] == "update" and first["vehicles"] and first["arrivals"] is None
        ws.send(json.dumps({"action": "subscribe", "routes": ["R3"], "stop": "C01"}))
        msg = None
        for _ in range(5):                               # skip any update sent before the subscription landed
            msg = json.loads(ws.receive(timeout=3))
            if msg["arrivals"]:
                break
        assert msg["arrivals"]["stop_id"] == "C01" and msg["arrivals"]["items"]
        assert {v["route_id"] for v in msg["vehicles"]} == {"R3"}
        ws.send("not json")
        assert json.loads(ws.receive(timeout=3)).get("type") in ("error", "update")
        ws.close()
    finally:
        server.shutdown()
