"""Vehicle simulation and arrival estimates, computed on demand from the timetable and the clock.

A trip's *scheduled* progress is `elapsed = now - start`. A delayed vehicle is behind schedule, so its actual
progress is `elapsed - delay`. Delay is a deterministic function of (trip, time) plus any route-wide incident delay,
so positions are smooth and reproducible without per-vehicle state.
"""
import math
import time
import zlib

from . import geo
from .db import DAY


class SimClock:
    """Seconds since midnight. `speed` > 1 fast-forwards the demo (e.g. 10 = ten simulated seconds per second)."""

    def __init__(self, speed=1.0, start_sec=None):
        self.speed = speed
        lt = time.localtime()
        self.start = lt.tm_hour * 3600 + lt.tm_min * 60 + lt.tm_sec if start_sec is None else start_sec
        self.t0 = time.monotonic()

    def __call__(self):
        return (self.start + (time.monotonic() - self.t0) * self.speed) % DAY


def hhmm(sec):
    sec = int(sec) % DAY
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}"


def hhmmss(sec):
    sec = int(sec) % DAY
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


class Simulation:
    def __init__(self, network, clock):
        self.net = network
        self.clock = clock
        self.route_delay = {}        # route_id -> extra delay seconds (incident)

    # ---- delay -------------------------------------------------------------------------------
    def delay(self, route_id, trip_id, elapsed):
        h = zlib.crc32(trip_id.encode())
        base = (h % 7) * 60                                    # 0-6 min, fixed per trip
        wobble = 45 * math.sin(elapsed / 240.0 + (h % 628) / 100.0)
        return int(max(0, base + wobble)) + self.route_delay.get(route_id, 0)

    def set_route_delay(self, route_id, seconds):
        if route_id not in self.net.routes:
            raise KeyError(route_id)
        if seconds > 0:
            self.route_delay[route_id] = int(seconds)
        else:
            self.route_delay.pop(route_id, None)

    # ---- vehicles ----------------------------------------------------------------------------
    def _trips(self):
        for route in self.net.routes.values():
            for d in (0, 1):
                for start in route.starts[d]:
                    yield route, d, start, f"{route.id}-{d}-{start // 60:04d}"

    def vehicles(self, route_ids=None):
        now = self.clock()
        out = []
        for route, d, start, trip_id in self._trips():
            if route_ids and route.id not in route_ids:
                continue
            pattern = route.patterns[d]
            elapsed = (now - start) % DAY
            if elapsed > pattern.duration + 1800:       # cannot be running: skip cheaply
                continue
            delay = self.delay(route.id, trip_id, elapsed)
            if elapsed > pattern.duration + delay:
                continue                                 # already finished
            out.append(self._vehicle(route, d, trip_id, pattern, elapsed - delay, delay))
        return out

    def _vehicle(self, route, d, trip_id, pattern, progress, delay):
        lat, lon, heading, next_idx = self._locate(route, pattern, progress)
        nxt = self.net.stops[pattern.stop_ids[next_idx]]
        return {"id": trip_id, "route_id": route.id, "direction": d, "lat": round(lat, 6), "lon": round(lon, 6),
                "heading": round(heading), "delay_sec": delay, "next_stop_id": nxt.id, "next_stop": nxt.name,
                "headsign": self.net.stops[pattern.stop_ids[-1]].name,
                "status": "waiting to depart" if progress < 0 else "in service"}

    def _locate(self, route, pattern, progress):
        """(lat, lon, heading, index of the next stop) after `progress` seconds of the trip."""
        g, idx, offs = route.geometry, pattern.geometry_index, pattern.offsets
        n = len(offs)
        if progress <= 0:
            seg = geo.slice_points(g, idx[0], idx[1])
            return seg[0][0], seg[0][1], geo.bearing(seg[0], seg[1]), 0
        for k in range(n - 1):
            depart = offs[k] + route.dwell_sec
            if progress <= depart:                          # dwelling at stop k
                seg = geo.slice_points(g, idx[k], idx[k + 1])
                return seg[0][0], seg[0][1], geo.bearing(seg[0], seg[1]), k
            if progress <= offs[k + 1]:
                seg = geo.slice_points(g, idx[k], idx[k + 1])
                span = max(offs[k + 1] - depart, 1)
                lat, lon, heading = geo.point_at(seg, (progress - depart) / span)
                return lat, lon, heading, k + 1
        seg = geo.slice_points(g, idx[-2], idx[-1])
        return seg[-1][0], seg[-1][1], geo.bearing(seg[-2], seg[-1]), n - 1

    # ---- arrivals ----------------------------------------------------------------------------
    def arrivals(self, stop_id, horizon_sec=3600, limit=10):
        """Upcoming arrivals at a stop: running vehicles use live delay; later trips use the schedule."""
        if stop_id not in self.net.stops:
            raise KeyError(stop_id)
        now = self.clock()
        out = []
        for route_id in self.net.stops[stop_id].routes:
            route = self.net.routes[route_id]
            for d in (0, 1):
                pattern = route.patterns[d]
                if stop_id == pattern.stop_ids[-1]:
                    continue                                 # vehicles terminate here; not boarding
                off = pattern.offset_of(stop_id)
                for start in route.starts[d]:
                    trip_id = f"{route.id}-{d}-{start // 60:04d}"
                    elapsed = (now - start) % DAY
                    delay = self.delay(route.id, trip_id, elapsed)
                    live = elapsed <= pattern.duration + delay
                    eta = (off + delay - elapsed) if live else ((start - now) % DAY + off)
                    if live and eta < 0:
                        continue                             # this vehicle has already passed
                    if eta > horizon_sec:
                        continue
                    out.append({"route_id": route.id, "route_name": route.name, "color": route.color,
                                "trip_id": trip_id, "headsign": self.net.stops[pattern.stop_ids[-1]].name,
                                "eta_sec": int(eta), "scheduled": hhmm(start + off),
                                "expected": hhmm(now + eta), "delay_sec": delay if live else 0, "live": live})
        return sorted(out, key=lambda a: a["eta_sec"])[:limit]
