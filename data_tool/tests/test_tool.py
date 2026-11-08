"""Unit tests for the data tool. Run from data_tool:  python -m unittest discover tests"""

import json
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from f1data import builder, circuits, estimate, outline, race_format as rf, server, sources, telemetry
from f1data.http_client import (CachedClient, DataUnavailable, NetworkError, RateLimiter,
                                build_url)


def tiny_package(frames=6, cars=2):
    shape = (frames, cars)
    return rf.RacePackage(
        season=2024, round=1, fidelity=rf.FIDELITY_TELEMETRY, total_laps=3, race_name="Test GP",
        circuit="Pérez Circuit", date="2024-03-02", sources="unit test", outline_source="test",
        start_epoch_ms=1_700_000_000_123, lights_out_ms=1000, map_w=4000, map_h=3000,
        outline=np.array([[0, 0], [4000, 0], [4000, 3000]]),
        drivers=[rf.Driver(1, "AAA", "Driver A", "Team", (10, 20, 30), 1, 1),
                 rf.Driver(44, "BBB", "Driver B", "Team", (200, 100, 0), 2, 2, True)][:cars],
        laps=[rf.LapRecord(0, 1, rf.LAP_PIT_IN, 90000, 90000)],
        x=np.full(shape, 100.0), y=np.full(shape, 200.0), state=np.full(shape, rf.STATE_PIT),
        no_data=np.zeros(shape, bool), estimated=np.ones(shape, bool),
        position=np.tile(np.arange(1, cars + 1, dtype=np.uint8), (frames, 1)),
        lap=np.ones(shape, np.int32), gap=np.full(shape, 12, np.uint16),
        track_status=np.full(frames, rf.TRACK_SC, np.uint8),
        leader_lap=np.ones(frames, np.uint8), flags=rf.FLAG_LAYOUT_DIFFERS,
        notes=["first note", "second note"])


class FormatTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "r.f1r"

    def test_round_trip(self):
        size, crc = rf.write_package(tiny_package(), self.path)
        data = rf.read_package(self.path)
        self.assertEqual(size, self.path.stat().st_size)
        self.assertEqual(crc, data["crc32"])
        self.assertEqual(data["strings"][rf.STR_CIRCUIT], ["Perez Circuit"])
        self.assertEqual(data["strings"][rf.STR_NOTE], ["first note", "second note"])
        self.assertEqual(data["start_epoch_ms"], 1_700_000_000_123)
        self.assertEqual(data["flags"], rf.FLAG_LAYOUT_DIFFERS)
        self.assertEqual(data["drivers"][1]["code"], "BBB")
        self.assertTrue(data["drivers"][1]["second_car"])
        self.assertEqual(data["laps"][0], (0, 1, rf.LAP_PIT_IN, 0, 90000, 90000))
        car = data["frames"]["cars"][3][1]
        self.assertEqual(int(car["xs"]) & 0xFFF, 100)
        self.assertEqual((int(car["xs"]) >> 12) & 3, rf.STATE_PIT)
        self.assertTrue(int(car["xs"]) & rf.BIT_ESTIMATED)
        self.assertFalse(int(car["xs"]) & rf.BIT_NO_DATA)
        self.assertEqual(int(data["frames"]["status"][0]), rf.TRACK_SC)

    def test_truncated_file_rejected(self):
        rf.write_package(tiny_package(), self.path)
        self.path.write_bytes(self.path.read_bytes()[:-1])
        with self.assertRaises(rf.BadRaceFile):
            rf.read_package(self.path)

    def test_header_corruption_rejected(self):
        rf.write_package(tiny_package(), self.path)
        raw = bytearray(self.path.read_bytes())
        raw[12] ^= 0xFF
        self.path.write_bytes(bytes(raw))
        with self.assertRaises(rf.BadRaceFile):
            rf.read_package(self.path)

    def test_future_version_rejected(self):
        rf.write_package(tiny_package(), self.path)
        raw = bytearray(self.path.read_bytes())
        raw[4] = 2
        self.path.write_bytes(bytes(raw))
        with self.assertRaisesRegex(rf.BadRaceFile, "version"):
            rf.read_package(self.path)

    def test_dark_colours_are_lightened_but_source_colour_is_kept(self):
        rf.write_package(tiny_package(), self.path)
        driver = rf.read_package(self.path)["drivers"][0]
        self.assertEqual(driver["rgb"], (10, 20, 30))
        self.assertNotEqual(driver["rgb565"], rf.rgb565((10, 20, 30)))


