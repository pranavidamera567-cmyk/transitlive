"""Route alternatives between two stops: direct rides and single-transfer itineraries, using the timetable."""
from . import geo
from .db import DAY

TRANSFER_BUFFER_SEC = 120
MAX_OPTIONS = 5


class Planner:
    def __init__(self, network, clock):
        self.net = network
        self.clock = clock

    def _wait(self, route, d, stop_id, after_sec, now):
        """Seconds from `now` until the first scheduled departure from `stop_id` at or after `after_sec` from now."""
        off = route.patterns[d].offset_of(stop_id)
        best = None
        for start in route.starts[d]:
            t = (start + off - now) % DAY
            while t < after_sec:                              # next day's occurrence if this one is too early
                t += DAY
            best = t if best is None else min(best, t)
        return best

    def _leg(self, route, d, from_id, to_id, depart_in, arrive_in):
        p = route.patterns[d]
        gi = dict(zip(p.stop_ids, p.geometry_index))
        return {"route_id": route.id, "route_name": route.name, "color": route.color, "direction": d,
                "headsign": self.net.stops[p.stop_ids[-1]].name,
                "from": {"id": from_id, "name": self.net.stops[from_id].name},
                "to": {"id": to_id, "name": self.net.stops[to_id].name},
                "stops_between": p.stop_ids.index(to_id) - p.stop_ids.index(from_id),
                "depart_in_sec": int(depart_in), "arrive_in_sec": int(arrive_in),
                "path": geo.slice_points(route.geometry, gi[from_id], gi[to_id])}

    def _direction(self, route, a, b):
        """Direction in which `a` comes before `b`, or None."""
        for d in (0, 1):
            p = route.patterns[d]
            if a in p.stop_ids and b in p.stop_ids and p.stop_ids.index(a) < p.stop_ids.index(b):
                return d
        return None

    def plan(self, from_id, to_id):
        now = self.clock()
        options = []
        for ra in self.net.routes.values():
            d = self._direction(ra, from_id, to_id)
            if d is not None:                                  # direct
                depart = self._wait(ra, d, from_id, 0, now)
                arrive = depart + ra.patterns[d].offset_of(to_id) - ra.patterns[d].offset_of(from_id)
                options.append([self._leg(ra, d, from_id, to_id, depart, arrive)])
        for ra in self.net.routes.values():                    # one transfer
            for rb in self.net.routes.values():
                if ra.id == rb.id:
                    continue
                for x in set(ra.stop_ids) & set(rb.stop_ids):
                    if x in (from_id, to_id):
                        continue
                    da, db_ = self._direction(ra, from_id, x), self._direction(rb, x, to_id)
                    if da is None or db_ is None:
                        continue
                    pa, pb = ra.patterns[da], rb.patterns[db_]
                    depart = self._wait(ra, da, from_id, 0, now)
                    at_x = depart + pa.offset_of(x) - pa.offset_of(from_id)
                    leave_x = self._wait(rb, db_, x, at_x + TRANSFER_BUFFER_SEC, now)
                    arrive = leave_x + pb.offset_of(to_id) - pb.offset_of(x)
                    options.append([self._leg(ra, da, from_id, x, depart, at_x),
                                    self._leg(rb, db_, x, to_id, leave_x, arrive)])
        results = [{"transfers": len(legs) - 1, "total_sec": legs[-1]["arrive_in_sec"], "legs": legs}
                   for legs in options]
        results.sort(key=lambda r: (r["total_sec"], r["transfers"]))
        unique, seen = [], set()
        for r in results:                                      # drop duplicate route combinations
            key = tuple((leg["route_id"], leg["from"]["id"], leg["to"]["id"]) for leg in r["legs"])
            if key not in seen:
                seen.add(key)
                unique.append(r)
        unique = unique[:MAX_OPTIONS]
        if unique:
            unique[0]["label"] = "Fastest"
            fewest = min(unique, key=lambda r: (r["transfers"], r["total_sec"]))
            if "label" not in fewest:
                fewest["label"] = "Fewest transfers"
        return unique
