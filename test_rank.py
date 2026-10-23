"""Small self-check for the ranking core. Run: python test_rank.py"""
import json
import pathlib
import tempfile
from datetime import date, timedelta

import checkins
import merge_pending
import tracker

TODAY = date.today()


def day(offset):
    return (TODAY + timedelta(days=offset)).isoformat()


def task(task_id, tier=2, impact=0.2, deadline=None, status="open", **extra):
    item = {
        "id": task_id,
        "title": task_id,
        "tier": tier,
        "impact": impact,
        "status": status,
        "first_seen": day(-10),
    }
    if deadline is not None:
        item["deadline"] = deadline
    item.update(extra)
    return item


def config(**extra):
    data = dict(tracker.DEFAULTS)
    data.update(extra)
    return tracker.clamp_settings(data)


def reset_today():
    tracker.save_today({
        "date": TODAY.isoformat(),
        "mode": "auto",
        "ids": [],
        "band_ids": [],
        "value_ids": [],
        "routine_ids": [],
        "filler_ids": [],
        "auto_added": [],
    })


def test_band_buckets():
    s = config(use_deadlines=True, band_late_days=14, band_ahead_days=7)
    assert tracker.band_bucket(task("late", deadline=day(-1)), s) == 0
    assert tracker.band_bucket(task("late14", deadline=day(-14)), s) == 0
    assert tracker.band_bucket(task("late15", deadline=day(-15)), s) is None
    assert tracker.band_bucket(task("today", deadline=day(0)), s) == 1
    assert tracker.band_bucket(task("in3", deadline=day(3)), s) == 1
    assert tracker.band_bucket(task("in4", deadline=day(4)), s) == 2
    assert tracker.band_bucket(task("in7", deadline=day(7)), s) == 2
    assert tracker.band_bucket(task("in8", deadline=day(8)), s) is None
    wide = config(use_deadlines=True, band_ahead_days=14)
    assert tracker.band_bucket(task("in8", deadline=day(8)), wide) == 3
    assert tracker.band_bucket(task("in14", deadline=day(14)), wide) == 3
    assert tracker.band_bucket(task("in15", deadline=day(15)), wide) is None
    off = config(use_deadlines=False)
    assert tracker.band_bucket(task("today", deadline=day(0)), off) is None


def test_band_order():
    tasks = {"tasks": [
        task("low", tier=3, impact=0.0, deadline=day(1)),
        task("high", tier=1, impact=0.4, deadline=day(1)),
        task("old", tier=2, impact=0.2, deadline=day(-1)),
    ]}
    s = config(use_deadlines=True, focus_size=3)
    built = tracker.build_day(tasks, s)
    assert [t["id"] for t in built["band"]] == ["old", "high", "low"]


def test_lapsed():
    s = config(lapse_limit_days=14)
    assert not tracker.is_lapsed(task("edge", deadline=day(-14)), s)
    assert tracker.is_lapsed(task("old", deadline=day(-15)), s)
    assert not tracker.is_lapsed(task("kept", deadline=day(-15), keep_until=day(1)), s)
    assert not tracker.is_lapsed(task("done", deadline=day(-15), status="done"), s)
    tasks = {"tasks": [
        task("live"),
        task("old", deadline=day(-15)),
        task("routine", routine_id="r1"),
    ]}
    assert [t["id"] for t in tracker.ranked_open(tasks, s)] == ["live"]


def test_lapsed_order():
    s = config(lapse_limit_days=14)
    tasks = {"tasks": [
        task("capped", deadline=day(-20), category="none"),
        task("expiring_old", deadline=day(-40), category="incoming-window"),
        task("expiring_new", deadline=day(-15), category="incoming-window"),
        task("compound_new", deadline=day(-15), category="legal-account"),
        task("compound_old", deadline=day(-40), category="client-job"),
        task("live", deadline=day(-2)),
    ]}
    rows = tracker.lapsed_tasks(tasks, s)
    assert [t["id"] for t in rows] == [
        "compound_old", "compound_new", "expiring_new", "expiring_old", "capped",
    ]
    assert [t["lapse_kind"] for t in rows] == [
        "compounding", "compounding", "expiring", "expiring", "capped",
    ]


