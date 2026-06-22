# 水盒子 DP-GEN 输入需求

目标：从头搭一个用于水盒子的 DP-GEN 力场开发配置。

约束：
- 训练阶段使用 DeePMD/DeepMD。
- exploration 阶段使用 LAMMPS。
- FP 标注阶段使用 VASP。
- 真实数据集、POTCAR、INCAR、工作目录还没准备好，路径可以使用 `/path/to/...` 占位。
- 输出只需要 `param_output.json` 和 `machine_output.json`。
