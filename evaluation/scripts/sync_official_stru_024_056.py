#!/usr/bin/env python3
"""Copy official abacus-develop STRU into question bank data/ and strip STRU grading."""

from __future__ import annotations

import shutil
from pathlib import Path

from ruamel.yaml import YAML

REPO = Path(__file__).resolve().parents[2]
yaml = YAML()
yaml.preserve_quotes = True
yaml.width = 4096
BANK = REPO / "evaluation" / "question_bank"
YAML_PATH = BANK / "input_generation" / "ig_agnostic_abacus.yaml"
EXAMPLES = Path("/tmp/abacus-develop-examples/examples")

# question_id -> list of (data_key, relative_path_under_examples)
STRU_SOURCES: dict[str, list[tuple[str, str]]] = {
    "IG_abacus_024_20260518": [
        ("STRU_pw_Si2", "02_scf/01_pw_Si2/STRU"),
        ("STRU_lcao_Si2", "02_scf/02_lcao_Si2/STRU"),
    ],
    "IG_abacus_025_20260518": [
        ("STRU_Fe_FM", "03_spin_polarized/02_Fe_FM/STRU"),
        ("STRU_Fe_AFM", "03_spin_polarized/03_Fe_AFM/STRU"),
        ("STRU_H_atom", "03_spin_polarized/01_H_atom/STRU"),
        ("STRU_Co6Nb4Os2", "03_spin_polarized/04_Co6Nb4Os2/STRU"),
    ],
    "IG_abacus_026_20260518": [
        ("STRU_Fe_nc", "04_noncollinear/01_Fe_noncollinear/STRU"),
    ],
    "IG_abacus_027_20260518": [
        ("STRU_GaAs", "05_soc/01_pw_GaAs/STRU"),
    ],
    "IG_abacus_028_20260518": [
        ("STRU_Fe_smearing", "06_smearing/01_lcao_Fe/STRU"),
    ],
    "IG_abacus_029_20260518": [
        ("STRU_Al", "07_charge_mixing/01_pw_Al/STRU"),
    ],
    "IG_abacus_030_20260518": [
        ("STRU_Al", "08_charge_density/01_pw_Al_nspin1/STRU"),
        ("STRU_Si2_lcao", "08_charge_density/02_lcao_Si2_nspin1/STRU"),
        ("STRU_Fe", "08_charge_density/03_pw_Fe_nspin2/STRU"),
    ],
    "IG_abacus_031_20260518": [
        ("STRU_Si2", "09_density_matrix/01_lcao_Si2_nspin1/STRU"),
    ],
    "IG_abacus_032_20260518": [
        ("STRU_hsk", "10_hs_matrix/04_out_hsk_multik/STRU"),
        ("STRU_hsr", "10_hs_matrix/01_out_hsr_multik/STRU"),
        ("STRU_gets", "10_hs_matrix/05_gets/STRU"),
    ],
    "IG_abacus_033_20260518": [
        ("STRU_Al_pw", "11_wfc/01_pw_scf_Al/STRU"),
        ("STRU_Si2_lcao", "11_wfc/02_lcao_scf_Si2/STRU"),
        ("STRU_Si2_getwf", "11_wfc/03_lcao_getwf/STRU"),
    ],
    "IG_abacus_034_20260518": [
        ("STRU_Al", "12_band/01_pw_Al/STRU"),
        ("STRU_Si2_lcao", "12_band/02_lcao_Si2/STRU"),
    ],
    "IG_abacus_035_20260518": [
        ("STRU_Al", "13_dos/01_pw_Al/STRU"),
        ("STRU_Si2_lcao", "13_dos/02_lcao_Si2/STRU"),
    ],
    "IG_abacus_036_20260518": [
        ("STRU_Si2", "14_mulliken/01_lcao_Si2/STRU"),
    ],
    "IG_abacus_037_20260518": [
        ("STRU_C62N1", "15_fixed_occ/01_C62N1/STRU"),
    ],
    "IG_abacus_038_20260518": [
        ("STRU_Fe_uspp", "16_uspp/01_pw_Fe/STRU"),
    ],
    "IG_abacus_039_20260518": [
        ("STRU_Al_relax", "17_relax/01_pw_cell_relax_BFGS_Al/STRU"),
        ("STRU_Si2_relax", "17_relax/02_lcao_relax_CG_Si2/STRU"),
    ],
    "IG_abacus_040_20260518": [
        ("STRU_Si8", "18_md/01_lcao_gamma_Si8/STRU"),
    ],
    "IG_abacus_042_20260518": [
        ("STRU_pw_Si2_hybrid", "20_hybrid/01_pw_Si2/STRU"),
        ("STRU_lcao_Si2_hybrid", "20_hybrid/02_lcao_Si2/STRU"),
    ],
    "IG_abacus_043_20260518": [
        ("STRU_H2O_pw", "21_deepks/01_pw_H2O/STRU"),
        ("STRU_H2O_lcao", "21_deepks/02_lcao_H2O/STRU"),
    ],
    "IG_abacus_044_20260518": [
        ("STRU_H2_length", "22_rt-tddft/01_H2_length_gauge/STRU"),
        ("STRU_H2_velocity", "22_rt-tddft/02_H2_velocity_gauge/STRU"),
    ],
    "IG_abacus_045_20260518": [
        ("STRU_Si2_sdft", "23_sdft/01_pw_Si2/STRU"),
        ("STRU_Al_sdft", "23_sdft/02_pw_md_Al/STRU"),
    ],
    "IG_abacus_046_20260518": [
        ("STRU_Si2_lr", "24_lr-tddft/01_lcao_Si2/STRU"),
        ("STRU_H2O_lr", "24_lr-tddft/02_lcao_H2O/STRU"),
    ],
    "IG_abacus_047_20260518": [
        ("STRU_Si2_vdw_D2", "25_vdw/01_vdw_D2_Si2/STRU"),
        ("STRU_Si2_vdw_D3", "25_vdw/02_vdw_D3_Si2/STRU"),
    ],
    "IG_abacus_052_20260518": [
        ("STRU_Si", "30_elec_pot/01_lcao_Si/STRU"),
    ],
    "IG_abacus_055_20260518": [
        ("STRU_Fe2", "33_pexsi/01_spin_Fe2/STRU"),
    ],
    "IG_abacus_056_20260518": [
        ("STRU_Si16_pw", "34_gpu/01_pw_Si16/STRU"),
        ("STRU_Si16_lcao", "34_gpu/02_lcao_Si16/STRU"),
    ],
}

