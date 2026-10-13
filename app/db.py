"""PostgreSQL (or SQLite for quick local runs) storage for the static network and timetable."""
import json
from pathlib import Path

from sqlalchemy import (Column, Float, ForeignKey, Integer, MetaData, String, Table, Text, create_engine, insert,
                        select)

from .geo import length_m, slice_points

metadata = MetaData()
stops = Table("stops", metadata,
              Column("id", String(16), primary_key=True), Column("name", String(100), nullable=False),
              Column("lat", Float, nullable=False), Column("lon", Float, nullable=False))
routes = Table("routes", metadata,
               Column("id", String(16), primary_key=True), Column("name", String(100), nullable=False),
               Column("color", String(9), nullable=False), Column("headway_min", Integer, nullable=False),
               Column("speed_kmh", Float, nullable=False), Column("dwell_sec", Integer, nullable=False),
               Column("geometry", Text, nullable=False))      # JSON [[lat, lon], ...]
route_stops = Table("route_stops", metadata,
                    Column("route_id", String(16), ForeignKey("routes.id"), primary_key=True),
                    Column("seq", Integer, primary_key=True), Column("stop_id", String(16), ForeignKey("stops.id")),
                    Column("geometry_index", Integer, nullable=False))
trips = Table("trips", metadata,
              Column("id", String(32), primary_key=True), Column("route_id", String(16), ForeignKey("routes.id"), index=True),
              Column("direction", Integer, nullable=False), Column("start_sec", Integer, nullable=False))
stop_times = Table("stop_times", metadata,       # one stop pattern per (route, direction); trips add their start time
                   Column("route_id", String(16), ForeignKey("routes.id"), primary_key=True),
                   Column("direction", Integer, primary_key=True), Column("seq", Integer, primary_key=True),
                   Column("stop_id", String(16), ForeignKey("stops.id")), Column("offset_sec", Integer, nullable=False))

DAY = 86400


def make_engine(url):
    return create_engine(url, future=True)


def pattern_offsets(geometry, stop_index, speed_kmh, dwell_sec):
    """Seconds after trip start at which a vehicle arrives at each stop (direction 0 order)."""
    mps = speed_kmh / 3.6
    offsets = [0]
    for a, b in zip(stop_index, stop_index[1:]):
        offsets.append(offsets[-1] + dwell_sec + int(round(length_m(slice_points(geometry, a, b)) / mps)))
    return offsets


def seed(engine, network_path):
    """Loads the network config and generates trips (every `headway_min`, both directions, all day)."""
    network = json.loads(Path(network_path).read_text())
    with engine.begin() as con:
        con.execute(insert(stops), network["stops"])
        for r in network["routes"]:
            con.execute(insert(routes), {k: r[k] for k in ("id", "name", "color", "headway_min", "speed_kmh", "dwell_sec")}
                        | {"geometry": json.dumps(r["geometry"])})
            con.execute(insert(route_stops), [{"route_id": r["id"], "seq": i, "stop_id": s, "geometry_index": gi}
                                              for i, (s, gi) in enumerate(zip(r["stops"], r["stop_index"]))])
            fwd = pattern_offsets(r["geometry"], r["stop_index"], r["speed_kmh"], r["dwell_sec"])
            total = fwd[-1]
            patterns = {0: list(zip(r["stops"], fwd)),
                        1: [(s, total - o) for s, o in zip(r["stops"], fwd)][::-1]}
            for direction, pattern in patterns.items():
                con.execute(insert(stop_times), [{"route_id": r["id"], "direction": direction, "seq": i, "stop_id": s,
                                                  "offset_sec": o} for i, (s, o) in enumerate(pattern)])
                step = r["headway_min"] * 60
                shift = step // 2 * direction   # stagger the two directions
                con.execute(insert(trips), [{"id": f"{r['id']}-{direction}-{t // 60:04d}", "route_id": r["id"],
                                             "direction": direction, "start_sec": t}
                                            for t in range(shift, DAY, step)])


def is_empty(engine):
    with engine.connect() as con:
        return con.execute(select(stops.c.id).limit(1)).first() is None
