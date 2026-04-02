"""Tests for evaluation.core.incar_parser."""

from __future__ import annotations

import pytest

from evaluation.core.incar_parser import (
    IncarCompareResult,
    compare_incar,
    extract_incar_from_text,
    normalize_value,
    parse_incar,
)

# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

SIMPLE_INCAR = """\
SYSTEM = Test
PREC   = Accurate
ENCUT  = 520
EDIFF  = 1E-06
NSW    = 0
IBRION = -1
"""


def test_parse_basic():
    d = parse_incar(SIMPLE_INCAR)
    assert d["SYSTEM"] == "Test"
    assert d["PREC"] == "Accurate"
    assert d["ENCUT"] == "520"
    assert d["EDIFF"] == "1E-06"
    assert d["NSW"] == "0"
    assert d["IBRION"] == "-1"


COMMENTED_INCAR = """\
# This is a comment
PREC = Accurate
ENCUT = 520  # plane-wave cutoff
! Another comment line
EDIFF = 1E-06  ! tight convergence
#ISPIN = 2
ISMEAR = 0
"""


def test_parse_comments():
    d = parse_incar(COMMENTED_INCAR)
    assert "PREC" in d
    assert d["ENCUT"] == "520"
    assert d["EDIFF"] == "1E-06"
    assert d["ISMEAR"] == "0"
    # Commented-out ISPIN should NOT be parsed
    assert "ISPIN" not in d


CONTINUATION_INCAR = """\
MAGMOM = 4*5.0 4*0.0
         24*0.0 4*0.0
ENCUT = 520
"""


def test_parse_continuation():
    d = parse_incar(CONTINUATION_INCAR)
    assert "MAGMOM" in d
    # Continuation should be appended with space
    assert "4*5.0 4*0.0" in d["MAGMOM"]
    assert "24*0.0 4*0.0" in d["MAGMOM"]
    assert d["ENCUT"] == "520"


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        (".TRUE.", True),
        (".TRUE", True),
        (".true.", True),
        ("T", True),
        (".FALSE.", False),
        (".FALSE", False),
        ("F", False),
    ],
)
def test_normalize_bool(raw, expected):
    assert normalize_value("LDAU", raw) is expected


def test_normalize_int():
    assert normalize_value("NSW", "0") == 0
    assert normalize_value("IBRION", "-1") == -1
    assert normalize_value("ISPIN", "2") == 2
    assert normalize_value("NELM", "200") == 200
    # Float notation for int tag
    assert normalize_value("ENCUT", "520.000000") == 520.0  # ENCUT is float


def test_normalize_float():
    assert normalize_value("EDIFF", "1E-06") == pytest.approx(1e-6)
    assert normalize_value("EDIFF", "5.00e-07") == pytest.approx(5e-7)
    assert normalize_value("SIGMA", "0.05") == pytest.approx(0.05)
    assert normalize_value("ENCUT", "520.000000") == pytest.approx(520.0)


def test_normalize_repeat():
    val = normalize_value("MAGMOM", "12*1.0000")
    assert isinstance(val, list)
    assert len(val) == 12
    assert all(v == pytest.approx(1.0) for v in val)


def test_normalize_mixed_repeat():
    val = normalize_value("MAGMOM", "4*5.0 24*0.0 4*0.0")
    assert isinstance(val, list)
    assert len(val) == 32
    assert val[:4] == [pytest.approx(5.0)] * 4
    assert val[4:28] == [pytest.approx(0.0)] * 24


def test_normalize_array_plain():
    val = normalize_value("LDAUL", "-1 -1 2 -1")
    assert val == [-1, -1, 2, -1]


def test_normalize_algo_alias():
    assert normalize_value("ALGO", "A") == "ALL"
    assert normalize_value("ALGO", "All") == "ALL"
    assert normalize_value("ALGO", "Damped") == "DAMPED"
    assert normalize_value("ALGO", "N") == "NORMAL"
    assert normalize_value("ALGO", "Fast") == "FAST"