def test_lapsed_filler():
    tasks = {"tasks": [
        task("live1"),
        task("live2"),
        task("old", deadline=day(-20)),
    ]}
    built = tracker.build_day(tasks, config(focus_size=3))
    assert built["ids"] == ["live1", "live2", "old"]
    assert built["filler_ids"] == ["old"]
    short = tracker.build_day({"tasks": [task("live1"), task("live2")]}, config(focus_size=3))
    assert short["ids"] == ["live1", "live2"]
    assert short["filler_ids"] == []


def test_digest_lapsed_section():
    reset_today()
    tasks = {"tasks": [task("old", deadline=day(-20)), task("live")]}
    text = tracker.render_digest(tasks)
    assert "Lapsed" in text and "old (20d late, capped)" in text
    assert "[lapsed 20d, filler]" in text
    assert "Counts: P2: 1, Lapsed: 1" in text
    assert tracker.load_today()["filler_ids"] == ["old"]


def test_soft_deadline_score():
    soft = task("soft", soft_deadline=day(1))
    plain = task("plain")
    dated = config(use_deadlines=True)
    assert tracker.score(soft, dated) > tracker.score(plain, dated)
    assert tracker.score(soft, config(use_deadlines=False)) == tracker.score(
        plain, config(use_deadlines=False)
    )


def test_effort_tiebreak():
    tasks = {"tasks": [
        task("l", effort="L"),
        task("s", effort="S"),
        task("m", effort="M"),
        task("u"),
    ]}
    order = [t["id"] for t in tracker.ranked_open(tasks, config())]
    assert order == ["s", "m", "u", "l"]


def test_unblocks_tiebreak():
    tasks = {"tasks": [
        task("a"),
        task("b"),
        task("dep", prerequisites=["a"]),
    ]}
    assert tracker.ranked_open(tasks, config())[0]["id"] == "a"


def test_prereq_marker_and_warning():
    tasks = {"tasks": [
        task("a"),
        task("b", prerequisites=["a"]),
    ]}
    b = tasks["tasks"][1]
    assert [t["id"] for t in tracker.open_prerequisites(b, tasks)] == ["a"]
    assert "prereq" in tracker.flags_for(b, config(), tasks)
    assert "a" in tracker.completion_warning(b, tasks)
    tasks["tasks"][0]["status"] = "done"
    assert tracker.open_prerequisites(b, tasks) == []
    assert "prereq" not in tracker.flags_for(b, config(), tasks)
    assert tracker.completion_warning(b, tasks) == ""


def test_dependency_issues():
    tasks = {"tasks": [
        task("a", prerequisites=["missing"]),
        task("b", prerequisites=["b"]),
        task("c", prerequisites=["d"]),
        task("d", prerequisites=["c"]),
    ]}
    issues = tracker.dependency_issues(tasks)
    assert any("unknown prerequisite missing" in i for i in issues)
    assert any("depends on itself" in i for i in issues)
    assert any("cycle among" in i for i in issues)


def test_build_day():
    tasks = {"tasks": [
        task("band1", tier=1, impact=0.4, deadline=day(0)),
        task("band2", tier=1, impact=0.4, deadline=day(1)),
        task("band3", tier=1, impact=0.4, deadline=day(5)),
        task("value", tier=2, impact=0.4),
        task("normal1", tier=1, impact=0.0),
        task("normal2", tier=2, impact=0.0),
        task("normal3", tier=3, impact=0.0),
        task("normal4", tier=4, impact=0.0),
    ]}
    s = config(use_deadlines=True, focus_size=7, value_slots=1)
    built = tracker.build_day(tasks, s)
    assert [t["id"] for t in built["band"]] == ["band1", "band2", "band3"]
    assert [t["id"] for t in built["value"]] == ["value"]
    assert built["ids"][:4] == ["band1", "band2", "band3", "value"]
    assert built["ids"][4:] == ["normal1", "normal2", "normal3"]
    assert len(built["ids"]) == 7


