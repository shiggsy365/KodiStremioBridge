import threading
import time

from stremio.aggregate import gather


def test_results_in_task_order_and_errors():
    def slow():
        time.sleep(0.1)
        return "slow"

    def boom():
        raise ValueError("boom")

    progress = []
    results, errors, cancelled = gather(
        [("a", slow), ("b", lambda: "fast"), ("c", boom)],
        on_progress=lambda done, total, label: progress.append((done, total)) or True,
    )
    assert results == [(0, "slow"), (1, "fast")]
    assert [(label, str(exc)) for label, exc in errors] == [("c", "boom")]
    assert not cancelled and (3, 3) in progress


def test_cancel_stops_waiting():
    release = threading.Event()
    started = time.time()
    results, _, cancelled = gather(
        [("fast", lambda: 1), ("stuck", lambda: release.wait(5))],
        on_progress=lambda done, total, label: False,
    )
    release.set()
    assert cancelled and time.time() - started < 2
    assert results == [(0, 1)]


def test_empty():
    assert gather([]) == ([], [], False)
