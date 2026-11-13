"""C1 (2026-11-12): one trader cycle at a time. Two cycles that read the same state file used to
both pass the live gate and then drop each other's pending row. Driven both ways through the real
gate with the shared fixture, with ANOTHER PROCESS holding the lock where the case needs one.
Offline: stand-in venue, temporary config and state, no network."""
import inspect
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import guards as G  # noqa: E402
import covenant_trader as T  # noqa: E402
from gated_fixture import Gated, Venue, order  # noqa: E402

results = []


def check(label, ok, detail=""):
    results.append(bool(ok))
    print(f"{'ok  ' if ok else 'FAIL'}  {label}" + ("" if ok else f"  {str(detail)[:320]}"), flush=True)


def why(res, i=0):
    return res[i].get("blocked_by") or []


def raw(path):
    with open(path, "rb") as fh:
        return fh.read()


# Another process holding the lock with the operating system's own call -- it does not import the
# trader, so what stops the cycle below is the lock itself and not a variable in this process.
HOLDER = r'''
import os, sys
fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)
os.lseek(fd, 0, 0)
if os.name == "nt":
    import msvcrt
    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
else:
    import fcntl
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
print("held", flush=True)
sys.stdin.read()
'''


def hold(path):
    p = subprocess.Popen([sys.executable, "-B", "-c", HOLDER, path], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, text=True)
    said = p.stdout.readline().strip()
    if said != "held":
        p.kill()
        raise RuntimeError("the other process could not take the lock: %r" % said)
    return p


def end(p, kill=False):
    if kill:
        p.kill()                      # as a crash or a power-user's End Task would
    else:
        p.stdin.close()
    p.wait(timeout=30)
    p.stdout.close()


def other_cycle_asks(state_path):
    """What a second trader process is told when it asks for the cycle lock on this state file."""
    env = dict(os.environ, COVENANT_TRADER_STATE=state_path)
    code = "import guards as G; ok, w = G.take_cycle_lock(); print('TAKEN' if ok else 'REFUSED: ' + str(w))"
    out = subprocess.run([sys.executable, "-B", "-c", code], cwd=HERE, env=env, capture_output=True,
                         text=True, timeout=120)
    return (out.stdout.strip().splitlines() or [out.stderr.strip()[-200:]])[-1]


SELL = order("XLM", "sell", 20.0)

# ---- the pass case ---------------------------------------------------------------------------
with Gated(approved=[SELL], hold_lock=False) as fx:
    v = Venue()
    res = fx.execute([dict(SELL)], [v])
    check("L1 nobody else is running: execute() takes the lock itself and the order goes live",
          v.live() == [True], (v.live(), why(res)))
    check("L2 ...and gives it back when it returns", not G.holds_cycle_lock())

# ---- the finding: another cycle is running -----------------------------------------------------
with Gated(approved=[SELL], hold_lock=False) as fx:
    other = hold(G.cycle_lock_path())
    try:
        before = raw(fx.state_path)
        v = Venue()
        res = fx.execute([dict(SELL)], [v])
        check("L3 another process holds the cycle lock: nothing goes live, and the refusal names the lock",
              v.live() == [False] and any("cycle lock" in r for r in why(res)), (v.live(), why(res)))
        check("L4 ...and the state file was not touched (no pending row written over the other cycle's)",
              raw(fx.state_path) == before)
        out = T.run_once(dict(fx.cfg))
        check("L5 run_once refuses the whole cycle before reading anything, and says why",
              out is None and isinstance(T.LAST_REFUSAL, str) and "cycle lock" in T.LAST_REFUSAL
              and raw(fx.state_path) == before, T.LAST_REFUSAL)
        try:
            T.save_state(dict(fx.st, sealed_signals=99))
            saved = "written"
        except T.StateBusy as e:
            saved = "StateBusy: %s" % e
        check("L6 save_state raises StateBusy instead of writing over a cycle in flight",
              saved.startswith("StateBusy") and raw(fx.state_path) == before, saved)
    finally:
        end(other, kill=True)
    # the holder was killed, not asked to let go: there is no stale lock to clear by hand
    v = Venue()
    res = fx.execute([dict(SELL)], [v])
    check("L7 the other process DIED holding the lock: the next cycle goes live, nothing to clean up",
          v.live() == [True], (v.live(), why(res)))

# ---- the gate and the permit, asked directly -----------------------------------------------------
with Gated(approved=[SELL], hold_lock=False) as fx:
    fx.seal([dict(SELL)])
    reasons, permit = G.live_gate(dict(SELL), Venue())
    check("L8 the gate asked by a process that does not hold the lock gives no permit and names the lock",
          permit is None and any("cycle lock" in r for r in reasons), reasons)
    ok, _w = G.take_cycle_lock()
    reasons, permit = G.live_gate(dict(SELL), Venue())
    check("L9 ...and holding it, the same question is permitted (the control)",
          ok and reasons == [] and isinstance(permit, str), reasons)
    G.release_cycle_lock()
    try:
        G.consume_permit(permit, "fake", "XLM", "sell")
        used = "consumed"
    except ValueError as e:
        used = "ValueError: %s" % e
    check("L10 a permit issued under the lock is not usable after the lock is given back",
          used.startswith("ValueError") and "cycle lock" in used, used)
    G.take_cycle_lock()
    check("L11 ...and is usable once, holding it again", G.consume_permit(permit, "fake", "XLM", "sell") == 20.0)
    G.release_cycle_lock()

# ---- this process is the cycle in flight; a second trader process asks ---------------------------
with Gated(approved=[SELL]) as fx:                      # hold_lock=True: as inside run_once
    said = other_cycle_asks(fx.state_path)
    check("L12 while this cycle holds the lock, a second trader process is refused by take_cycle_lock",
          said.startswith("REFUSED") and "another trader cycle" in said, said)
    path = fx.state_path
check("L13 ...and once this cycle has ended, the second process takes it",
      other_cycle_asks(path) == "TAKEN", other_cycle_asks(path))

# ---- the helper itself ---------------------------------------------------------------------------
with Gated(hold_lock=False) as fx:
    a, _ = G.take_cycle_lock()
    b, _ = G.take_cycle_lock()
    G.release_cycle_lock()
    still = G.holds_cycle_lock()
    G.release_cycle_lock()
    check("L14 the lock counts: taken twice, it is held until it has been given back twice",
          a and b and still and not G.holds_cycle_lock())
    G.release_cycle_lock()
    check("L15 giving back a lock that is not held does nothing", not G.holds_cycle_lock())
    check("L16 the lock file sits beside the state file the gate reads",
          os.path.dirname(G.cycle_lock_path()) == os.path.dirname(G.TRADER_STATE)
          and G.cycle_lock_path().startswith(G.TRADER_STATE))

for fn, word in ((T.run_once, "take_cycle_lock"), (T.execute, "take_cycle_lock"), (T.save_state, "take_cycle_lock"),
                 (G.live_gate, "holds_cycle_lock"), (G.consume_permit, "holds_cycle_lock")):
    check("L17 %s asks for the lock (pinned, so a rewrite cannot drop it quietly)" % fn.__name__,
          word in inspect.getsource(fn))

n = len(results)
print(f"\nC1 cycle lock: {sum(results)}/{n} passed")
sys.exit(0 if all(results) else 1)
