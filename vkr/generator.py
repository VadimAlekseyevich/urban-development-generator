"""Учебная модель прямоугольного участка. Все координаты заданы в метрах."""
import math
import random
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    def contains(self, other):
        return (self.x <= other.x and self.y <= other.y
                and other.x + other.w <= self.x + self.w
                and other.y + other.h <= self.y + self.h)

    def distance(self, other):
        dx = max(self.x - other.x - other.w, other.x - self.x - self.w, 0)
        dy = max(self.y - other.y - other.h, other.y - self.y - self.h, 0)
        return math.hypot(dx, dy)

    @property
    def center(self):
        return self.x + self.w / 2, self.y + self.h / 2


def generate(site, target, seats, seed):
    rng = random.Random(seed)
    bounds = Rect(0, 0, site["width"], site["height"])
    roads = [Rect(0, road["y"] - road["width"] // 2, bounds.w, road["width"])
             for road in site["roads"]]
    reserved = [Rect(**site["park"]), Rect(**site["school"]), *roads]
    candidates = []
    for road in roads:
        for y in (road.y - 6 - 16, road.y + road.h + 6):
            for x in range(12, bounds.w, 48):
                footprint = Rect(x, y, rng.choice((24, 32)), 16)
                candidates.append((footprint, rng.randint(3, 5)))
    rng.shuffle(candidates)

    placed = []
    buildings = []
    population = 0
    rejected = {"geometry": 0, "radius": 0}
    limit = min(target, seats * 10)
    for rect, floors in candidates:
        if population == limit:
            break
        if (not bounds.contains(rect) or any(rect.distance(r) == 0 for r in reserved)
                or any(rect.distance(r) < 6 for r in placed)):
            rejected["geometry"] += 1
            continue
        if math.dist(rect.center, site["school_point"]) > site["radius"]:
            rejected["radius"] += 1
            continue
        capacity = math.floor(rect.w * rect.h * floors * 0.75 / 30)
        residents = min(capacity, limit - population)
        buildings.append(dict(asdict(rect), id=len(buildings) + 1, floors=floors,
                              capacity=capacity, residents=residents))
        placed.append(rect)
        population += residents

    if population == target:
        reason = "target"
    elif population == seats * 10:
        reason = "school"
    else:
        reason = "land"
    return dict(buildings=buildings, population=population, deficit=target - population,
                used_seats=(population + 9) // 10, reason=reason, rejected=rejected)
