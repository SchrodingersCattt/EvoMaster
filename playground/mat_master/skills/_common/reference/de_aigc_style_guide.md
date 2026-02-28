# De-AIGC Writing Style Guide

**Purpose**: Remove AI-sounding language while keeping precision, credibility, and readability.

---

## 1. Core Principles

1. **Lead with the real problem, not broad context.**
2. **Prefer concrete verbs over abstract labels.**
3. **Calibrate claims to evidence.** Match confidence level to what is directly shown.
4. **Use neutral, testable language.** Avoid emotional, promotional, or defensive tone.
5. **Keep one main point per sentence.** Split overloaded sentences.
6. **State contribution type explicitly.** Method / metric / dataset / tool / mechanism.
7. **Separate observations from extrapolation.** Report what is shown; label what is speculative.
8. **Remove redundancy.** Define key terms once, reuse consistently.
9. **Keep terminology stable end-to-end.** One concept, one preferred name.
10. **End with bounded significance.** Scoped usefulness, not universal impact.

---

## 2. AI-Sounding Patterns → Better Rewrites

| AI-Sounding | Better |
|---|---|
| `workflow`, `framework`, `paradigm`, `strategy` | specific operation: `integrate A with B`, `measure X` |
| `groundbreaking`, `unprecedented`, `pioneering` | evidence-linked: `improves X by Y under Z` |
| `the only viable`, `cannot`, `eliminates`, `proves` | calibrated: `the most practical under these conditions`, `limits`, `supports` |
| `universal`, `predictive`, `paves the way for` | scoped: `within this setting`, `for this class of systems` |
| stacked adjectives: `robust`, `critical`, `remarkable` | single qualifier with evidence, or none |
| filler verbs: `highlighting`, `showcasing`, `demonstrating` | direct: `confirm`, `show`, `indicate` — or drop |
| intensifiers: `just`, `merely`, `simply`, `dramatically` | quantitative statement, no intensifier |
| literary verbs: `fosters`, `unleashes`, `underpins` | plain: `establishes`, `reduces`, `constrains` |
| loose metaphor: `atomic-scale energy sink` | mechanism statement: `provides additional vibrational degrees of freedom that disperse frictional energy` |
| vague statistics: `mean-average`, `average displacement` | named statistic: `MAD (mean absolute deviation)`, `RMSD` |
| `"This limitation is not coincidental"` | `"This limitation is intrinsic to [system]"` |
| `"is directly computable from X"` when X is what you ran | delete — trivially true |
| long noun chains | verb-centered sentence |
| repeated framing lines across paragraphs | single definition, concise reuse |

---

## 3. High-Risk Sentence Openers

Flag and rewrite on sight:

| Opener | Problem | Fix |
|---|---|---|
| `Notably, ...` | inflates routine observation | delete; let data speak |
| `Significantly, ...` | asserts without proving | delete or state what specifically is significant |
| `In stark/sharp contrast, ...` | overstates even moderate contrast | use numbers; contrast carries itself |
| `It is worth noting that ...` | editorial padding | delete |
| `These results suggest/indicate that ...` | overused opener | start with subject + verb instead |
| `In summary, ...` (outside Conclusion) | premature summary | move to Conclusion or delete |
| `To this end, ...` | misused as "therefore" | use explicit causal connector |
| `In this context, ...` | redundant; context just established | delete |
| `Importantly, ...` | empty emphasis | delete |
| `This work paves the way for ...` | speculative flourish | state the specific next step or delete |
| `It is well known that ...` | assumes consensus; lazy citation | cite the primary source directly |
| `This section reviews ...` | announces instead of delivers | delete; start with substantive content |
| `It should be noted that ...` | editorial padding | delete |

---

## 4. Revision Passes (use as a checklist, not a mandatory sequence — skip passes with no findings)

### Pass 1 — Claim Calibration
- Mark: `prove`, `establish`, `enable`, `predict`, `the only`.
- Downgrade anything not directly supported by shown evidence.

### Pass 2 — Specificity Upgrade
- Replace abstract nouns with measurable objects and actions.
- Add boundary conditions (system, regime, time window).
- Replace vague statistics with named ones (MAD, RMSD, STD).

### Pass 3 — Sentence Compression
- Remove filler clauses and repeated qualifiers.
- Delete any sentence that only restates the previous one.

### Pass 4 — Redundancy Removal
- Delete duplicate thesis statements.
- One location per core definition.

### Pass 5 — Tone Scan
- Search and evaluate: `robust`, `critical`, `remarkable`, `notably`, `significantly`, `highlighting`, `showcasing`, `fosters`, `unleashes`, `paves the way`.
- Keep only if directly supported; otherwise delete or replace.

---

## 5. Rewrite Frames

- `This work addresses [practical constraint] in [context].`
- `We combine [method A] and [method B] to [explicit objective].`
- `Under [condition], [metric] changes from [baseline] to [result].`
- `[Observable] indicates [mechanistic step], not [alternative], because [discriminating evidence].`
- `These results provide [specific utility] for [bounded scope].`
- `This interpretation holds under [assumptions] and may not extend beyond [boundary].`

---

## 6. One-Line North Star

**Do not maximize excitement; maximize precision, evidential fit, and scoped usefulness.**

---

## Profile-specific notes

- **patent**: De-AIGC applies to the narrative sections (Background Art, Detailed Description). Do NOT apply to Claims — patent claim language is intentionally formal and repetitive by convention.
- **grant**: Standard De-AIGC rules apply, but note that certain terms (`transformative`, `innovative`) are conventional in grant rhetoric and may be acceptable when the funder's guidelines use them.