PROMPTS: dict[str, str] = {
    "IG_abacus_024_20260518": (
        "为 Si₂ 金刚石结构准备两套基础 ABACUS 自洽场输入，分别使用平面波与 LCAO 基组："
        "在 `run_pw_si2/`、`run_lcao_si2/` 各编写 `INPUT` 与 `KPT`（只写文件，不运行）。"
    ),
    "IG_abacus_025_20260518": (
        "准备四套 LCAO 自旋极化 SCF 输入："
        "`run_fe_fm/`（bcc Fe 铁磁）、`run_fe_afm/`（反铁磁）、`run_h_atom/`（单 H）、"
        "`run_co6nb4os2/`（Co₆Nb₄Os₂ 团簇）。各目录编写 `INPUT` 与 `KPT`（只写文件，不运行）。"
    ),
    "IG_abacus_026_20260518": (
        "为 bcc Fe 准备一套 ABACUS 非共线 LCAO 自洽场输入：在 `run_fe_nc/` 编写 `INPUT` 与 `KPT`"
        "（含自旋轨道耦合；只写文件，不运行）。"
    ),
    "IG_abacus_027_20260518": (
        "为 GaAs 准备 ABACUS 平面波 + 自旋轨道耦合两步输入："
        "`run_gaas_scf/`（自洽）、`run_gaas_band/`（能带非自洽）。各目录编写 `INPUT` 与 `KPT`（只写文件，不运行）。"
    ),
    "IG_abacus_028_20260518": (
        "为自旋极化 Fe 准备两套 LCAO SCF 输入，对比 Gaussian 与 fixed smearing："
        "`run_gaussian/`、`run_fixed/` 各编写 `INPUT` 与 `KPT`（只写文件，不运行）。"
    ),
    "IG_abacus_029_20260518": (
        "为 fcc Al 准备两套 PW SCF 输入，对比 Broyden 与 plain 电荷混合："
        "`run_broyden/`、`run_plain/` 各编写 `INPUT` 与 `KPT`（只写文件，不运行）。"
    ),
    "IG_abacus_030_20260518": (
        "准备三套输出电荷密度的 SCF 输入：`run_pw_al/`、`run_lcao_si2/`、`run_pw_fe/`。"
        "各目录编写 `INPUT` 与 `KPT`（只写文件，不运行）。"
    ),
    "IG_abacus_031_20260518": (
        "为 LCAO Si₂ 在 `run_lcao_si2/` 准备输出密度矩阵的 SCF 输入：编写 `INPUT`"
        "（Γ 点可不写 `KPT`；只写文件，不运行）。"
    ),
    "IG_abacus_032_20260518": (
        "准备三套 LCAO H/S 矩阵输出输入：`run_hsk/`、`run_hsr/`、`run_get_s/`。"
        "各目录编写 `INPUT`；需 k 点采样的目录另写 `KPT`（只写文件，不运行）。"
    ),
    "IG_abacus_033_20260518": (
        "准备三套波函数输出输入：`run_pw_al/`、`run_lcao_si2/`、`run_lcao_getwf/`。"
        "各目录编写 `INPUT` 与 `KPT`（只写文件，不运行）。"
    ),
    "IG_abacus_034_20260518": (
        "准备 PW 与 LCAO 能带两步工作流输入："
        "`run_pw_scf/`、`run_pw_band/`、`run_lcao_scf/`、`run_lcao_band/`。"
        "各目录编写 `INPUT` 与 `KPT`（只写文件，不运行）。"
    ),
    "IG_abacus_035_20260518": (
        "准备 PW 与 LCAO DOS 工作流输入："
        "`run_pw_scf/`、`run_pw_dos/`、`run_lcao_scf/`、`run_lcao_dos/`。"
        "各目录编写 `INPUT` 与 `KPT`（只写文件，不运行）。"
    ),
    "IG_abacus_036_20260518": (
        "为 LCAO Si₂ 在 `run_lcao_si2/` 准备含 Mulliken 输出的 SCF 输入：编写 `INPUT` 与 `KPT`（只写文件，不运行）。"
    ),
    "IG_abacus_037_20260518": (
        "为自旋极化 C/N 在 `run_fixed_occ/` 准备固定占据 SCF 输入：编写 `INPUT`（只写文件，不运行）。"
    ),
    "IG_abacus_038_20260518": (
        "为 bcc Fe 在 `run_pw_fe_uspp/` 准备超软赝势 PW SCF 输入：编写 `INPUT` 与 `KPT`（只写文件，不运行）。"
    ),
    "IG_abacus_039_20260518": (
        "准备两套弛豫输入：`run_pw_cell_relax/`（PW 可变晶胞）、`run_lcao_relax/`（LCAO 原子弛豫）。"
        "各目录编写 `INPUT` 与 `KPT`（只写文件，不运行）。"
    ),
    "IG_abacus_040_20260518": (
        "为 Si₈ 准备两套 LCAO 分子动力学输入：`run_md_nve/`（NVE）、`run_md_nvt/`（NVT）。"
        "各目录编写 `INPUT`（`gamma_only` 时可不写 `KPT`；只写文件，不运行）。"
    ),
    "IG_abacus_042_20260518": (
        "为 Si₂ 准备两套杂化泛函 SCF 输入：`run_pw_si2_hybrid/`（PW）、`run_lcao_si2_hybrid/`（LCAO）。"
        "各目录编写 `INPUT` 与 `KPT`（只写文件，不运行）。"
    ),
    "IG_abacus_043_20260518": (
        "准备 DeePKS 两套输入：`run_pw_h2o_gen_bessel/`（gen_bessel）、`run_lcao_h2o_deepks/`（deepks 自洽）。"
        "各目录编写 `INPUT`；需要 k 点的目录另写 `KPT`（只写文件，不运行）。"
    ),
    "IG_abacus_044_20260518": (
        "为 H₂ 准备两套实时 TDDFT 输入：`run_h2_tddft_length/`（长度规范）、`run_h2_tddft_velocity/`（速度规范）。"
        "各目录编写 `INPUT` 与 `KPT`（只写文件，不运行）。"
    ),
    "IG_abacus_045_20260518": (
        "准备两套随机 DFT 平面波输入：`run_pw_si2_sdft/`（Si₂ SCF）、`run_pw_al_sdft_md/`（Al MD）。"
        "各目录编写 `INPUT` 与 `KPT`（只写文件，不运行）。"
    ),
    "IG_abacus_046_20260518": (
        "准备两套 LCAO 线性响应 TDDFT 输入：`run_lcao_si2_lr_tddft/`（Si₂）、`run_lcao_h2o_lr_tddft/`（H₂O）。"
        "各目录编写 `INPUT`；需 k 点的目录另写 `KPT`（只写文件，不运行）。"
    ),
    "IG_abacus_047_20260518": (
        "为 Si₂ 准备四套范德华修正 SCF 输入："
        "`run_vdw_d2_lcao/`、`run_vdw_d2_pw/`、`run_vdw_d3_lcao/`、`run_vdw_d3_pw/`。"
        "各目录编写 `INPUT` 与 `KPT`；D2 目录另须提供 `c6.txt`（只写文件，不运行）。"
    ),
    "IG_abacus_052_20260518": (
        "为 Si 准备 LCAO SCF 静电势输出输入：在 `run_lcao_si_epot/` 编写 `INPUT` 与 `KPT`（只写文件，不运行）。"
    ),
    "IG_abacus_055_20260518": (
        "为 Fe₂ 二聚体准备 LCAO 自旋极化 PEXSI SCF 输入：在 `run_lcao_fe2_pexsi/` 编写 `INPUT` 与 `KPT`（只写文件，不运行）。"
    ),
    "IG_abacus_056_20260518": (
        "为 Si₁₆ 准备两套 GPU 加速 SCF 输入：`run_pw_si16_gpu/`（PW）、`run_lcao_si16_gpu/`（LCAO）。"
        "各目录编写 `INPUT` 与 `KPT`（只写文件，不运行）。"
    ),
}


