"""AN1 (A / Codex #10.1, 2026-11-04): the nonce reservation is now atomic. reserve_nonce() replaces the
is_nonce_seen()+mark_nonce_seen() check-then-mark (two separate connections) that let two concurrent callers
both pass the check before either marked. Driven both ways, including a real concurrency test: under a barrier,
exactly ONE of N simultaneous reservers of the same nonce wins."""
import os, sys, tempfile, threading, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import covenant_unified_v8 as v8


def _db():
    fd, p = tempfile.mkstemp(suffix=".db"); os.close(fd); os.unlink(p)   # a fresh, unused path
    return v8.Database(db_path=p)


def test_first_reserves_replay_rejected():
    db = _db()
    assert db.reserve_nonce("n1") is True
    assert db.reserve_nonce("n1") is False          # replay
    assert db.is_nonce_seen("n1") is True            # the old read API agrees it is now seen


def test_empty_nonce_reserves_nothing():
    db = _db()
    assert db.reserve_nonce("") is True
    assert db.reserve_nonce(None) is True


def test_release_allows_a_genuine_retry():
    db = _db()
    assert db.reserve_nonce("r1") is True
    assert db.reserve_nonce("r1") is False
    db.release_nonce("r1")
    assert db.reserve_nonce("r1") is True            # released -> reservable again
    db.release_nonce("")                              # no-op, must not raise


def test_expired_reservation_is_reusable():
    db = _db()
    assert db.reserve_nonce("e1", expiry=-1) is True  # stored already-expired
    assert db.reserve_nonce("e1") is True             # expired -> not a replay, reservable


def test_concurrent_exactly_one_winner():
    db = _db()
    N = 16
    barrier = threading.Barrier(N)
    results, lock = [], threading.Lock()

    def worker():
        barrier.wait()                                # release all threads at once
        r = db.reserve_nonce("hot")
        with lock:
            results.append(r)

    ts = [threading.Thread(target=worker) for _ in range(N)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert results.count(True) == 1, (results.count(True), results)   # EXACTLY ONE wins
    assert results.count(False) == N - 1


def _main():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    p = 0
    for fn in fns:
        try:
            fn(); print("PASS", fn.__name__); p += 1
        except Exception:
            print("FAIL", fn.__name__); traceback.print_exc()
    print("AN1: %d/%d passed" % (p, len(fns)))
    return 0 if p == len(fns) else 1


if __name__ == "__main__":
    sys.exit(_main())