def test_value_rules():
    tasks = {"tasks": [
        task("v1", tier=2, impact=0.3),
        task("v2", tier=1, impact=0.5),
        task("v3", tier=2, impact=0.2),
        task("v4", tier=2, impact=0.4, deadline=day(1)),
    ]}
    s = config(use_deadlines=True, focus_size=7, value_slots=1)
    built = tracker.build_day(tasks, s)
    assert [t["id"] for t in built["value"]] == ["v1"]


def test_focus_size_and_no_padding():
    tasks = {"tasks": [task(f"n{i}") for i in range(10)]}
    s = config(focus_size=3)
    assert len(tracker.build_day(tasks, s)["ids"]) == 3
    tiny = {"tasks": [task("only")]}
    assert tracker.build_day(tiny, config(focus_size=7))["ids"] == ["only"]
    assert config(focus_size=99)["focus_size"] == 10


def test_shuffle():
    reset_today()
    tasks = {"tasks": [
        task("band", tier=1, impact=0.4, deadline=day(1)),
        task("value", tier=2, impact=0.4),
        *[task(f"n{i}", tier=2, impact=0.0) for i in range(10)],
    ]}
    s = config(use_deadlines=True, focus_size=5, value_slots=1)
    picks = tracker.shuffle_day(tasks, s)
    ids = [t["id"] for t in picks]
    assert "band" in ids and "value" in ids
    assert len(ids) == 5
    assert len([i for i in ids if i.startswith("n")]) == 3


def test_refill():
    reset_today()
    tasks = {"tasks": [
        task("a"),
        task("b"),
        task("c", status="done", completed=TODAY.isoformat()),
        task("d"),
        task("e"),
    ]}
    s = config(focus_size=3, refill_when_done=True)
    tracker.save_today({
        "date": TODAY.isoformat(),
        "mode": "auto",
        "ids": ["a", "b", "c"],
        "auto_added": [],
    })
    picks, mode = tracker.today_picks(tasks, settings=s)
    assert [t["id"] for t in picks] == ["a", "b", "c", "d"]
    assert tracker.load_today()["auto_added"] == ["d"]

    reset_today()
    tracker.save_today({
        "date": TODAY.isoformat(),
        "mode": "auto",
        "ids": ["a", "b", "c"],
        "auto_added": [],
    })
    picks, _ = tracker.today_picks(tasks, settings=config(focus_size=3, refill_when_done=False))
    assert [t["id"] for t in picks] == ["a", "b", "c"]


def test_un_done_trims():
    reset_today()
    tasks = {"tasks": [
        task("a"),
        task("b"),
        task("c", status="done", completed=TODAY.isoformat()),
        task("d"),
        task("e"),
    ]}
    s = config(focus_size=3, refill_when_done=True)
    tracker.save_today({
        "date": TODAY.isoformat(),
        "mode": "auto",
        "ids": ["a", "b", "c"],
        "auto_added": [],
    })
    tracker.today_picks(tasks, settings=s)
    tasks["tasks"][2]["status"] = "open"
    tasks["tasks"][2]["completed"] = None
    picks, _ = tracker.today_picks(tasks, settings=s)
    assert [t["id"] for t in picks] == ["a", "b", "c"]
    assert tracker.load_today()["auto_added"] == []


def test_trim_on_reopen():
    reset_today()
    tracker.save_today({
        "date": TODAY.isoformat(),
        "mode": "auto",
        "ids": ["a", "b", "c", "d"],
        "auto_added": ["d"],
    })
    tracker.trim_auto_added()
    assert tracker.load_today()["ids"] == ["a", "b", "c"]
    assert tracker.load_today()["auto_added"] == []


