#!/usr/bin/env python
"""IPC subsystem benchmark.

Measures four aspects of the in-process IPC / event-bus / rate-limiter
stack so the CI ratchet can catch regressions in the Electron↔Python
control plane:

1. **Auth handshake latency** (cold + warm) — the cost of validating a
   representative ``{type, id, data}`` IPC payload against the
   declarative schema.  Every IPC command goes through this gate; a
   regression here is a regression on every command.

2. **push() throughput under N concurrent subscribers** — measures
   ``event_bus.publish`` fan-out cost as the subscriber count grows.
   The production server has 1-3 subscribers (TCP transport, logging,
   diagnostics); we test up to 64 to expose O(N) scaling.

3. **End-to-end latency for a streaming partial round-trip** — measures
   the publish→subscriber callback wall-clock for the
   ``transcription_final`` event.  This is the "how long after ASR
   finishes does the renderer see the text" metric.

4. **Rate-limiter throughput at saturation** — measures
   ``_RateLimiter.allow`` ops/sec at the burst ceiling (200/s).  A
   regression here means a flood attack can starve the dispatcher.

The bench is deterministic (seeded RNG, fixed subscriber counts, no
network I/O) so two runs on the same machine produce identical
distributions modulo OS scheduler noise.

Usage:
    python bench/bench_ipc.py
    python bench/bench_ipc.py --json
    python bench/bench_ipc.py --subscribers 128
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_SUBSCRIBERS = (1, 4, 16, 64)
DEFAULT_HANDSHAKE_ITERS = 2000
DEFAULT_PUSH_ITERS = 5000
DEFAULT_ROUND_TRIP_ITERS = 1000
DEFAULT_RATE_LIMIT_ITERS = 100_000


def _percentile(sorted_vals: list[float], pct: float) -> float:
    """Percentile helper. ``pct`` in [0, 100]."""
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, int(len(sorted_vals) * pct / 100.0))
    return sorted_vals[idx]


def _stats(latencies_us: list[float]) -> dict[str, float]:
    """Reduce a list of per-op latencies (microseconds) to a stats dict."""
    if not latencies_us:
        return {"n": 0, "p50_us": 0.0, "p99_us": 0.0, "max_us": 0.0, "mean_us": 0.0}
    sorted_vals = sorted(latencies_us)
    return {
        "n": len(sorted_vals),
        "p50_us": round(_percentile(sorted_vals, 50.0), 2),
        "p99_us": round(_percentile(sorted_vals, 99.0), 2),
        "max_us": round(sorted_vals[-1], 2),
        "mean_us": round(statistics.mean(sorted_vals), 2),
    }


def bench_auth_handshake(iterations: int) -> dict:
    """Measure the cost of validating a representative IPC payload.

    The schema mirrors the ``set_config`` command shape (a dict with a
    few typed fields).  Each iteration calls
    ``_validate_dict_payload`` once on a fresh dict copy — the
    validation is pure-Python (no I/O), so this is the steady-state
    per-command overhead.
    """
    from voice_typer.server.ipc.validation import _validate_dict_payload

    schema = {
        "type": {"type": str, "required": True, "max_value_len": 64},
        "id": {"type": str, "required": True, "max_value_len": 64},
        "data": {"type": dict, "required": True},
    }

    # Cold run: first validation triggers any lazy imports inside the
    # validation module.  Warm runs measure steady-state.
    payload = {"type": "set_config", "id": "abc-123", "data": {"key": "value"}}

    cold_t0 = time.perf_counter_ns()
    _validate_dict_payload(payload, schema)
    cold_us = (time.perf_counter_ns() - cold_t0) / 1000.0

    warm_latencies: list[float] = []
    for _ in range(iterations):
        t0 = time.perf_counter_ns()
        _validate_dict_payload(payload, schema)
        warm_latencies.append((time.perf_counter_ns() - t0) / 1000.0)

    return {
        "name": "auth_handshake",
        "iterations": iterations,
        "cold_us": round(cold_us, 2),
        "warm": _stats(warm_latencies),
    }


def bench_push_throughput(subscriber_counts: tuple[int, ...], iterations: int) -> dict:
    """Measure ``event_bus.publish`` throughput as subscriber count grows.

    For each N in ``subscriber_counts``, we register N no-op subscriber
    callbacks, publish ``iterations`` events, and record per-publish
    latency.  The callbacks are no-ops (``def cb(_): pass``) so the
    measurement isolates the bus's fan-out cost, not the subscriber's
    work.

    The event is the ``transcription_final`` shape (small dict) so the
    serialization cost is realistic.
    """
    from voice_typer.server import event_bus

    event = {"type": "transcription_final", "data": {"text": "hello world"}}
    results_per_n: list[dict] = []
    for n in subscriber_counts:
        # Register N unique no-op callbacks.  Use a list comprehension
        # (not lambdas) so each callback is a distinct object — set
        # semantics would dedupe lambdas with the same body.
        callbacks = [_make_noop_subscriber(i) for i in range(n)]
        for cb in callbacks:
            event_bus.subscribe(cb)
        try:
            # Warm-up: prime any lazy structures in the bus.
            for _ in range(min(50, iterations)):
                event_bus.publish(event)
            latencies: list[float] = []
            for _ in range(iterations):
                t0 = time.perf_counter_ns()
                event_bus.publish(event)
                latencies.append((time.perf_counter_ns() - t0) / 1000.0)
        finally:
            for cb in callbacks:
                event_bus.unsubscribe(cb)
        results_per_n.append(
            {
                "subscribers": n,
                "iterations": iterations,
                "publish_latency": _stats(latencies),
                "throughput_ops_per_s": round(
                    iterations / (sum(latencies) / 1e6) if latencies else 0.0, 1
                ),
            }
        )
    return {
        "name": "push_throughput",
        "subscriber_counts": list(subscriber_counts),
        "results_per_n": results_per_n,
    }


def _make_noop_subscriber(idx: int):
    """Return a unique no-op subscriber callable.

    Each returned function is a distinct closure (captures a unique
    ``idx``) so ``event_bus.subscribe`` stores them as N separate
    entries in its set — without this, ``lambda _: None`` would dedupe
    to a single entry (Python's set uses ``id()`` for callables, but
    each ``lambda`` literal creates a new object, so this is mostly
    defensive).
    """

    def cb(_msg: dict) -> None:
        # Touch ``idx`` so the closure captures it (otherwise Python
        # may elide the closure cell).
        _ = idx

    return cb


def bench_streaming_round_trip(iterations: int) -> dict:
    """Measure end-to-end latency for a streaming partial round-trip.

    Simulates: the transcription thread calls
    ``event_bus.publish({"type": "transcription_final", ...})``; the
    IPC transport (subscribed) receives the callback.  We measure the
    wall-clock from ``publish`` return to the subscriber being invoked.

    In production this includes the TCP ``sendall`` to the renderer
    (microseconds on localhost); here we measure the in-process portion
    only (no socket).  The bus's synchronous path calls subscribers in
    the publisher's thread, so this is effectively the call-overhead
    measurement.
    """
    from voice_typer.server import event_bus

    event = {"type": "transcription_final", "data": {"text": "partial text"}}
    latencies: list[float] = []
    barrier = threading.Barrier(2)  # publisher + subscriber

    def subscriber(_msg: dict) -> None:
        # Record the latency from publish time to callback time.
        # ``publish_t0`` is set by the publisher just before the
        # ``publish()`` call.  The synchronous bus calls us in the
        # publisher's thread, so this is a tight loop.
        latencies.append((time.perf_counter_ns() - publish_t0[0]) / 1000.0)

    publish_t0: list[int] = [0]
    event_bus.subscribe(subscriber)
    try:
        # Warm-up.
        for _ in range(min(50, iterations)):
            publish_t0[0] = time.perf_counter_ns()
            event_bus.publish(event)
        latencies.clear()
        for _ in range(iterations):
            publish_t0[0] = time.perf_counter_ns()
            event_bus.publish(event)
    finally:
        event_bus.unsubscribe(subscriber)
    return {
        "name": "streaming_round_trip",
        "iterations": iterations,
        "latency": _stats(latencies),
    }


def bench_rate_limiter_saturation(iterations: int) -> dict:
    """Measure ``_RateLimiter.allow`` throughput at saturation.

    The limiter is configured with the production defaults (burst=200/s,
    sustained=600/10s).  We send ``iterations`` commands with cost=1
    (the ``heartbeat`` cost, which bypasses the limit) and measure the
    per-call overhead.

    A separate measurement uses ``set_config`` (cost=2) to exercise the
    cost-weighted path.  Both must stay well under 100 µs/call to keep
    the dispatcher from being CPU-bound on rate-limit bookkeeping.
    """
    from voice_typer.server.ipc.rate_limiter import _RateLimiter

    limiter = _RateLimiter()
    # Heartbeat bypasses the limit entirely — measures pure call overhead.
    hb_latencies: list[float] = []
    for _ in range(iterations):
        t0 = time.perf_counter_ns()
        limiter.allow(command="heartbeat")
        hb_latencies.append((time.perf_counter_ns() - t0) / 1000.0)

    # ``set_config`` exercises the full deque + cost-weighted path.
    # We can only send 200 before the burst rejects — so reset the
    # limiter every 100 calls to stay under the cap.
    sc_latencies: list[float] = []
    batch = 100
    sent_in_batch = 0
    for _ in range(min(iterations, 10_000)):
        if sent_in_batch >= batch:
            limiter = _RateLimiter()
            sent_in_batch = 0
        t0 = time.perf_counter_ns()
        allowed = limiter.allow(command="set_config")
        sc_latencies.append((time.perf_counter_ns() - t0) / 1000.0)
        if allowed:
            sent_in_batch += 1

    return {
        "name": "rate_limiter_saturation",
        "iterations_heartbeat": iterations,
        "iterations_set_config": len(sc_latencies),
        "heartbeat_allow": _stats(hb_latencies),
        "set_config_allow": _stats(sc_latencies),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="IPC subsystem benchmark")
    parser.add_argument(
        "--subscribers",
        type=str,
        default=",".join(str(s) for s in DEFAULT_SUBSCRIBERS),
        help="Comma-separated subscriber counts for the push throughput bench.",
    )
    parser.add_argument(
        "--handshake-iters",
        type=int,
        default=DEFAULT_HANDSHAKE_ITERS,
        help=f"Iterations for the auth handshake bench (default: {DEFAULT_HANDSHAKE_ITERS}).",
    )
    parser.add_argument(
        "--push-iters",
        type=int,
        default=DEFAULT_PUSH_ITERS,
        help=f"Iterations per N for the push throughput bench (default: {DEFAULT_PUSH_ITERS}).",
    )
    parser.add_argument(
        "--round-trip-iters",
        type=int,
        default=DEFAULT_ROUND_TRIP_ITERS,
        help=f"Iterations for the streaming round-trip bench (default: {DEFAULT_ROUND_TRIP_ITERS}).",
    )
    parser.add_argument(
        "--rate-limit-iters",
        type=int,
        default=DEFAULT_RATE_LIMIT_ITERS,
        help=f"Iterations for the rate-limiter bench (default: {DEFAULT_RATE_LIMIT_ITERS}).",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable")
    args = parser.parse_args()

    subscriber_counts = tuple(int(s) for s in args.subscribers.split(",") if s.strip())

    results = {
        "auth_handshake": bench_auth_handshake(args.handshake_iters),
        "push_throughput": bench_push_throughput(subscriber_counts, args.push_iters),
        "streaming_round_trip": bench_streaming_round_trip(args.round_trip_iters),
        "rate_limiter_saturation": bench_rate_limiter_saturation(args.rate_limit_iters),
    }

    if args.json:
        json.dump(results, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    print("=" * 72)
    print("Voice Typer — IPC Subsystem Benchmark")
    print("=" * 72)

    hs = results["auth_handshake"]
    print(f"\n## Auth Handshake ({hs['iterations']} iters)")
    print(f"  Cold (first call)  : {hs['cold_us']:.2f} µs")
    print(f"  Warm p50 / p99 / max: {hs['warm']['p50_us']} / {hs['warm']['p99_us']} / {hs['warm']['max_us']} µs")

    pt = results["push_throughput"]
    print(f"\n## Push Throughput ({pt['iterations'] if 'iterations' in pt else ''} iters per N)")
    print(f"  {'subscribers':<12} {'p50 (µs)':<12} {'p99 (µs)':<12} {'max (µs)':<12} {'ops/s':<12}")
    for r in pt["results_per_n"]:
        s = r["publish_latency"]
        print(
            f"  {r['subscribers']:<12} {s['p50_us']:<12} {s['p99_us']:<12} {s['max_us']:<12} {r['throughput_ops_per_s']:<12}"
        )

    rt = results["streaming_round_trip"]
    print(f"\n## Streaming Round-Trip ({rt['iterations']} iters)")
    print(f"  p50 / p99 / max: {rt['latency']['p50_us']} / {rt['latency']['p99_us']} / {rt['latency']['max_us']} µs")

    rl = results["rate_limiter_saturation"]
    print(f"\n## Rate Limiter Saturation")
    print(f"  heartbeat (bypass)  : p50={rl['heartbeat_allow']['p50_us']} p99={rl['heartbeat_allow']['p99_us']} µs")
    print(f"  set_config (cost=2) : p50={rl['set_config_allow']['p50_us']} p99={rl['set_config_allow']['p99_us']} µs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
