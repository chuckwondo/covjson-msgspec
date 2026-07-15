# Validation

Decoding is permissive: msgspec guarantees a document is structurally valid and
correctly typed, but it does not reject a coverage for *conformance*, so a
slightly-nonconformant but repairable document still loads. Conformance checks are
opt-in and tiered, through `validate`.

```python
from covjson_msgspec import validate

issues = validate(cov)                    # cheap, O(1)-per-object structural checks
issues = validate(cov, check_values=True) # also the O(n) element-vs-dataType checks
```

`validate` returns a `list[Issue]`. Each [`Issue`](../reference/validation.md)
carries a [`Severity`](../reference/validation.md) (an error or a warning) and a
pointer to the offending member, so you can report or filter them:

```python
from covjson_msgspec import Severity

errors = [i for i in validate(cov) if i.severity is Severity.ERROR]
```

To treat any issue as fatal, decode-then-check in one step with `mode="raise"`,
which raises `CovJSONValidationError` instead of returning:

```python
validate(cov, mode="raise")               # raises on the first error
```

The two tiers exist because they cost differently: the default pass is cheap and
structural, while `check_values=True` scans every element (the values against the
declared `dataType`, the `shape` against `axisNames`). Run the cheap pass freely;
reach for the value pass when you need it. See
[ADR-0002](../adr/0002-opt-in-tiered-validation.md) for why these checks live in
`validate` rather than at decode.