def test_console_fields():
    import console
    old_tasks = console.TASKS
    with tempfile.TemporaryDirectory() as tmp:
        console.TASKS = pathlib.Path(tmp) / "tasks.json"
        console.TASKS.write_text(
            json.dumps({"tasks": [task("a")]}), encoding="utf-8"
        )
        try:
            console.update_task({"id": "a", "field": "soft_deadline", "value": "2026-12-01"})
            console.update_task({"id": "a", "field": "drop_reason", "value": "no longer needed"})
            data = json.loads(console.TASKS.read_text(encoding="utf-8"))
            saved = data["tasks"][0]
            assert saved["soft_deadline"] == "2026-12-01"
            assert saved["drop_reason"] == "no longer needed"
            try:
                console.update_task({"id": "a", "field": "soft_deadline", "value": "bad"})
            except ValueError as exc:
                assert str(exc) == "date must be YYYY-MM-DD"
            else:
                raise AssertionError("bad soft deadline accepted")
        finally:
            console.TASKS = old_tasks


def test_checkin_queue():
    s = config()
    old = task("old", first_seen=day(-50))
    older = task("older", first_seen=day(-60))
    fresh = task("fresh", first_seen=day(-10))
    started = task("started", first_seen=day(-50), status="started")
    dated = task("dated", first_seen=day(-50), deadline=day(1))
    stale = task("stale", first_seen=day(-50), why="demoted: stale")
    rows = tracker.checkin_queue(
        {"tasks": [old, older, fresh, started, dated, stale]}, s, notes={}
    )
    assert [t["id"] for t in rows] == ["older", "old"]
    assert [t["checkin_due_date"] for t in rows] == [day(-15), day(-5)]

    edited = task("edited", first_seen=day(-50), source_note="n1")
    assert tracker.checkin_queue(
        {"tasks": [edited]}, s, notes={"n1": TODAY}
    ) == []

    queued = task(
        "queued", first_seen=day(-50), checkin_state="queued",
        checkin_due=day(-5), checkin_at=day(-1),
    )
    active = task(
        "active", first_seen=day(-50), checkin_state="active",
        checkin_until=day(10),
    )
    assert [t["id"] for t in tracker.checkin_queue(
        {"tasks": [queued, active]}, s, notes={}
    )] == ["queued"]


def test_checkin_flow():
    old_tasks = tracker.TASKS
    old_cache = checkins.CACHE
    with tempfile.TemporaryDirectory() as tmp:
        tracker.TASKS = pathlib.Path(tmp) / "tasks.json"
        checkins.CACHE = pathlib.Path(tmp) / "checkin_cache.json"
        s = config()
        try:
            items = [task(f"t{i}", first_seen=day(-50 - i)) for i in range(10)]
            tracker.TASKS.write_text(
                json.dumps({"version": 1, "tasks": items}), encoding="utf-8"
            )
            summary = checkins.run(settings=s, notes={}, use_jev=False)
            assert summary["queued"] == 10
            data = json.loads(tracker.TASKS.read_text(encoding="utf-8"))
            rows = tracker.checkin_queue(data, s, notes={})
            assert [t["id"] for t in rows] == [f"t{i}" for i in range(9, -1, -1)]
            assert checkins.answer("t0", "unsure", settings=s)["tier"] == 3
            demoted = json.loads(tracker.TASKS.read_text(encoding="utf-8"))
            one = next(t for t in demoted["tasks"] if t["id"] == "t0")
            assert "demoted: stale" in one["why"]
            assert one["demoted_from"] == 2
            assert checkins.answer("t0", "restore", settings=s)["tier"] == 2

            expired = task(
                "expired", first_seen=day(-50), checkin_state="queued",
                checkin_due=day(-20), checkin_at=day(-15),
            )
            tracker.TASKS.write_text(
                json.dumps({"version": 1, "tasks": [expired]}), encoding="utf-8"
            )
            summary = checkins.run(
                settings=config(checkin_answer_days=14), notes={}, use_jev=False
            )
            assert summary["expired"] == 1
            data = json.loads(tracker.TASKS.read_text(encoding="utf-8"))
            assert data["tasks"][0]["checkin_state"] == "demoted"

            restored = task(
                "restored", first_seen=day(-50), source_note="n2",
                checkin_state="demoted", demoted_from=1, demoted_at=day(-5),
                why="demoted: stale",
            )
            tracker.TASKS.write_text(
                json.dumps({"version": 1, "tasks": [restored]}), encoding="utf-8"
            )
            summary = checkins.run(settings=s, notes={"n2": TODAY}, use_jev=False)
            assert summary["restored"] == 1
            data = json.loads(tracker.TASKS.read_text(encoding="utf-8"))
            assert data["tasks"][0]["tier"] == 1
            assert "demoted: stale" not in data["tasks"][0]["why"]

            high = task("high", first_seen=day(-50))
            low = task("low", first_seen=day(-50))
            tracker.TASKS.write_text(
                json.dumps({"version": 1, "tasks": [high, low]}), encoding="utf-8"
            )
            old_key = checkins.jev_categorize.api_key
            old_judge = checkins.judge
            checkins.jev_categorize.api_key = lambda: "test"
            checkins.judge = lambda rows, all_tasks, notes, cache: {
                "high": 0.9, "low": 0.1,
            }
            try:
                summary = checkins.run(settings=s, notes={})
            finally:
                checkins.jev_categorize.api_key = old_key
                checkins.judge = old_judge
            assert summary["queued"] == 1
            assert summary["demoted"] == 1
            data = json.loads(tracker.TASKS.read_text(encoding="utf-8"))
            by_id = {t["id"]: t for t in data["tasks"]}
            assert by_id["high"]["checkin_state"] == "queued"
            assert by_id["low"]["checkin_state"] == "demoted"
        finally:
            tracker.TASKS = old_tasks
            checkins.CACHE = old_cache


