"""Route-optimization engine — pure/offline tests (injected geocoder, no network)."""
from __future__ import annotations

import math

import pytest

from utah.product import route as R


# ---- haversine --------------------------------------------------------------

def test_haversine_zero_distance():
    assert R.haversine_miles((33.0, -83.0), (33.0, -83.0)) == pytest.approx(0.0, abs=1e-9)


def test_haversine_one_degree_is_about_69_miles():
    # 1 degree of longitude at the equator ~ 69.1 miles
    d = R.haversine_miles((0.0, 0.0), (0.0, 1.0))
    assert d == pytest.approx(69.1, abs=0.5)


def test_haversine_symmetric():
    a, b = (33.05, -83.45), (33.95, -84.10)
    assert R.haversine_miles(a, b) == pytest.approx(R.haversine_miles(b, a), abs=1e-9)


# ---- distance matrix --------------------------------------------------------

def test_distance_matrix_shape_and_diag():
    pts = [(33.0, -83.0), (33.1, -83.1), (33.2, -83.2)]
    m = R.distance_matrix(pts)
    assert len(m) == 3 and all(len(row) == 3 for row in m)
    for i in range(3):
        assert m[i][i] == pytest.approx(0.0, abs=1e-9)
    assert m[0][1] == pytest.approx(m[1][0], abs=1e-9)
    assert m[0][1] == pytest.approx(R.haversine_miles(pts[0], pts[1]), abs=1e-9)


# ---- solver -----------------------------------------------------------------

def _line_matrix(positions):
    return [[abs(a - b) for b in positions] for a in positions]


def test_solver_beats_naive_order():
    # stops on a line at 0,10,1,2 starting at index 0
    m = _line_matrix([0, 10, 1, 2])
    order = R.solve_order(m, start=0, round_trip=False)
    assert order[0] == 0
    assert set(order) == {0, 1, 2, 3}
    # optimal open path is 0->2->3->1 = 1+1+8 = 10
    assert order == [0, 2, 3, 1]
    assert R.path_miles(m, order, round_trip=False) == pytest.approx(10.0)


def test_priority_pulls_valuable_stop_first():
    # positions 0,5,6 ; index 2 is the high-value stop
    m = _line_matrix([0, 5, 6])
    plain = R.solve_order(m, start=0, round_trip=False)
    assert plain == [0, 1, 2]  # pure distance
    prioritized = R.solve_order(
        m, start=0, priorities=[0.0, 0.0, 10.0], round_trip=False
    )
    assert prioritized == [0, 2, 1]  # value first, slight mileage cost


# ---- geocoding (injected) ---------------------------------------------------

def _fake_geocoder(table):
    return lambda addr: table.get(addr)


def test_geocode_fills_coords_and_flags_misses():
    stops = [
        R.Stop(label="a", address="A St"),
        R.Stop(label="b", address="B St"),
        R.Stop(label="bad", address="Nowhere"),
    ]
    geo = _fake_geocoder({"A St": (33.0, -83.0), "B St": (33.1, -83.1)})
    routable, unroutable = R.geocode_stops(stops, geo)
    assert routable == [0, 1]
    assert unroutable == [2]
    assert stops[0].lat == 33.0 and stops[0].lng == -83.0
    # never fabricates the miss
    assert stops[2].lat is None and stops[2].lng is None


# ---- end to end: Canvasser config ------------------------------------------

def test_optimize_canvasser_returns_one_route_with_totals():
    stops = [
        R.Stop(label="hot", address="far", priority=100.0),
        R.Stop(label="x", address="near1"),
        R.Stop(label="y", address="near2"),
    ]
    geo = _fake_geocoder({
        "far": (33.50, -83.0),
        "near1": (33.01, -83.0),
        "near2": (33.02, -83.0),
    })
    depot = R.Stop(label="home", address="depot")
    geo2 = _fake_geocoder({**{
        "far": (33.50, -83.0), "near1": (33.01, -83.0), "near2": (33.02, -83.0),
    }, "depot": (33.00, -83.0)})
    res = R.optimize(stops, R.CANVASSER, depot=depot, geocoder=geo2)
    assert len(res.routes) == 1
    vr = res.routes[0]
    assert set(vr.stops) == {0, 1, 2}
    # high-priority "far" visited before the two near stops
    assert vr.stops.index(0) == 0
    assert vr.total_miles > 0 and vr.minutes > 0
    assert vr.maps_url.startswith("https://www.google.com/maps/dir/")
    assert not res.unroutable


# ---- end to end: Fleet config ----------------------------------------------

def test_optimize_fleet_splits_across_vehicles_covering_all_stops():
    coords = {
        "n1": (33.0, -83.0), "n2": (33.0, -83.05),
        "s1": (32.5, -83.0), "s2": (32.5, -83.05),
    }
    stops = [R.Stop(label=k, address=k) for k in ("n1", "n2", "s1", "s2")]
    depot = R.Stop(label="hub", address="hub")
    geo = _fake_geocoder({**coords, "hub": (32.75, -83.025)})
    res = R.optimize(stops, R.FLEET_2, depot=depot, geocoder=geo)
    assert len(res.routes) == 2
    covered = [i for vr in res.routes for i in vr.stops]
    assert sorted(covered) == [0, 1, 2, 3]      # every stop once, no dupes
    assert len(covered) == len(set(covered))
    assert res.total_miles == pytest.approx(sum(vr.total_miles for vr in res.routes))


# ---- DB row mappers (pure) --------------------------------------------------

def test_probate_rows_become_priority_weighted_stops():
    rows = [
        (487, "LINDA SUNDSTROM", "hall", 1068000, 34.3275, -83.8282),
        (17, "JEAN SAWYER", "hall", 913400, 34.1403, -83.9799),
        (99, "NO COORDS", "hall", 500000, None, None),  # dropped, never invented
    ]
    stops = R._probate_rows_to_stops(rows)
    assert [s.label for s in stops] == ["LINDA SUNDSTROM", "JEAN SAWYER"]
    assert stops[0].priority == 1068000.0  # ARV drives priority
    assert stops[0].lat == 34.3275 and stops[0].meta["county"] == "hall"


def test_lead_rows_drop_addressless():
    rows = [(1, "Shop A", "GA", "1 Main St"), (2, "Shop B", "GA", None)]
    stops = R._lead_rows_to_stops(rows)
    assert [s.label for s in stops] == ["Shop A"]
    assert stops[0].address == "1 Main St"


def test_optimize_handles_all_unroutable_without_crashing():
    stops = [R.Stop(label="x", address="??"), R.Stop(label="y", address="!!")]
    res = R.optimize(stops, R.CANVASSER, geocoder=_fake_geocoder({}))
    assert res.routes == []
    assert res.unroutable == [0, 1]
    assert res.total_miles == 0.0