def _is_stru_ref(key: str, value: object) -> bool:
    if "stru" in key.lower():
        return True
    if isinstance(value, str) and value.rstrip("/").endswith("STRU"):
        return True
    if isinstance(value, dict):
        fn = str(value.get("filename", ""))
        if "/STRU" in fn or fn.endswith("STRU"):
            return True
        if value.get("check") in (
            "lattice_matches",
            "magnetic_order",
            "species_count",
            "total_atoms",
        ):
            return True
    return False


def _is_stru_score(item: dict) -> bool:
    iid = item.get("id", "")
    crit = item.get("criterion", "")
    if "stru" in iid.lower():
        return True
    if item.get("verify") == "stru_file_check":
        return True
    if "/STRU" in crit or "`STRU" in crit or " in STRU" in crit:
        return True
    if "LATTICE_CONSTANT" in crit and "INPUT" not in crit:
        return True
    if "NUMERICAL_ORBITAL" in crit:
        return True
    return False


def _copy_stru_files() -> None:
    for qid, entries in STRU_SOURCES.items():
        num = qid.split("_")[2]
        dest_dir = BANK / "data" / f"IG_abacus_{num}_20260518"
        dest_dir.mkdir(parents=True, exist_ok=True)
        for key, rel in entries:
            src = EXAMPLES / rel
            if not src.is_file():
                raise FileNotFoundError(src)
            shutil.copy2(src, dest_dir / key)


