"""Writes data/network.json: a fictional transit network with configured route geometries.

Stops sit on a street grid around a made-up downtown (coordinates are real map positions so OpenStreetMap
tiles line up, but the lines, stops and timetables are invented). Each route's geometry is a polyline that
passes through its stops, with an extra bend point between stops so it looks like street geometry.
"""
import json
from pathlib import Path

STOPS = {
    "N01": ("North Terminal", 41.9000, -87.6300), "N02": ("Lake Park", 41.8900, -87.6300),
    "N03": ("Market Square", 41.8830, -87.6300), "C01": ("Central Station", 41.8780, -87.6300),
    "N05": ("Riverside", 41.8700, -87.6300), "N06": ("South Terminal", 41.8600, -87.6300),
    "W01": ("West End", 41.8780, -87.6800), "W02": ("University", 41.8780, -87.6650),
    "W03": ("Tech Park", 41.8780, -87.6480), "E01": ("Harbor View", 41.8780, -87.6150),
    "E02": ("East Pier", 41.8780, -87.6000),
    "D01": ("Hillcrest", 41.8950, -87.6600), "D02": ("Old Town", 41.8880, -87.6450),
    "D04": ("Stadium", 41.8700, -87.6150), "D05": ("Southport", 41.8620, -87.6000),
}

ROUTES = [
    ("R1", "Red Line (North-South)", "#d62728", ["N01", "N02", "N03", "C01", "N05", "N06"], 10, 26),
    ("R2", "Blue Line (East-West)", "#1f77b4", ["W01", "W02", "W03", "C01", "E01", "E02"], 12, 28),
    ("R3", "Green Express (Diagonal)", "#2ca02c", ["D01", "D02", "N03", "N05", "D04", "D05"], 15, 32),
]


def build_route(route_id, name, color, stop_ids, headway, speed):
    geometry, stop_index = [], []
    for i, sid in enumerate(stop_ids):
        _, lat, lon = STOPS[sid]
        if i:
            _, plat, plon = STOPS[stop_ids[i - 1]]
            # one bend between stops (an L-shaped street turn); collinear for straight lines
            geometry.append([round(plat, 6), round(lon, 6)] if i % 2 else [round(lat, 6), round(plon, 6)])
        stop_index.append(len(geometry))
        geometry.append([lat, lon])
    return {"id": route_id, "name": name, "color": color, "stops": stop_ids, "headway_min": headway,
            "speed_kmh": speed, "dwell_sec": 30, "geometry": geometry, "stop_index": stop_index}


def main():
    network = {"center": [41.8780, -87.6300],
               "stops": [{"id": k, "name": v[0], "lat": v[1], "lon": v[2]} for k, v in STOPS.items()],
               "routes": [build_route(*r) for r in ROUTES]}
    out = Path(__file__).resolve().parent.parent / "data" / "network.json"
    out.write_text(json.dumps(network, indent=1))
    print(f"wrote {len(network['stops'])} stops and {len(network['routes'])} routes to {out}")


if __name__ == "__main__":
    main()
