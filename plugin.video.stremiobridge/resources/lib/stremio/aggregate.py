"""Running requests to several addons at once."""

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

MAX_WORKERS = 8


def gather(tasks, on_progress=None, max_workers=MAX_WORKERS):
    """Run ``(label, fn)`` tasks in parallel.

    Returns ``(results, errors, cancelled)``: `results` is a list of
    ``(task_index, value)`` in task order (not completion order), `errors` a list
    of ``(label, exception)``. `on_progress(done, total, label)` is called as each
    task finishes; returning False stops waiting for the rest.
    """
    total = len(tasks)
    values, errors = {}, []
    if not tasks:
        return [], errors, False

    pool = ThreadPoolExecutor(max_workers=min(max_workers, total))
    futures = {pool.submit(fn): (i, label) for i, (label, fn) in enumerate(tasks)}
    pending, done_count, cancelled = set(futures), 0, False
    try:
        while pending and not cancelled:
            # Short waits so a progress dialog's cancel button is noticed promptly.
            finished, pending = wait(pending, timeout=0.25, return_when=FIRST_COMPLETED)
            for future in finished:
                i, label = futures[future]
                try:
                    values[i] = (i, future.result())
                except Exception as exc:  # noqa: BLE001 - reported to the caller
                    errors.append((label, exc))
                done_count += 1
                if on_progress and on_progress(done_count, total, label) is False:
                    cancelled = True
            if not finished and on_progress and on_progress(done_count, total, None) is False:
                cancelled = True
    finally:
        # Don't block on stragglers after a cancel; their results are discarded.
        pool.shutdown(wait=not cancelled, cancel_futures=True)
    return [values[i] for i in sorted(values)], errors, cancelled
