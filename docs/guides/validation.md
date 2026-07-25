# Validation

Decoding is permissive: msgspec guarantees a document is structurally valid and
correctly typed, but it does not reject a coverage for *conformance*, so a
slightly-nonconformant but repairable document still loads. Conformance checks are
opt-in and tiered, through `validate`.

```python
from covjson_msgspec import validate

report = validate(cov)                    # cheap, O(1)-per-object structural checks
report = validate(cov, check_values=True) # also the O(n) element-vs-dataType checks
```

`validate` returns a [`ValidationReport`](../reference/validation.md): the findings
bundled with the valid/invalid verdict. Ask `report.ok` for validity (it is
`True` when no error-severity issue was found), and `report.errors` /
`report.warnings` to split the findings by severity. Each
[`Issue`](../reference/validation.md) carries a
[`Severity`](../reference/validation.md) and a pointer to the offending member:

```python
if not report.ok:
    for issue in report.errors:
        print(issue.at, issue)
```

The report has *no* truth value: `if report:` is ambiguous between "has findings"
and "is valid" (opposite readings), so it raises. Ask `report.ok` for the verdict,
and reach the findings through `report.issues` (all), `report.errors`, or
`report.warnings`. The report is deliberately not iterable or sized: `for x in
report` and `len(report)` raise, so each call site names the view it means.

To treat any error as fatal, decode-then-check in one step with `mode="raise"`,
which raises `CovJSONValidationError` (carrying every error) instead of returning:

```python
validate(cov, mode="raise")               # raises if any error is found
```

The two tiers exist because they cost differently: the default pass is cheap and
structural, while `check_values=True` scans every element (the values against the
declared `dataType`, the `shape` against `axisNames`). Run the cheap pass freely;
reach for the value pass when you need it. See
[ADR-0002](../adr/0002-opt-in-tiered-validation.md) for why these checks live in
`validate` rather than at decode.
