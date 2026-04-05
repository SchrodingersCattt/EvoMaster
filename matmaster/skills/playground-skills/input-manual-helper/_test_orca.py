"""临时验证脚本 — 验证 ORCA 后端全链路"""

import sys

sys.path.insert(0, '.')

from engine.renderer import RenderIntent  # noqa: E402
from engine.schema import SchemaRegistry  # noqa: E402
from engine.software.orca import ORCABackend  # noqa: E402

out = []

# Schema
reg = SchemaRegistry()
reg.load_software('orca')
tags = reg.list_tags('orca')
out.append(f'Schema tags: {len(tags)}')

# Render scf
backend = ORCABackend()
intent = RenderIntent(software='orca', task_type='scf', structure_file=None, params={})
text = backend.render(intent)
out.append('--- Rendered ORCA input (scf) ---')
out.append(text)
out.append('---')

# Parse
doc = backend.parse(text)
out.append(
    f'Parsed: {len(doc.params)} params, {len(doc.sections)} sections, {len(doc.parse_errors)} errors'
)

# Diagnostics
diags = backend.get_diagnostics(doc, reg)
out.append(f'Diagnostics: {len(diags)} items')
for d in diags:
    out.append(f'  {d.to_human()}')

# Completions
completions = backend.get_completions(doc, 1, 0, reg)
out.append(f'Completions at line 1 (keyword line): {len(completions)} items')
if completions:
    out.append(f'  First 5: {[c.label for c in completions[:5]]}')

# Render opt
intent2 = RenderIntent(
    software='orca',
    task_type='opt',
    structure_file=None,
    params={'functional': 'PBE0', 'basis': 'def2-TZVP'},
)
text2 = backend.render(intent2)
out.append('--- Rendered ORCA opt input ---')
out.append(text2)
out.append('---')

# Parse opt
doc2 = backend.parse(text2)
out.append(
    f'Parsed opt: {len(doc2.params)} params, {len(doc2.sections)} sections, {len(doc2.parse_errors)} errors'
)
diags2 = backend.get_diagnostics(doc2, reg)
out.append(f'Diagnostics opt: {len(diags2)} items')
for d in diags2:
    out.append(f'  {d.to_human()}')

# Render tddft
intent3 = RenderIntent(
    software='orca',
    task_type='tddft',
    structure_file=None,
    params={'functional': 'B3LYP', 'basis': 'def2-TZVP'},
)
text3 = backend.render(intent3)
out.append('--- Rendered ORCA tddft input ---')
out.append(text3)
out.append('---')

result = '\n'.join(out)
with open('_test_orca_output.txt', 'w', encoding='utf-8') as f:
    f.write(result)
print('DONE — output written to _test_orca_output.txt')
