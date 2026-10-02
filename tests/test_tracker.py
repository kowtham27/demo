from instabot.client import TargetInfo
from instabot.notifier import Notifier, _chunks, format_report
from instabot.storage import Member, Storage
from instabot.tracker import Tracker


class FakeSource:
    def __init__(self):
        self.lists = {"followers": [], "following": []}
        self.reported = None  # override reported counts to simulate truncated fetches
        self.private = False
        self.viewable = True

    def target_info(self, username):
        counts = self.reported or {k: len(v) for k, v in self.lists.items()}
        return TargetInfo("42", username, self.private, counts["followers"], counts["following"])

    def can_view(self, target):
        return self.viewable

    def fetch(self, kind, target_pk):
        return list(self.lists[kind])


class RecordingNotifier(Notifier):
    def __init__(self):
        super().__init__()
        self.reports, self.alerts = [], []

    def report_changes(self, target, kind, diff):
        if diff.added or diff.removed:
            self.reports.append((target, kind, diff))

    def alert(self, message):
        self.alerts.append(message)


def members(*names):
    return [Member(pk=f"pk_{n}", username=n) for n in names]


def make():
    source, notifier = FakeSource(), RecordingNotifier()
    tracker = Tracker(source, Storage(":memory:"), notifier, min_completeness=0.9)
    return source, notifier, tracker


def test_first_run_is_silent_baseline():
    source, notifier, tracker = make()
    source.lists["followers"] = members("a", "b", "c")
    tracker.check_target("friend")
    assert notifier.reports == []


def test_detects_new_and_lost_followers_and_following():
    source, notifier, tracker = make()
    source.lists = {"followers": members("a", "b", "c"), "following": members("x", "y")}
    tracker.check_target("friend")

    source.lists = {"followers": members("a", "c", "d", "e"), "following": members("y", "z")}
    tracker.check_target("friend")

    by_kind = {kind: diff for _, kind, diff in notifier.reports}
    assert [m.username for m in by_kind["followers"].added] == ["d", "e"]
    assert [m.username for m in by_kind["followers"].removed] == ["b"]
    assert [m.username for m in by_kind["following"].added] == ["z"]
    assert [m.username for m in by_kind["following"].removed] == ["x"]


def test_no_changes_no_report():
    source, notifier, tracker = make()
    source.lists["followers"] = members("a", "b")
    tracker.check_target("friend")
    tracker.check_target("friend")
    assert notifier.reports == []


def test_rename_is_not_reported_as_unfollow():
    source, notifier, tracker = make()
    source.lists["followers"] = [Member("pk_1", "old_name")]
    tracker.check_target("friend")
    source.lists["followers"] = [Member("pk_1", "new_name")]
    tracker.check_target("friend")
    assert notifier.reports == []
    assert tracker.storage.current_members("42", "followers")["pk_1"].username == "new_name"


def test_truncated_fetch_is_ignored():
    source, notifier, tracker = make()
    source.lists["followers"] = members(*"abcdefghij")
    tracker.check_target("friend")

    # Instagram says 10 followers but only returns 3 -> must not report 7 "unfollows".
    source.lists["followers"] = members("a", "b", "c")
    source.reported = {"followers": 10, "following": 0}
    tracker.check_target("friend")
    assert notifier.reports == []
    assert len(tracker.storage.current_members("42", "followers")) == 10


def test_private_unviewable_target_alerts():
    source, notifier, tracker = make()
    source.private, source.viewable = True, False
    tracker.check_target("friend")
    assert notifier.alerts and "private" in notifier.alerts[0]


def test_history_records_events_after_baseline():
    source, _, tracker = make()
    source.lists["followers"] = members("a")
    tracker.check_target("friend")
    source.lists["followers"] = members("b")
    tracker.check_target("friend")
    changes = {(r["change"], r["username"]) for r in tracker.storage.history("friend")}
    assert changes == {("added", "b"), ("removed", "a")}


def test_report_format_and_chunking():
    from instabot.storage import Diff
    diff = Diff(added=[Member("1", "new_guy", "New <Guy>")], removed=[], baseline=False)
    rich = format_report("friend", "followers", diff, rich=True)
    assert "&lt;Guy&gt;" in rich and "instagram.com/new_guy" in rich
    long_text = "\n".join("x" * 100 for _ in range(100))
    assert all(len(c) <= 4000 for c in _chunks(long_text, 4000))
