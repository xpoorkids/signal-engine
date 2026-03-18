"""
Execution-quality hook for future edge and sizing logic.

Purpose
-------
- Defines the interface that `worker.promote` uses to ask for execution edge
  and safe size estimates before adding execution-related confidence bonuses.
- In the current production code this module is intentionally inactive: it
  always returns zero edge and zero size cap.

Runtime data flow
-----------------
Inputs:
- Current `Event`
- `EngineState`

Transformations:
- None today. `estimate_edge()` is a stub and does not inspect the event or
  state.

Outputs:
- `edge_bps = 0.0`
- `reasons = []`
- `size_cap_usd = 0.0`

Key logic
---------
- There is no live execution model yet.
- `worker.promote` still calls this function when `ENABLE_EXECUTION` is on, but
  the returned values do not currently influence routing in a meaningful way.

Failure modes
-------------
- The main risk here is operator assumption: engineers may believe execution
  quality is being modeled when it is not.
- Because the function is deterministic and side-effect free, it does not fail
  loudly or surface missing execution data.

Logging and observability
-------------------------
- Any execution logs you see downstream in `worker.promote` reflect this stub
  output unless this module is later implemented.

Gotchas
-------
- Treat execution support as unimplemented in current runtime behavior.
- If routing behavior appears to depend on execution quality, verify the caller;
  this module itself contributes no positive or negative edge today.
"""

from typing import Tuple, List


def estimate_edge(e, state) -> Tuple[float, List[str], float]:
    """
    Return execution edge and size limits for downstream scoring.

    Current behavior: no-op placeholder returning zero edge and zero size cap.
    """
    # Phase 1 placeholder: execution logic disabled
    edge_bps = 0.0
    reasons: List[str] = []
    size_cap_usd = 0.0

    # stub for future execution logic
    return edge_bps, reasons, size_cap_usd
