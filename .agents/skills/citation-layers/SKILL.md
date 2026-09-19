---
name: citation-layers
description: Rules for thesis-tracker citation and claim-support validation — Layer A source grounding, Layer B claim support, evidence-only and insufficient-evidence fallback, unsupported leakage. Use for any change that could affect how generated claims are linked to SEC source text, including QA generation, evidence extraction, citation validation, support scoring, and retrieval changes evaluated against citation metrics. Use it before relaxing any grounding check.
---

# Citation Layers

Two separate questions, never merged:

- **Layer A — grounding**: does the cited text actually occur in the cited chunk?
- **Layer B — support**: does that text support the claim?

A citation can be perfectly grounded and still fail to support its claim.
Vector similarity answers neither question.

## 1. Layer A is exact match after normalization

The model returns `claim`, `evidence_chunk_id`, `evidence_text`, copied from
the chunk rather than composed. Python normalizes (NFKC, NBSP, soft hyphen,
zero-width, quote/dash variants, whitespace) and requires an exact match.

No fuzzy grounding. A 0.90 similarity threshold was rejected: if normalized
text still does not match, the model rewrote it. Normalization removes
representational noise; it does not license paraphrase.

## 2. The chunk must be the right chunk

Evidence that exists elsewhere in the filing but not in the cited chunk fails
Layer A. This is what preserves provenance.

## 3. Layer B is three-state

`supported` / `partial` / `unsupported`. Do not collapse uncertainty into
`supported`. Layer B evaluates entailment only — it does not re-check
grounding.

Target: unsupported leakage = 0. A narrower answer beats an unsupported one.

## 4. Fallback ladder

```
≥1 supported claim            → normal answer
0 supported, evidence exists  → evidence_only
no usable evidence            → insufficient_evidence
```

`evidence_only` is not normal success. Keep it distinguishable in metrics;
do not fold it into the answered count unless an eval spec says so.

## 5. Attribute the failure to the right layer

retrieval / generation / Layer A / Layer B are different failures. If the
evidence was never retrieved, that is a retrieval failure, not a citation
one. Preserving this distinction is what makes the next round debuggable.

## Removed mechanisms stay removed

The route-consensus gate was tested and showed no discriminative power. Do
not reintroduce it, or any new gate, without regression evidence. A mechanism
that sounds architecturally elegant but moves no metric gets deleted.

## Model provenance

Record requested model, returned model, fingerprint, prompt/template version,
prompt hash, and the raw response or an audit representation. Model alias
drift has been observed — a request for one DeepSeek alias returned another
serving model — so citation results must stay traceable to the model that
actually ran.

## Test expectations

Layer A:
```
exact copied text                  → PASS
whitespace/format-only difference  → PASS
one-character semantic change      → REJECT
fabricated sentence                → REJECT
real sentence, wrong chunk         → REJECT
```

Layer B: directly supported factual claim; partially supported
interpretation; unsupported inference; topically related but non-entailing
evidence; a numeric claim whose evidence does not establish the number.

Do not relax these to accommodate generation behaviour.

## Interaction with retrieval

Changed chunk delivery means rerunning citation and support regressions.
Higher Recall@K does not imply better answers, and a small ranking change
must not introduce unsupported leakage.

Expanded parent/sibling context may help generation, but the citation still
points at the chunk containing the quoted evidence.

## Completion

Better-looking answer text is not completion. Check retrieval regression,
Layer A grounding, Layer B support, unsupported leakage, and both fallback
states — inspecting individual failed cases, not only aggregate scores.

Report in Chinese: 改了什么、影响哪一层、regression 结果、grounding 是否变化、
unsupported leakage 是否仍为 0、fallback 行为是否符合规则、剩余失败案例及所属层。
