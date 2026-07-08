# ADR-0010: Dependency floor policy: deliberate, wheeled, tested both ends

## Status

Accepted

## Context

For a library, the lower bounds in `pyproject.toml` are a compatibility
contract: they declare the oldest dependency versions a user may bring. Until
now this project's floors were none of the three things a contract should be.

- **Arbitrary.** Some were deliberate (`xarray>=2024.10` for `DataTree`,
  `fastapi>=0.110` for the titiler coupling); others were set once and never
  revisited (`msgspec>=0.18`, `geopandas>=0.14`) with no recorded reason.
- **Untested.** CI only ever resolved the *latest* versions, so every floor was
  an unverified claim. Issue #14 added new msgspec usage
  (`json.schema_components(ref_template=...)`) and nobody actually knew whether
  the declared `msgspec>=0.18` still held.
- **Silently auto-raised.** Dependabot bumped floors on each upstream release
  (PR #59 tried `geopandas>=0.14` to `>=1.1.4`), narrowing user compatibility
  for a version the library uses nothing from.

A one-off audit would fix the numbers but not the process: without a stated
principle and a test, the floors would drift back out of true on the next
dependency change.

## Decision

Adopt a three-part policy, and record it here so future changes have a rule to
follow rather than a set of numbers to copy.

**1. Floor-selection principle.** Each runtime floor is the *lowest* version
that both (a) provides every API the library actually uses and (b) ships a wheel
for the Python floor (currently 3.11, per ADR-0001), so a user on the minimum
Python never has to build from source. A floor rises above that lowest point
only by a deliberate, recorded decision, not by default.

Applying (b) is not optional bookkeeping: it moved a floor. `cftime>=1.6` looked
fine and passed locally, but cftime is Cython-compiled and its first release
with a `cp311` wheel is 1.6.2 (1.6.0 and 1.6.1 have none); the local pass came
only from this machine building the sdist. The floor is now `cftime>=1.6.2`.

Two structural facts shape the rest of the audit:

- A dependency that a *sibling* extra constrains upward cannot be lowered in
  isolation and cannot be verified below that constraint by the all-extras test
  job. `numpy` and `pandas` are pinned up by `xarray` regardless of their own
  declarations, so their floors record the lowest version with a `cp311` wheel
  and are not chased lower for a reach the combined resolution never delivers.
- "Lowest we support" is a judgment for a fast-moving, pre-1.0 core dependency,
  not a mechanical minimum. `msgspec` stays at `>=0.18` deliberately: the APIs
  the library uses (`json.format`, `schema_components(ref_template=)`,
  `structs.replace`) exist since 0.16, but 0.18 is a recent, tested baseline and
  the reach below it is negligible. The floor is now deliberate, not inherited.

**2. Test both ends.** The `test` job in CI carries a `resolution` axis crossed
with the full Python matrix: `highest` (the lockfile) and `lowest-direct` (uv
resolves direct dependencies to their declared floors and keeps transitive
dependencies modern). All extras are synced, so every runtime floor is exercised
on every supported Python. The `lowest-direct` leg is **blocking**: a red run
means a declared floor is too low for the code as written and must rise. This is
what converts each floor from a claim into a verified fact.

**3. Dependabot stance.** The `uv` ecosystem is set to
`versioning-strategy: lockfile-only` (landed first, standalone, in PR #66),
so Dependabot refreshes `uv.lock` (dev and CI) but never rewrites the runtime
lower bounds. Floors rise only by the deliberate decision principle 1 describes.

## Alternatives considered

- **A one-off audit with no test and no ADR.** Fixes the current numbers but not
  the process; the floors drift back to untested and arbitrary on the next
  dependency change. Rejected: the durable problem is the absence of a principle
  and a verification, not the specific stale numbers.
- **Test only the `highest` resolution (the status quo).** Cheapest, but it is
  exactly the gap that made every floor an unverified claim. Rejected.
- **`--resolution lowest` (floor *everything*, transitive included) instead of
  `lowest-direct`.** Pins transitive dependencies to their own ancient floors
  too, producing failures in third-party interactions the library does not own
  and cannot fix by adjusting its own declarations. Rejected: `lowest-direct`
  tests the contract the library actually makes (its direct floors) while
  letting transitive dependencies resolve modern.
- **Per-extra `lowest-direct` lanes** (resolve `[pandas]` alone, `[geo]` alone,
  ...) so a sibling extra cannot pin a floor up during verification. This would
  let `numpy` / `pandas` floors be lowered and still verified. Rejected as
  disproportionate: it multiplies CI jobs for reach that the common, all-extras
  install never delivers. The full cross with all extras synced is the scope.
- **Keep letting Dependabot raise floors and just review the PRs.** Every such
  PR narrows compatibility by default and must be argued down one at a time;
  `lockfile-only` inverts the default so a floor moves only when we mean it to.
  Rejected.

## Consequences

- Every runtime floor now carries a one-line rationale in `pyproject.toml` and
  is verified green at `lowest-direct` across Python 3.11 to 3.14. `cftime` rose
  to `1.6.2` (wheel availability); every other floor held, now with a recorded
  reason.
- The test matrix roughly doubles the `test` legs (Python matrix times two
  resolutions). The `lowest-direct` leg re-resolves each run (it cannot use
  `--locked`, since changing the resolution mode makes uv ignore the lockfile),
  so it is slightly slower than the cached `highest` leg. Acceptable for the
  guarantee it buys. If CI cost bites, the middle Pythons' `lowest-direct` legs
  are the first trim.
- **The accepted cost of `lowest-direct`:** it holds direct dependencies at
  their old floors while resolving transitive dependencies modern, so a newly
  published transitive release can occasionally clash with an old direct floor
  and turn the blocking leg red through no code change of ours. The resolution
  is always the same and always deliberate: raise the offending floor. This is a
  feature (the job surfaces a real incompatibility) priced as an occasional
  maintenance nudge, not a reason to weaken the job.
- `versioning-strategy: lockfile-only` leaves transitive lock entries stale over
  time (dependabot-core#14073); a periodic `uv lock --upgrade` covers it if the
  lockfile's transitive freshness ever matters.
- Out of scope, unchanged: dev-tooling floors in `[dependency-groups]` (not a
  user contract) and the Python floor itself (`>=3.11`, gated on titiler per
  ADR-0001). No upper bounds are introduced; libraries avoid artificial
  ceilings.
