# Pattern Design Guide

This guide provides a systematic approach to designing and maintaining extraction patterns for pattern-based enrichment.

## Overview

Pattern-based enrichment relies on regular expressions and keyword matching to extract structured fields from semi-structured text (e.g., literature abstracts, claims, quotes).

**Goals:**
- Maximize precision (few false positives)
- Maintain recall (capture most relevant records)
- Keep patterns maintainable and documented
- Enable pattern reuse across similar domains

---

## Step 1: Pattern Taxonomy

Organize patterns by **target field** and **pattern type**:

```python
PATTERNS = {
    # Material identification
    'material': {
        'keywords': [...],          # Exact keyword matches
        'regex': [...],             # Regex patterns
        'abbreviations': {...},     # Abbr -> Full name mappings
    },
    
    # Property identification
    'property': {
        'keywords': [...],
        'regex': [...],
        'synonyms': {...},          # Alternative names for same property
    },
    
    # Value extraction (numbers + units)
    'value': {
        'number_pattern': r'...',   # Float/int pattern
        'unit_patterns': {...},     # Unit regex -> canonical unit
    },
    
    # Filtering (inclusion/exclusion)
    'filters': {
        'include': [...],           # Patterns that indicate "keep this row"
        'exclude': [...],           # Patterns that indicate "skip this row"
    },
}
```

---

## Step 2: Keyword Lists (Highest Precision)

**Best for:** Known, standardized terms (e.g., material names, property names)

### Material Keywords Example

```python
MATERIAL_KEYWORDS = [
    # Add your domain-specific material names
]

def match_material_keyword(text: str) -> str | None:
    text_lower = text.lower()
    for kw in MATERIAL_KEYWORDS:
        if kw.lower() in text_lower:
            return kw
    return None
```

### Property Keywords Example

```python
PROPERTY_KEYWORDS = {
    # Map: canonical name -> list of aliases/synonyms
}

def match_property_keyword(text: str) -> str | None:
    text_lower = text.lower()
    for prop, synonyms in PROPERTY_KEYWORDS.items():
        for syn in synonyms:
            if syn.lower() in text_lower:
                return prop
    return None
```

**Guidelines:**
- Use **exact canonical forms** (not variations)
- Order keywords by specificity (longer, more specific first)
- Document each keyword with a source/example
- Test against sample data before deployment

---

## Step 3: Regex Patterns (Higher Recall)

**Best for:** Variable formats, numeric extraction, contextual matching

### Material Name Extraction Example

```python
# Define your regex patterns for material name extraction
PAT_BRACKETED_FORMULA = re.compile(r'\[([^\]]+)\]', re.IGNORECASE)
PAT_CHEMICAL_FORMULA = re.compile(r'...', re.IGNORECASE)

def extract_material_name(text: str) -> str | None:
    # Try patterns in priority order
    for pat in [PAT_BRACKETED_FORMULA, PAT_CHEMICAL_FORMULA]:
        m = pat.search(text)
        if m:
            return m.group(1).strip()
    return None
```

### Property Value + Unit Extraction Example

```python
# Define patterns to extract numeric values and units
PAT_NUMERIC = re.compile(r'([\-+]?\d+\.?\d*(?:[eE][\-+]?\d+)?)')

# Map found units to canonical forms
UNIT_NORMALIZATION = {
    # 'found_unit': 'canonical_unit',
}

def extract_value_and_unit(text: str) -> tuple[str, str]:
    """Extract value and unit. Returns (value_str, unit_str) or ("", "")."""
    m = PAT_NUMERIC.search(text)
    if m:
        return m.group(1), ""  # Implement unit extraction based on your patterns
    return "", ""
```

**Guidelines:**
- Test regex against 10+ real examples before use
- Use raw strings (`r'...'`) to avoid escape issues
- Add `re.IGNORECASE` flag where case doesn't matter
- Document each pattern with example matches
- Beware of overlapping patterns; order matters

---

## Step 4: Filters (Inclusion/Exclusion)

**Purpose:** Determine which rows to keep after enrichment

### Inclusion Filters

```python
# Define patterns that indicate rows to keep
FILTERS_INCLUDE = [
    # re.compile(r'...', re.I),
]

def passes_inclusion_filter(text: str, filters: list) -> bool:
    """Return True if text matches at least one inclusion filter."""
    return any(f.search(text) for f in filters)
```

### Exclusion Filters

```python
# Define patterns that indicate rows to skip
FILTERS_EXCLUDE = [
    # re.compile(r'...', re.I),
]

def passes_exclusion_filter(text: str, filters: list) -> bool:
    """Return True if text does NOT match any exclusion filter."""
    return not any(f.search(text) for f in filters)
```

**Implementation:**

```python
def should_keep_row(quote_text: str, claim_text: str,
                    include_filters: list, exclude_filters: list) -> bool:
    combined = quote_text + ' ' + claim_text
    
    # Check inclusion
    if not passes_inclusion_filter(combined, include_filters):
        return False
    
    # Check exclusion
    if not passes_exclusion_filter(combined, exclude_filters):
        return False
    
    return True
```

---

## Step 5: Documentation & Testing

### Documentation Template

For each pattern, document its purpose, regex pattern, and design rationale.

### Testing Checklist

Validate patterns against test samples before deployment.

---

## Step 6: Maintenance & Versioning

### Pattern Registry

Maintain a version-controlled pattern file with metadata for your domain.

### Update Workflow

1. **Identify gaps** in test data → Patterns miss legitimate rows
2. **Analyze misses** → Add new keyword or regex
3. **Test** → Verify improvement without breaking existing matches
4. **Document** → Update pattern metadata (coverage %, examples)
5. **Version bump** → Increment pattern version
6. **Archive** → Keep old patterns for rollback

---

## Anti-Patterns (What to Avoid)

| ❌ Anti-Pattern | ✅ Better Approach |
|-----------------|-------------------|
| Overly complex regex (>100 chars) | Break into multiple simpler patterns |
| Regex that matches too much (low precision) | Add negative lookahead or combine with filters |
| Hard-coded values without documentation | Use constants with comments and examples |
| Patterns that work on one sample but fail on others | Test against 10+ diverse samples |
| Regex without `re.IGNORECASE` when case-insensitive needed | Always specify flags explicitly |
| Fabricating data when pattern doesn't match | Return empty string; let `enrich_keep=false` filter |

---

## Examples by Domain

Domains have different characteristics. Adapt the approach accordingly.

---

## Troubleshooting Pattern Performance

Common issues and general approaches to resolve them.

---

## See Also

- [enrich_strategy.md](enrich_strategy.md) — How to use patterns in enrichment
- [canonical_evidence_schema.md](canonical_evidence_schema.md) — Target fields
