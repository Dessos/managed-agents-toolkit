---
type: evaluation
status: active
created: {{CREATED}}
updated: {{UPDATED}}
tags: [evaluation, experiment]
related: []
confidence: high
source: "{{SOURCE}}"
# result: positive | negative | inconclusive
---

# {{TITLE}}

## Hypothesis

What we expected to be true before the experiment. Phrased as a falsifiable
claim. ("Using `mcp_tool` hooks reduces Python overhead by >50% vs CLI
shimming.")

## Parameters

What was varied (the independent variable) and what was held constant
(controls). Include exact versions / config / inputs so the result is
reproducible.

## Results

Table or bullet list of the measurements. Numbers, not impressions.

| Variant | Metric A | Metric B | Notes |
|---|---|---|---|
|  |  |  |  |

## Interpretation

What the data actually says. Distinguish what was measured from what we
*infer* from the measurements. Call out alternative explanations.

## What to do

Actionable consequences. If the hypothesis won → adopt where. If it lost →
what alternative gets the next try. If inconclusive → what would
disambiguate.

## Limitations

Confounders, small sample sizes, anything that limits the result's
generalization.

## Related

- [[hypothesis-source-adr]]
- `path/to/benchmark.py`
- raw data location