class RateLimiterTests(unittest.TestCase):
    def test_second_limit_applies(self):
        now = [0.0]
        waits = []

        def sleep(seconds):
            waits.append(seconds)
            now[0] += seconds

        limiter = RateLimiter([(2, 1.0), (3, 60.0)], clock=lambda: now[0], sleep=sleep)
        for _ in range(4):
            limiter.acquire()
        self.assertGreaterEqual(now[0], 60.0)


class FakeResponse:
    def __init__(self, status, body=None, headers=None):
        self.status_code = status
        self.body = body
        self.headers = headers or {}

    def json(self):
        return self.body


class ClientTests(unittest.TestCase):
    def client(self, responses):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        session = mock.Mock()
        session.headers = {}
        session.get.side_effect = responses
        self.session = session
        return CachedClient("t", "https://x.test", Path(self.dir.name), RateLimiter([(100, 1.0)]),
                            session=session, log=lambda _t: None, sleep=lambda _s: None)

    def test_operators_survive_url_encoding(self):
        url = build_url("https://x.test/v1", "location", [("date", ">=", "2024-03-02T15:00:00")])
        self.assertEqual(url, "https://x.test/v1/location?date>=2024-03-02T15:00:00")

    def test_retries_server_errors_then_caches(self):
        client = self.client([FakeResponse(503), FakeResponse(429), FakeResponse(200, [1])])
        self.assertEqual(client.get("a"), [1])
        self.assertEqual(client.get("a"), [1])
        self.assertEqual(self.session.get.call_count, 3)

    def test_gives_up_with_clear_error(self):
        client = self.client([FakeResponse(500)] * 7)
        with self.assertRaises(NetworkError):
            client.get("a")

    def test_live_lock_is_reported(self):
        client = self.client([FakeResponse(401)])
        with self.assertRaisesRegex(DataUnavailable, "live"):
            client.get("a")

    def test_not_found_means_empty(self):
        self.assertEqual(self.client([FakeResponse(404)]).get("a"), [])


def race_row(season="2010", date="2010-03-14"):
    return {"season": season, "round": "1", "raceName": "Test GP", "date": date,
            "time": "12:00:00Z",
            "Circuit": {"circuitId": "bahrain", "circuitName": "Bahrain"}}


class ClassifyTests(unittest.TestCase):
    NOW = sources.parse_time("2026-11-06T00:00:00Z")

    def test_before_lap_timing(self):
        info = sources.classify_race(race_row("1990", "1990-03-11"), [], self.NOW)
        self.assertEqual(info.availability, sources.AVAIL_UNAVAILABLE)
        self.assertIn("1996", info.note)

    def test_lap_timing_era_is_estimated(self):
        info = sources.classify_race(race_row(), [], self.NOW)
        self.assertEqual(info.availability, sources.AVAIL_ESTIMATED)

    def test_telemetry_when_session_exists(self):
        session = {"date_start": "2024-03-02T15:00:00+00:00", "session_key": 1}
        info = sources.classify_race(race_row("2024", "2024-03-02"), [session], self.NOW)
        self.assertEqual(info.availability, sources.AVAIL_TELEMETRY)

    def test_cancelled_session_is_not_telemetry(self):
        session = {"date_start": "2026-04-12T15:00:00+00:00", "is_cancelled": True}
        info = sources.classify_race(race_row("2026", "2026-04-12"), [session], self.NOW)
        self.assertEqual(info.availability, sources.AVAIL_ESTIMATED)

    def test_future(self):
        info = sources.classify_race(race_row("2026", "2026-12-06"), [], self.NOW)
        self.assertEqual(info.availability, sources.AVAIL_FUTURE)

    def test_duration_parsing(self):
        self.assertAlmostEqual(sources.parse_duration("1:39.264"), 99.264)
        self.assertAlmostEqual(sources.parse_duration("25.5"), 25.5)
        self.assertAlmostEqual(sources.parse_duration("1:00:00.5"), 3600.5)
        self.assertIsNone(sources.parse_duration(""))
        self.assertIsNone(sources.parse_duration("n/a"))


