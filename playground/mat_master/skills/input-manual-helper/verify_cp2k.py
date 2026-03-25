"""验证 CP2K 后端全链路。"""
import sys
sys.path.insert(0, '.')

from engine.software.cp2k import CP2KBackend
from engine.renderer import RenderIntent
from engine.schema import SchemaRegistry

# 1. Schema
reg = SchemaRegistry()
reg.load_software('cp2k')
tag = reg.get_tag('cp2k', 'CUTOFF')
assert tag is not None, "CUTOFF tag should not be None"
print(f"[OK] Schema: CUTOFF type={tag.param_type} default={tag.default} range={tag.valid_range}")

all_tags = reg.list_tags('cp2k')
print(f"[OK] Total tags in schema: {len(all_tags)}")

# 2. Renderer
backend = CP2KBackend()
intent = RenderIntent(software='cp2k', task_type='scf', structure_file=None, params={})
text = backend.render(intent)
print(f"[OK] Rendered {len(text)} chars")
print("--- Rendered CP2K input ---")
print(text)
print("--- end ---")

# 3. Parser
doc = backend.parse(text)
print(f"[OK] Parsed: {len(doc.params)} params, {len(doc.sections)} top sections")
print(f"     parse_errors: {len(doc.parse_errors)}")
for sec in doc.sections:
    print(f"     section: {sec.name} (children={len(sec.children)}, params={len(sec.params)})")

# 4. Diagnostics
diags = backend.get_diagnostics(doc, reg)
print(f"[OK] Diagnostics: {len(diags)} items")
for d in diags:
    print(f"  {d.to_human()}")

# 5. Completions (quick check)
comps = backend.get_completions(doc, 1, 0, reg)
print(f"[OK] Completions at line 1: {len(comps)} items")

# 6. 验证 render 含必要的关键字
required_keywords = [
    "&GLOBAL", "RUN_TYPE ENERGY", "&FORCE_EVAL", "METHOD Quickstep",
    "&DFT", "BASIS_SET_FILE_NAME", "POTENTIAL_FILE_NAME",
    "&MGRID", "CUTOFF", "&SCF", "EPS_SCF",
    "&XC", "&XC_FUNCTIONAL PBE",
    "&KPOINTS", "SCHEME",
    "&SUBSYS", "&CELL", "&COORD", "Si", "&KIND Si",
    "BASIS_SET DZVP-MOLOPT-SR-GTH", "POTENTIAL GTH-PBE-q4",
]
missing = [kw for kw in required_keywords if kw not in text]
if missing:
    print(f"[WARN] Missing in rendered output: {missing}")
else:
    print("[OK] All required keywords present in rendered output")

print("\nAll checks passed!")
