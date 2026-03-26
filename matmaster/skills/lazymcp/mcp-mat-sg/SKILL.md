---
name: mcp-mat-sg
description: 当需要构建、生成或修改原子结构时调用本 skill。支持从 SMILES 构建分子、Wyckoff/模板构建晶体、切表面 slab、超胞、缺陷、掺杂、交联聚合物等。
skill_type: mcp-loader
mcp_server: mat_sg
---

# mat_sg — 结构生成与修改

## 连接方式

调用本 skill 后，下列工具已自动注册到你的工具列表中，可直接按工具名调用。无需手动连接 MCP 服务器。

## 工具列表

| 工具名 | 类型 | 说明 |
|--------|------|------|
| add_hydrogens | sync | 为分子添加氢原子 |
| generate_ordered_replicas | sync | 生成有序副本 |
| apply_structure_transformation | sync | 应用结构变换 |
| remove_solvents | sync | 移除溶剂分子 |
| build_bulk_structure_by_template | sync | 通过模板构建体相结构 |
| build_bulk_structure_by_wyckoff | sync | 通过 Wyckoff 位置构建体相结构 |
| extract_molecules_from_crystal | sync | 从晶体中提取分子 |
| make_supercell_structure | sync | 构建超胞 |
| make_defect_structure | sync | 构建缺陷结构 |
| make_doped_structure | sync | 构建掺杂结构 |
| make_amorphous_structure | sync | 构建非晶结构 |
| make_crosslinked_structure | sync | 构建交联聚合物结构 |
| build_polymer_chain | sync | 构建聚合物链 |
| build_molecule_structures_from_smiles | sync | 从 SMILES 构建分子结构 |
| add_cell_for_molecules | sync | 为分子添加晶胞 |
| build_surface_slab | sync | 切表面 slab |
| build_surface_adsorbate | sync | 构建表面吸附结构 |
| build_surface_interface | sync | 构建表面界面 |
| analyze_wyckoff_positions | sync | 分析 Wyckoff 位置 |
| get_structure_info | sync | 获取结构信息 |
| get_molecule_info | sync | 获取分子信息 |

## 典型用法

- 从 SMILES 构建分子: `mat_sg_build_molecule_structures_from_smiles`
- 通过 Wyckoff 位置构建晶体: `mat_sg_build_bulk_structure_by_wyckoff`
- 切表面 slab: `mat_sg_build_surface_slab`
- 构建超胞: `mat_sg_make_supercell_structure`
- 构建缺陷/掺杂结构: `mat_sg_make_defect_structure`, `mat_sg_make_doped_structure`

## 注意事项

- 所有工具均为同步执行 (sync)，直接返回结果
- 虽然 executor 配置了 dispatcher (GPU)，但 sync_tools 列表涵盖了所有工具，因此均在本地同步执行