class EstimateTests(unittest.TestCase):
    def test_progress_hits_every_real_lap_time(self):
        timing = estimate.DriverTiming([100.0, 95.0, 96.0])
        motion = estimate.build_motion(timing, -0.01, 96.0)
        got = estimate.progress_at(motion, np.array([100.0, 195.0, 291.0]))
        np.testing.assert_allclose(got, [1.0, 2.0, 3.0])

    def test_progress_never_goes_backwards(self):
        timing = estimate.DriverTiming([100.0, 120.0, 118.0, 95.0], {2: 24.0})
        motion = estimate.build_motion(timing, -0.01, 96.0)
        values = estimate.progress_at(motion, np.arange(0, 433, 0.5))
        self.assertTrue(np.all(np.diff(values) >= -1e-9))

    def test_pit_stop_holds_at_the_line_across_the_lap_boundary(self):
        timing = estimate.DriverTiming([100.0, 110.0, 110.0, 95.0], {2: 20.0})
        motion = estimate.build_motion(timing, 0.0, 96.0)
        self.assertEqual(motion.pit_windows, [(200.0, 220.0)])
        held = estimate.progress_at(motion, np.array([201.0, 210.0, 219.0]))
        np.testing.assert_allclose(held, [2.0, 2.0, 2.0])

    def test_very_long_lap_stops_rather_than_crawls(self):
        timing = estimate.DriverTiming([100.0, 3000.0, 100.0])
        motion = estimate.build_motion(timing, 0.0, 100.0)
        progress = estimate.progress_at(motion, np.array([100.0 + 125.0, 2000.0]))
        np.testing.assert_allclose(progress, [2.0, 2.0])

    def test_missing_laps_are_filled_and_counted(self):
        durations, filled = estimate.fill_missing_laps({1: 100.0, 3: 90.0}, 4, {2: 95.0}, 91.0)
        self.assertEqual(durations, [100.0, 95.0, 90.0, 91.0])
        self.assertEqual(filled, 2)

    def test_slow_window_needs_most_of_the_field(self):
        normal = [estimate.DriverTiming([90.0] * 6) for _ in range(5)]
        self.assertEqual(estimate.slow_windows(normal, 90.0), [])
        one_slow = normal[:4] + [estimate.DriverTiming([90, 90, 150, 90, 90, 90])]
        self.assertEqual(estimate.slow_windows(one_slow, 90.0), [])
        field = [estimate.DriverTiming([90, 90, 150, 90, 90, 90]) for _ in range(5)]
        self.assertEqual(estimate.slow_windows(field, 90.0), [(180.0, 330.0)])

    def test_order_ranks_out_cars_last_and_finishers_by_time(self):
        positions = estimate.compute_order(np.array([50.0, 50.0, 51.0, 20.0]),
                                           np.array([5000.0, 4990.0, 0.0, 0.0]),
                                           np.array([False, False, True, False]))
        self.assertEqual(positions.tolist(), [2, 1, 4, 3])

    def test_classification_status(self):
        self.assertTrue(estimate.is_classified({"status": "Finished"}))
        self.assertTrue(estimate.is_classified({"status": "+1 Lap"}))
        self.assertTrue(estimate.is_classified({"status": "Lapped"}))
        self.assertFalse(estimate.is_classified({"status": "Engine"}))
        self.assertTrue(estimate.did_not_start({"status": "Withdrew"}))


