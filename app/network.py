"""In-memory view of the static network, loaded once from the database."""
import json
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select

from . import db


@dataclass
class Stop:
    id: str
    name: str
    lat: float
    lon: float
    routes: list = field(default_factory=list)


@dataclass
class Pattern:
    """Stop sequence of one route in one direction with arrival offsets from trip start."""
    stop_ids: list
    offsets: list
    geometry_index: list

    @property
    def duration(self):
        return self.offsets[-1]

    def offset_of(self, stop_id):
        return self.offsets[self.stop_ids.index(stop_id)]


@dataclass
class Route:
    id: str
    name: str
    color: str
    headway_min: int
    dwell_sec: int
    geometry: list
    stop_ids: list
    patterns: dict            # direction -> Pattern
    starts: dict              # direction -> sorted trip start seconds


class Network:
    def __init__(self, engine):
        with engine.connect() as con:
            self.stops = {r.id: Stop(r.id, r.name, r.lat, r.lon) for r in con.execute(select(db.stops))}
            rs = defaultdict(list)
            for r in con.execute(select(db.route_stops).order_by(db.route_stops.c.route_id, db.route_stops.c.seq)):
                rs[r.route_id].append(r)
            st = defaultdict(list)
            for r in con.execute(select(db.stop_times).order_by(db.stop_times.c.route_id, db.stop_times.c.direction,
                                                                 db.stop_times.c.seq)):
                st[(r.route_id, r.direction)].append(r)
            starts = defaultdict(list)
            for r in con.execute(select(db.trips).order_by(db.trips.c.start_sec)):
                starts[(r.route_id, r.direction)].append(r.start_sec)
            self.routes = {}
            for r in con.execute(select(db.routes).order_by(db.routes.c.id)):
                stop_ids = [x.stop_id for x in rs[r.id]]
                gidx = {x.stop_id: x.geometry_index for x in rs[r.id]}
                patterns = {d: Pattern([x.stop_id for x in st[(r.id, d)]], [x.offset_sec for x in st[(r.id, d)]],
                                       [gidx[x.stop_id] for x in st[(r.id, d)]]) for d in (0, 1)}
                self.routes[r.id] = Route(r.id, r.name, r.color, r.headway_min, r.dwell_sec, json.loads(r.geometry),
                                          stop_ids, patterns, {d: starts[(r.id, d)] for d in (0, 1)})
                for s in stop_ids:
                    self.stops[s].routes.append(r.id)

    def search_stops(self, q):
        q = (q or "").strip().lower()
        hits = [s for s in self.stops.values() if not q or q in s.name.lower() or q == s.id.lower()]
        return sorted(hits, key=lambda s: (not s.name.lower().startswith(q), s.name))