def test_normalize_prec_alias():
    assert normalize_value("PREC", "ACC") == "ACCURATE"
    assert normalize_value("PREC", "Accurate") == "ACCURATE"
    assert normalize_value("PREC", "H") == "HIGH"
    assert normalize_value("PREC", "High") == "HIGH"


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------


def test_compare_exact_match():
    incar = """\
PREC = Accurate
ENCUT = 520
NSW = 0
IBRION = -1
EDIFF = 1E-06
"""
    ref = parse_incar(incar)
    agent = parse_incar(incar)
    result = compare_incar(ref, agent, key_tags=["NSW", "IBRION"])
    assert result.full_score == 1.0
    assert result.key_score == 1.0
    assert len(result.mismatched) == 0
    assert len(result.missing) == 0


def test_compare_with_mismatches():
    ref_text = """\
LDAU = .TRUE.
LDAUTYPE = 2
LDAUL = -1 -1 2 -1
LDAUU = 0.000 0.000 4.380 0.000
NSW = 0
IBRION = -1
LMAXMIX = 4
MAGMOM = 12*1.0000
"""
    agent_text = """\
LDAU = .TRUE.
LDAUTYPE = 2
LDAUL = -1 -1 2 -1
LDAUU = 0 0 4.0 0
NSW = 100
IBRION = 2
"""
    ref = parse_incar(ref_text)
    agent = parse_incar(agent_text)
    result = compare_incar(
        ref, agent, key_tags=["LDAU", "LDAUTYPE", "LDAUL", "LDAUU", "NSW", "IBRION"]
    )

    # LDAU, LDAUTYPE, LDAUL should match
    assert "LDAU" in result.key_matched
    assert "LDAUTYPE" in result.key_matched
    assert "LDAUL" in result.key_matched

    # LDAUU: 4.38 vs 4.0 — ~9% diff, within 15% default tol
    assert "LDAUU" in result.key_matched

    # NSW: 0 vs 100 — mismatch (int exact)
    assert "NSW" in result.key_mismatched

    # IBRION: -1 vs 2 — mismatch
    assert "IBRION" in result.key_mismatched

    # LMAXMIX, MAGMOM — missing in agent
    assert "LMAXMIX" in result.missing
    assert "MAGMOM" in result.missing

    # Scores
    assert result.key_score < 1.0
    assert result.full_score < 1.0


def test_compare_key_tags_score():
    ref_text = "NSW = 0\nIBRION = -1\nISPIN = 2\n"
    agent_text = "NSW = 0\nIBRION = -1\nISPIN = 1\n"
    ref = parse_incar(ref_text)
    agent = parse_incar(agent_text)
    result = compare_incar(ref, agent, key_tags=["NSW", "IBRION", "ISPIN"])
    assert result.key_score == pytest.approx(2.0 / 3.0)


# ---------------------------------------------------------------------------
# Extract from text
# ---------------------------------------------------------------------------


def test_extract_from_markdown():
    text = """
Here is the INCAR:

```
PREC = Accurate
ENCUT = 520
NSW = 0
IBRION = -1
EDIFF = 1E-06
ISMEAR = 0
```

That's the file.
"""
    extracted = extract_incar_from_text(text)
    assert extracted is not None
    d = parse_incar(extracted)
    assert d["PREC"] == "Accurate"
    assert d["ENCUT"] == "520"
    assert d["NSW"] == "0"


def test_extract_from_markdown_with_lang():
    text = """
```fortran
PREC = Accurate
ENCUT = 520
NSW = 0
EDIFF = 1E-06
```
"""
    extracted = extract_incar_from_text(text)
    assert extracted is not None
    assert "PREC" in parse_incar(extracted)


def test_extract_no_incar():
    text = "This is just some text with no INCAR content."
    assert extract_incar_from_text(text) is None


# ---------------------------------------------------------------------------
# Real reference INCAR: IG_incar_005 (DFT+U)
# ---------------------------------------------------------------------------