class TelemetryTests(unittest.TestCase):
    def test_lights_out_prefers_lap_one_start(self):
        laps = [{"lap_number": 1, "date_start": "2024-03-02T15:03:42.341+00:00"}] * 3
        t0 = telemetry.lights_out_time(laps, [], {"date_start": "2024-03-02T15:00:00+00:00"})
        self.assertEqual(t0, sources.parse_time("2024-03-02T15:03:42.341+00:00"))

    def test_lights_out_falls_back_to_session_started(self):
        rc = [{"date": "2024-03-02T15:03:40+00:00", "message": "SESSION STARTED"}]
        t0 = telemetry.lights_out_time([], rc, {"date_start": "2024-03-02T15:00:00+00:00"})
        self.assertEqual(t0, sources.parse_time(rc[0]["date"]))

    def test_track_status_sequence(self):
        def msg(second, text, category="Other", flag=None, scope=None):
            return {"date": f"2024-01-01T00:00:{second:02d}+00:00", "message": text,
                    "category": category, "flag": flag, "scope": scope}
        rc = [msg(10, "SAFETY CAR DEPLOYED", "SafetyCar"),
              msg(20, "RED FLAG", "Flag", "RED", "Track"),
              msg(30, "TRACK CLEAR", "Flag", "GREEN", "Track"),
              msg(40, "VIRTUAL SAFETY CAR DEPLOYED", "SafetyCar"),
              msg(50, "VIRTUAL SAFETY CAR ENDING", "SafetyCar"),
              msg(55, "YELLOW IN TRACK SECTOR 3", "Flag", "YELLOW", "Sector")]
        t0 = sources.parse_time("2024-01-01T00:00:00+00:00")
        events = telemetry.track_status_events(rc, t0)
        self.assertEqual(events, [(10.0, rf.TRACK_SC), (20.0, rf.TRACK_RED),
                                  (30.0, rf.TRACK_GREEN), (40.0, rf.TRACK_VSC),
                                  (50.0, rf.TRACK_GREEN)])

    def test_gap_encoding(self):
        self.assertEqual(telemetry.encode_gap(12.34), 123)
        self.assertEqual(telemetry.encode_gap("+1 LAP"), rf.GAP_LAPS_BASE + 1)
        self.assertEqual(telemetry.encode_gap("+2 LAPS"), rf.GAP_LAPS_BASE + 2)
        self.assertEqual(telemetry.encode_gap(None), rf.GAP_UNKNOWN)

    def test_step_values(self):
        got = telemetry.step_values(np.array([1.0, 3.0]), np.array([5, 6]),
                                    np.array([0.0, 1.0, 2.0, 3.5]), 9)
        self.assertEqual(got.tolist(), [9, 5, 5, 6])

    def test_ties_become_unique_positions(self):
        got = telemetry.dense_order(np.array([[1, 1, 3], [2, 1, 3]]))
        self.assertEqual(got.tolist(), [[1, 2, 3], [2, 1, 3]])

    def test_null_lap_duration_uses_next_lap_start(self):
        rows = [{"lap_number": 1, "date_start": "2024-01-01T00:00:00+00:00", "lap_duration": None},
                {"lap_number": 2, "date_start": "2024-01-01T00:01:40+00:00", "lap_duration": 90.0},
                {"lap_number": 3, "date_start": "2024-01-01T00:03:10+00:00", "lap_duration": None}]
        t0 = sources.parse_time("2024-01-01T00:00:00+00:00")
        laps, unknown = telemetry.load_laps(rows, t0)
        self.assertEqual(laps, [(1, 0.0, 100.0, True), (2, 100.0, 190.0, False)])
        self.assertEqual(unknown, 1)

    def test_laps_timed_before_lights_out_are_dropped(self):
        # Japan 2024: a stray lap from the aborted start, 25 minutes before the restart.
        rows = [{"lap_number": 1, "date_start": "2024-01-01T01:00:00+00:00", "lap_duration": 90.0},
                {"lap_number": 2, "date_start": "2024-01-01T00:34:30+00:00", "lap_duration": 203.0}]
        t0 = sources.parse_time("2024-01-01T01:00:00+00:00")
        laps, unusable = telemetry.load_laps(rows, t0)
        self.assertEqual(laps, [(1, 0.0, 90.0, False)])
        self.assertEqual(unusable, 1)

    def test_aborted_start_is_detected(self):
        t0 = sources.parse_time("2024-04-07T05:32:00.407+00:00")
        rc = [{"date": "2024-04-07T05:04:24.473+00:00", "message": "SESSION ABORTED"}]
        self.assertTrue(telemetry.aborted_start(rc, t0))
        later = [{"date": "2024-04-07T06:10:00+00:00", "message": "SESSION ABORTED"}]
        self.assertFalse(telemetry.aborted_start(later, t0))

    def test_lap_rows_absent_from_the_source_are_counted(self):
        history = [(1, 0.0, 90.0, False), (2, 90.0, 180.0, False), (4, 270.0, 360.0, False)]
        self.assertEqual(telemetry.missing_lap_rows(history), 1)
        self.assertEqual(telemetry.missing_lap_rows([]), 0)

    def test_finish_uses_official_time_when_final_lap_has_no_end(self):
        history = [(69, 0.0, 90.0, False), (70, 90.0, 180.0, False)]
        self.assertEqual(telemetry.finish_time(history, {"number_of_laps": 71, "duration": 275.0}),
                         275.0)

    def test_finish_uses_lap_end_normally(self):
        history = [(70, 90.0, 180.0, False)]
        self.assertEqual(telemetry.finish_time(history, {"number_of_laps": 70, "duration": 179.5}),
                         180.0)
        self.assertEqual(telemetry.finish_time(history, {}), 180.0)

    def test_location_teleport_is_removed(self):
        t = np.arange(0, 10, 0.25)
        x = t * 800.0          # 80 m/s in decimetres.
        y = np.zeros_like(t)
        x[10:13] += 26000      # Three samples 2.6 km away, as seen in the real feed.
        keep = telemetry.remove_glitches(t, x, y)
        self.assertEqual(np.nonzero(~keep)[0].tolist(), [10, 11, 12])

    def test_long_gap_does_not_reject_the_rest_of_the_race(self):
        t = np.array([0.0, 0.25, 120.0, 120.25])
        x = np.array([0.0, 100.0, 90000.0, 90100.0])
        self.assertTrue(telemetry.remove_glitches(t, x, np.zeros(4)).all())

    def test_pit_date_is_the_exit_time(self):
        rows = [{"date": "2024-01-01T00:02:00+00:00", "lane_duration": 25.0, "lap_number": 3}]
        t0 = sources.parse_time("2024-01-01T00:00:00+00:00")
        windows, laps = telemetry.pit_windows(rows, [], t0, set())
        self.assertEqual(windows, [(95.0, 120.0)])
        self.assertEqual(laps, {3})

    def test_pit_fallback_uses_out_laps(self):
        windows, laps = telemetry.pit_windows([], [(4, 300.0, 400.0, False)], 0.0, {4})
        self.assertEqual(windows, [(288.0, 312.0)])
        self.assertEqual(laps, {3})


