# Session Busy Policies

## Public Contract

`TurnRequest.busy` is a validated `BusyPolicy`. Existing requests default to
`APPEND`; existing CLI and channel behavior is unchanged.

```python
from repoagent.spine import BusyPolicy, TurnRequest

request = TurnRequest.create(
    session_id="coding-session",
    text="Also check the error path",
    busy=BusyPolicy.INJECT,
)
handle = scheduler.submit(request)
outcome = await handle.result()
```

The scheduler and its mailbox belong to one asyncio event loop. The synchronous
Agent worker reaches the mailbox through `run_coroutine_threadsafe`, never by
mutating it from the worker thread. No new CLI switch or channel wire-format
option is introduced by this change.

## Scheduling Semantics

| Policy | Busy session | Idle session |
| --- | --- | --- |
| APPEND | Queue at tail | Execute normally |
| INJECT | Queue in the active Turn's mailbox | Execute normally |
| INTERRUPT | Signal current Turn cancellation; enqueue at front | Execute normally |

The worker remains the terminal-future owner. Interrupt does not directly
resolve the current handle or run the next request concurrently with it.
Foreground/background pools remain isolated, and policies never target another
session's active Turn.

The runner consumes pending injections via `drain()`. Consumed entries leave
the mailbox and become owned by the host Turn. Unconsumed entries fall back to
the ordinary queue tail, preserving their original handles. Cancelling an
unconsumed injection removes only that entry. Cancelling an already merged
handle does not cancel its host.

Shutdown seals admission, cancels pending queue and mailbox entries, waits for
the active Turn according to its grace period, and settles merged outcomes.
Late drain calls from a cancelled/finished worker cannot consume another Turn's
mailbox. An executor without `complete_injected` is rejected before an INJECT
request is admitted; append-only custom executors still work at runtime.

## Agent Integration

`AgentTurnRunner` passes a drain bridge into `AgentLoop.run`. The loop consumes
messages before subsequent model attempts, after preceding tool calls have
returned. It does not drain on the initial model attempt. If the host finishes
without another attempt, unconsumed injections fall back to separate Turns.

Injected user text is redacted and recorded once in conversation history. For
structured providers it is appended as a real user message; text-only providers
see it through the normal history assembly. Successfully processed injections
are included in the memory backend's final store call with their own Turn IDs
and the host Turn ID. They are not a new provider/tool execution on their own.

## Durable Outcomes

`TurnRuntime.complete_injected` requires an accepted request in the same session
and a distinct, terminal host outcome. It records:

1. `turn.accepted`, already persisted before returning the injection handle.
2. `turn.merged`, linking `host_turn_id` and entering the running lifecycle state.
3. `turn.completed`, `turn.failed` or `turn.cancelled`, following the host outcome.

The child outcome keeps its own request/Turn identity. Usage is zero, no explicit
reply is emitted, and the final answer is empty. This prevents shared host work
from being billed or delivered twice. If persisting a child completion fails,
its handle raises that error rather than remaining unresolved; the host and
other queue entries can still settle. Normal restart recovery can detect the
remaining incomplete durable child record.

## Verification and Limits

- Fourteen new tests cover merge completion/failure/cancellation, fallback,
  interruption priority, pending cancellation, shutdown, idle policies, invalid
  policies, missing completion ownership, persistence failure, and real Agent
  structured/text prompt consumption.
- Full regression after the production edits: 1,432 passed, 52 skipped, six
  existing warnings. Five additional tests were subsequently added; the final
  focused run passed 41 tests. No production code changed between those runs.
- Original R0 workload and all original correctness predicates passed for both
  subjects: five repetitions, 10,000 requests each, zero loss/duplicates/unresolved
  handles/lifecycle contradictions/pool violations. This includes all five
  request fates, not merely ordinary FIFO delivery.
- Independent validation of retained RepoAgent event journals found 10,000
  accepted/terminal pairs, 80 merged requests, 9,520 completed and 480 cancelled.
- The adapter translates native events and outcomes; it does not implement busy
  scheduling. RepoAgent's real `TurnRuntime` and `RunStore` were used.
- The initial ordinary-disk adapter run timed out at the original 30-second
  bulk-phase bound. Repeating with unchanged `RunStore` on `/dev/shm` passed.
  The timeout was NOT raised and fsync was NOT disabled. This supports scheduler
  correctness under the retained tmpfs fixture, not disk persistence throughput
  or power-loss durability. Ordinary-disk throughput remains unresolved.

Raw local evidence: `artifacts/upstream-protocol-20260914/r0-comparison.json`,
`r0-native-events.tar.gz`, `r0-verification.json`, `r0-tmpfs.log` and
`regression-busy-policy.txt`. This does not complete live scheduling, cost,
tracing or evolution parity, and no model quality improvement is claimed.