IG005_REF = """\
INCAR created by Atomic Simulation Environment
 EMAX = 15.000000
 EMIN = -15.000000
 ENAUG = 659.424000
 ENCUT = 520.000000
 PSTRESS = 0.000000
 EDIFF = 5.00e-07
 SYMPREC = 1.00e-04
 ALGO = A
 GGA = PE
 PREC = High
 IBRION = -1
 ISPIN = 2
 ISYM = 2
 KPAR = 4
 LDAUTYPE = 2
 LMAXMIX = 4
 LORBIT = 11
 NELM = 200
 NSW = 0
 NCORE = 12
 LDAU = .TRUE.
 LDAUL = -1 -1 2 -1
 LDAUU = 0.000 0.000 4.380 0.000
 LDAUJ = 0.000 0.000 0.000 0.000
 MAGMOM = 12*1.0000
"""


def test_real_ig005_reference():
    d = parse_incar(IG005_REF)
    # Check all key tags parsed
    assert d["ALGO"] == "A"
    assert d["PREC"] == "High"
    assert d["LDAU"] == ".TRUE."
    assert d["LDAUL"] == "-1 -1 2 -1"
    assert d["LDAUU"] == "0.000 0.000 4.380 0.000"
    assert d["MAGMOM"] == "12*1.0000"
    assert d["NSW"] == "0"
    assert d["IBRION"] == "-1"
    assert d["LMAXMIX"] == "4"

    # Check normalization
    assert normalize_value("ALGO", d["ALGO"]) == "ALL"
    assert normalize_value("PREC", d["PREC"]) == "HIGH"
    assert normalize_value("LDAU", d["LDAU"]) is True
    assert normalize_value("LDAUL", d["LDAUL"]) == [-1, -1, 2, -1]
    assert normalize_value("LDAUU", d["LDAUU"]) == [
        pytest.approx(0.0),
        pytest.approx(0.0),
        pytest.approx(4.38),
        pytest.approx(0.0),
    ]
    magmom = normalize_value("MAGMOM", d["MAGMOM"])
    assert len(magmom) == 12
    assert all(v == pytest.approx(1.0) for v in magmom)


def test_real_ig005_agent_comparison():
    """Compare a realistic agent output against IG_005 reference."""
    agent_text = """\
LDAU = .TRUE.
LDAUTYPE = 2
LDAUL = -1 -1 2 -1
LDAUU = 0 0 4.0 0
LDAUJ = 0 0 0.0 0
ISPIN = 2
ISMEAR = 0
SIGMA = 0.05
ENCUT = 520
PREC = Accurate
EDIFF = 1E-06
ALGO = Normal
IBRION = 2
NSW = 100
ISIF = 3
LORBIT = 11
LWAVE = .FALSE.
LCHARG = .FALSE.
LREAL = Auto
GGA = PE
AMIX = 0.2
BMIX = 0.0001
AMIX_MAG = 0.8
BMIX_MAG = 0.0001
NELM = 200
"""
    ref = parse_incar(IG005_REF)
    agent = parse_incar(agent_text)
    result = compare_incar(
        ref,
        agent,
        key_tags=["LDAU", "LDAUTYPE", "LDAUL", "LDAUU", "NSW", "IBRION", "ISPIN", "LMAXMIX", "MAGMOM"],
    )

    # Key mismatches expected
    assert "NSW" in result.key_mismatched  # 0 vs 100
    assert "IBRION" in result.key_mismatched  # -1 vs 2
    assert "LMAXMIX" in result.key_missing  # not in agent
    assert "MAGMOM" in result.key_missing  # not in agent

    # Key matches
    assert "LDAU" in result.key_matched
    assert "LDAUTYPE" in result.key_matched
    assert "LDAUL" in result.key_matched
    assert "ISPIN" in result.key_matched

    # LDAUU: 4.38 vs 4.0 ~= 8.7% diff < 15% tol → match
    assert "LDAUU" in result.key_matched

    # Overall key_score should be ~5/9
    assert result.key_score == pytest.approx(5.0 / 9.0)

    # Summary should be readable
    s = result.summary()
    assert "NSW" in s
    assert "LMAXMIX" in s
    assert "MAGMOM" in s