class OutlineTests(unittest.TestCase):
    def circle(self, radius=800.0, seconds=90.0, rate=3.7):
        t = np.arange(0, seconds, 1 / rate)
        angle = 2 * np.pi * t / seconds
        return t, radius * 10 * np.cos(angle), radius * 10 * np.sin(angle)

    def test_lap_becomes_a_resampled_outline(self):
        t, x, y = self.circle()
        result = outline.outline_from_samples(t, x, y, 0.0, 90.0, "test")
        self.assertEqual(len(result.points), outline.OUTLINE_POINTS)
        self.assertAlmostEqual(result.length_m, 2 * np.pi * 800, delta=30)

    def test_lap_with_skipped_section_is_rejected(self):
        t, x, y = self.circle()
        keep = np.ones(len(t), bool)
        keep[100:112] = False
        result = outline.outline_from_samples(t[keep], x[keep], y[keep], 0.0, 90.0, "test")
        self.assertIsNone(result)

    def test_short_coverage_is_rejected(self):
        t, x, y = self.circle()
        self.assertIsNone(outline.outline_from_samples(t, x, y, 0.0, 200.0, "test"))

    def test_transform_stays_in_map_and_never_mirrors(self):
        t, x, y = self.circle()
        pts = np.column_stack([x, y])[:-1] / 10
        pts[:, 0] *= 2.2
        fit = outline.fit_transform(pts)
        mapped = fit.apply(pts)
        self.assertGreaterEqual(mapped.min(), 0)
        self.assertLessEqual(mapped.max(), rf.MAP_UNITS)

        def signed_area(p):
            return 0.5 * np.sum(p[:, 0] * np.roll(p[:, 1], -1) - np.roll(p[:, 0], -1) * p[:, 1])
        # y flips for the screen, so an anticlockwise loop has opposite signed area on screen,
        # exactly as it does when you look at it the right way up.
        self.assertLess(signed_area(mapped) * signed_area(pts), 0)

    def test_profile_slows_time_where_the_car_was_slow(self):
        line = outline.Outline(np.array([[0.0, 0.0], [100.0, 0.0], [100.0, 100.0], [0.0, 100.0]]),
                               "t", 400.0, np.array([0.0, 0.9, 1.0]), np.array([0.0, 0.1, 1.0]))
        xy = line.fraction_to_xy(np.array([0.9]))
        np.testing.assert_allclose(xy, [[40.0, 0.0]], atol=1e-6)

    def test_schematic_is_flagged(self):
        self.assertTrue(outline.schematic_outline().schematic)