def test_remention():
    old = task(
        "Build the old thing",
        first_seen=day(-50),
        checkin_state="demoted",
        demoted_from=2,
        demoted_at=day(-5),
        why="demoted: stale",
    )
    rows = [old]
    assert merge_pending.match_dupe("Build the old thing", rows) is old
    incoming = {
        "source_date": TODAY.isoformat(),
        "source_note": "new-note",
        "quote": "new quote",
        "status": "started",
        "deadline": day(2),
    }
    assert merge_pending.revive_task(old, incoming, "New note", TODAY.isoformat())
    assert old["tier"] == 2
    assert "checkin_state" not in old
    assert "demoted: stale" not in old["why"]
    assert old["last_seen"] == TODAY.isoformat()
    assert old["source_note"] == "new-note"
    assert old["status"] == "started"
    assert old["mention_count"] == 2
    assert old["deadline"] == day(2)
    assert not merge_pending.revive_task(old, incoming, "New note", TODAY.isoformat())
    assert tracker.checkin_base(old, {}) == TODAY
    assert checkins.free_signal(old, {}) is True

    demoted = task(
        "Another old thing",
        first_seen=day(-50),
        checkin_state="demoted",
        demoted_from=2,
        demoted_at=day(-5),
        last_seen=TODAY.isoformat(),
        why="demoted: stale",
    )
    assert checkins.free_signal(demoted, {}) is True


def test_digest_checkins():
    reset_today()
    text = tracker.render_digest({"tasks": [task("old", first_seen=day(-50))]})
    assert "Check-ins" in text
    assert "old (50d old" in text
    assert "Check-ins: 1" in text


def main():
    old_today = tracker.TODAY_FILE
    with tempfile.TemporaryDirectory() as tmp:
        tracker.TODAY_FILE = pathlib.Path(tmp) / "today.json"
        try:
            test_band_buckets()
            test_band_order()
            test_lapsed()
            test_lapsed_order()
            test_lapsed_filler()
            test_digest_lapsed_section()
            test_soft_deadline_score()
            test_console_fields()
            test_checkin_queue()
            test_checkin_flow()
            test_remention()
            test_digest_checkins()
            test_effort_tiebreak()
            test_unblocks_tiebreak()
            test_prereq_marker_and_warning()
            test_dependency_issues()
            test_build_day()
            test_value_rules()
            test_focus_size_and_no_padding()
            test_shuffle()
            test_refill()
            test_un_done_trims()
            test_trim_on_reopen()
            print("ok")
        finally:
            tracker.TODAY_FILE = old_today
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