def _patch_questions(questions: list[dict]) -> int:
    patched = 0
    for q in questions:
        qid = q["id"]
        if qid not in STRU_SOURCES:
            continue
        num = qid.split("_")[2]
        q["human_prompt_seed"] = PROMPTS[qid]
        q["data_files"] = [
            {
                "key": key,
                "path": f"data/IG_abacus_{num}_20260518/{key}",
                "oss_url": "",
                "description": f"Official STRU from abacus-develop examples/{rel.rsplit('/', 1)[0]}",
            }
            for key, rel in STRU_SOURCES[qid]
        ]
        q["reference_answers"] = [
            r
            for r in q.get("reference_answers", [])
            if not _is_stru_ref(r.get("key", ""), r.get("value"))
        ]
        q["scoring_checklist"] = [
            s for s in q.get("scoring_checklist", []) if not _is_stru_score(s)
        ]
        # ruamel may append data_files at the end; normalize field order.
        df = q.pop("data_files", None)
        if df is not None:
            q["data_files"] = df
        patched += 1
    return patched


def main() -> None:
    if not EXAMPLES.is_dir():
        raise SystemExit(f"Missing examples tree: {EXAMPLES}")
    _copy_stru_files()
    with YAML_PATH.open(encoding="utf-8") as fh:
        doc = yaml.load(fh)
    questions = doc["questions"]
    n = _patch_questions(questions)
    with YAML_PATH.open("w", encoding="utf-8") as fh:
        yaml.dump(doc, fh)
    print(f"Patched {n} questions; wrote {YAML_PATH}")


if __name__ == "__main__":
    main()