class LayoutTests(unittest.TestCase):
    def test_old_layout_is_flagged(self):
        self.assertIn("2002", circuits.layout_warning("hockenheimring", 2000, "German GP"))
        self.assertIsNone(circuits.layout_warning("hockenheimring", 2018, "German GP"))

    def test_sakhir_outer_layout_only_for_that_race(self):
        self.assertIsNotNone(circuits.layout_warning("bahrain", 2020, "Sakhir Grand Prix"))
        self.assertIsNone(circuits.layout_warning("bahrain", 2020, "Bahrain Grand Prix"))


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.out = Path(self.dir.name)

    def write_catalog(self):
        rows = [{"season": 1990, "round": 1, "name": "US Ø", "circuit": "Phoenix",
                 "date": "1990-03-11", "availability": "U", "note": "Results only."},
                {"season": 2024, "round": 1, "name": "Bahrain", "circuit": "Sakhir",
                 "date": "2024-03-02", "availability": "T", "note": "OpenF1."}]
        builder.catalog_path(self.out).write_text(json.dumps({"x": rows}), encoding="utf-8")

    def test_catalog_only_rows(self):
        self.write_catalog()
        lines = builder.write_manifest(self.out).read_text(encoding="ascii").splitlines()
        self.assertTrue(lines[0].startswith("F1RM\t1\t"))
        first = lines[1].split("\t")
        self.assertEqual(len(first), 11)
        self.assertEqual(first[2], "U")
        self.assertEqual(lines[2].split("\t")[2], "N")

    def test_built_race_replaces_catalog_row_and_survives_rebuild(self):
        self.write_catalog()
        root = builder.root_dir(self.out)
        size, crc = rf.write_package(tiny_package(), root / "races" / "2024_01.f1r")
        (root / "reports").mkdir()
        (root / "reports" / "2024_01.json").write_text(json.dumps(
            {"season": 2024, "round": 1, "fidelity": "telemetry", "flags": 0,
             "file": "2024_01.f1r", "size": size, "crc32": f"{crc:08x}", "race": "Bahrain",
             "circuit": "Sakhir", "date": "2024-03-02"}), encoding="utf-8")
        lines = builder.write_manifest(self.out).read_text(encoding="ascii").splitlines()
        row = lines[2].split("\t")
        self.assertEqual(row[2:7], ["R", "T", "2024_01.f1r", str(size), f"{crc:08x}"])
        self.assertEqual(builder.verify_all(self.out, log=lambda _t: None), 0)

    def test_corrupt_file_fails_verify(self):
        root = builder.root_dir(self.out)
        path = root / "races" / "2024_01.f1r"
        rf.write_package(tiny_package(), path)
        path.write_bytes(path.read_bytes()[:-3])
        self.assertEqual(builder.verify_all(self.out, log=lambda _t: None), 1)

    def test_long_line_fits_firmware_buffer(self):
        row = ["2024", "1", "N", "E", "-", "0", "0", "A" * 200, "B" * 200, "2024-01-01", "C" * 300]
        line = builder.fit_line(row)
        self.assertLessEqual(len(line), builder.MAX_LINE)
        self.assertEqual(len(line.split("\t")), 11)


class ServerTests(unittest.TestCase):
    def test_only_race_files_are_served(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "f1replay" / "races").mkdir(parents=True)
            for name in ("manifest.txt", "wifi.txt", "races/2024_01.f1r"):
                (root / "f1replay" / name).write_text("x")
            self.assertIsNotNone(server.resolve(root, "/f1replay/manifest.txt"))
            self.assertIsNotNone(server.resolve(root, "/f1replay/races/2024_01.f1r?x=1"))
            for bad in ("/f1replay/wifi.txt", "/f1replay/../f1replay/wifi.txt",
                        "/f1replay/races/../wifi.txt", "/", "/etc/passwd"):
                self.assertIsNone(server.resolve(root, bad), bad)


if __name__ == "__main__":
    unittest.main()
