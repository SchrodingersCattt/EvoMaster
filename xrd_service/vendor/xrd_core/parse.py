import re
import math
import os
import ast
import logging

import pandas as pd
import numpy as np
from pathlib import Path
import os, re, struct, math, logging, tempfile, shutil, subprocess
try:
    import xylib as xylib_mod
except Exception:
    xylib_mod = None

here = Path(__file__).resolve()

def chemistry_subscript(chemicals):
    chemicals_deals = []
    for i in chemicals:
        sub_map = str.maketrans('0123456789', '₀₁₂₃₄₅₆₇₈₉')
        formula = i

        in_subscript = False  # 用于追踪是否处于子标式内部
        result = []

        for char in formula:
            if char == '·':
                in_subscript = not in_subscript
                result.append(char)
            elif char.isalpha() and in_subscript:
                in_subscript = False
                result.append(char)
            elif char.isdigit() and not in_subscript:
                result.append(char.translate(sub_map))
            else:
                result.append(char)

        result = ''.join(result)
        chemicals_deals.append(result)
    return chemicals_deals

def huoqu(database_all, tables_name):  # 传入数据库名称，数据库列表,返回all=【卡片名称化学式，峰值信息，晶相信息】
    chemical = []  # 化学式
    all_table = []  # 保存所有数据
    imformation = []  # 卡片编号
    Crystal_system = []  # 晶系
    Space_group = []  # 空间群
    theta_all = []  # x
    insty_all = []  # y
    for ind, a in enumerate(tables_name):

        table_list = database_all[ind]
        all_table.append(table_list)

        # =============================================
        # =============================================
        # =============================================
        chem_row = None
        for row in table_list:
            if row[0] == '化学式:' or row[0] == 'Chemical formula:':  # 判断
                chem_row = row
                break
        if chem_row is not None and len(chem_row) > 1:
            chemical.append(chem_row[1])  # 化学式写入list

        else:
            chemical.append("Unknown")  # 第一列中无化学式则返回Unknown

        # chemical = xiajiaobiao_protect(chemical)  # 下角标转化
        # =============================================
        # =============================================
        # =============================================
        imformation_row = None
        for sxinxi in table_list:
            if sxinxi[0] == 'Reference code:' or sxinxi[0] == '卡片号码:':
                imformation_row = sxinxi
                break
        if imformation_row is not None and len(imformation_row) > 1:
            imformation.append(imformation_row[1])
        else:
            imformation.append('Unknow')
        # =============================================
        # =============================================
        # =============================================
        Crystal_system_row = None
        for sxinxi in table_list:
            if sxinxi[0] == '晶系:' or sxinxi[0] == 'Crystal system:':
                Crystal_system_row = sxinxi
                break
        if Crystal_system_row is not None and len(Crystal_system_row) > 1:
            Crystal_system.append(Crystal_system_row[1])
        else:
            Crystal_system.append('Unknow')
        # =============================================
        # =============================================
        # =============================================
        Space_group_row = None
        for sxinxi in table_list:
            if sxinxi[0] == '空间群:' or sxinxi[0] == 'Space group:':
                Space_group_row = sxinxi
                break
        if Space_group_row is not None and len(Space_group_row) > 1:
            Space_group.append(Space_group_row[1])
        else:
            Space_group.append('Unknow')

        # =============================================
        # =============================================
        # =============================================
        theta = []  # 2theta
        insty = []  # 幅值
        for j in range(1, len(table_list)):
            theta.append(table_list[j][2])
            insty.append(table_list[j][3])
        del_c = []
        for k in range(len(insty)):  # 出错时一定要注意这个地方，if语句的判断，因为有的时候insty里面可能包含“ ”。
            if insty[k] == None:
                del_c.append(k)
        del_c.sort(reverse=True)
        for l in del_c:
            del insty[l]

        del_g = []
        for m in range(len(theta)):
            if theta[m] == ' ' or theta[m] is None:
                del_g.append(m)
        del_g.sort(reverse=True)
        for n in del_g:
            del theta[n]
        theta_all.append(theta)
        insty_all.append(insty)
    # =========================================================================================
    chemical_xia = chemistry_subscript(chemical)
    all = []  # 仅仅储存空间群、晶系、化学式、卡片编号等信息。[[卡片信息，化学式，x，y，晶系，空间群]]
    for i in range(len(chemical)):
        all_small = []
        all_small.append(imformation[i])
        all_small.append(chemical[i])
        all_small.append(theta_all[i])
        all_small.append(insty_all[i])
        all_small.append(Crystal_system[i])
        all_small.append(Space_group[i])
        all_small.append(chemical_xia[i])
        all.append(all_small)
    return all


def get_data_from_database():
    if os.environ.get("LOAD_DATABASE", None) == "false":
        logging.warning("Database is disabled")
        return None

    df = pd.read_hdf(here.parent / "XRD_database.h5")
    data = np.array(df).tolist()

    restored_data = []
    for da in data:
        for item in da:
            try:
                # 使用 ast.literal_eval 将字符串还原为列表
                restored_item = ast.literal_eval(item)
            except (ValueError, SyntaxError):
                # 如果转换失败，说明不是列表形式，直接保留原值
                restored_item = item
            restored_data.append(restored_item)

    tables_name = df.columns.tolist()
    all_tab = huoqu(restored_data, tables_name[0])  # 获取到输出库所有数据，只为只读取一边数据库
    return all_tab


def extract_data_from_xml(xml_string):
    # 匹配 intensities
    intensities_pattern = r'<intensities unit="counts">([\d\s]+)</intensities>'
    intensities_match = re.search(intensities_pattern, xml_string)

    if intensities_match:
        intensities = intensities_match.group(1).split()
        intensities = [int(value) for value in intensities]
        intensities = [round(((i * 100.0) / max(intensities)), 1) for i in intensities]
    else:
        intensities = []

    # 匹配 startPosition 和 endPosition
    positions_pattern = r'<startPosition>([\d\.]+)</startPosition>\s*<endPosition>([\d\.]+)</endPosition>'
    position_match = re.search(positions_pattern, xml_string)

    if position_match:
        start_position = position_match.group(1)
        end_position = position_match.group(2)
        step = (float(end_position) - float(start_position)) / (len(intensities) - 1)
        positions = [round(float(start_position) + i * step, 2) for i in range(len(intensities))]
    else:
        positions = []
    return positions, intensities


def read_xrdml(file_name: str, file_content: bytes):
    content = file_content.decode("utf-8")
    x_list, y_list = extract_data_from_xml(content)
    data = [x_list, y_list]
    return {
        f"{file_name}": {
            "data": data,
            "columns_title": ["Positions", "Intensities"],
        }
    }

def _parse_binary_float_fallback(file_name: str, file_bytes: bytes):
    """
    厂商 RAW 二进制兜底：按小端 float32 解出序列，尝试两种布局：
    1) 交错 XYXY...；
    2) 先 X block 再 Y block。
    满足基本物理约束后返回归一化数据。
    """
    floats = []
    try:
        # 以 4 字节为步长解出全部 float32
        for (val,) in struct.iter_unpack("<f", file_bytes[:len(file_bytes) // 4 * 4]):
            if math.isfinite(val):
                floats.append(val)
            else:
                floats.append(float("nan"))
    except Exception as e:
        raise ValueError(f"二进制 float32 解码失败: {e}")

    def normalize_y(ys):
        m = max(ys) if ys else 0.0
        return [0.0] * len(ys) if m <= 0 else [round((v * 100.0) / m, 1) for v in ys]

    # 帮助函数：判断 x 合理且单调
    def is_reasonable_x(x):
        if len(x) < 10:
            return False
        # 2theta 合理范围与步长
        if not (0.0 <= x[0] <= 180.0 and 0.0 <= x[-1] <= 180.0):
            return False
        diffs = [x[i+1] - x[i] for i in range(len(x)-1)]
        pos = sum(1 for d in diffs if d > 0)
        # 至少 90% 递增
        if pos < 0.9 * len(diffs):
            return False
        # 步长中位数
        steps = sorted(d for d in diffs if d > 0)
        if not steps:
            return False
        med = steps[len(steps)//2]
        return 1e-4 <= med <= 1.0

    # 布局1：交错 XYXY...
    xs1, ys1 = [], []
    for i in range(0, len(floats) - 1, 2):
        x = floats[i]; y = floats[i+1]
        if not math.isfinite(x) or not math.isfinite(y):
            continue
        xs1.append(round(x, 2))
        ys1.append(y)
    if is_reasonable_x(xs1) and len(xs1) == len(ys1) and len(xs1) >= 50:
        return {f"{file_name}": {"data": [xs1, normalize_y(ys1)], "columns_title": ["Positions", "Intensities"]}}

    # 布局2：X block + Y block（从头开始取最长递增前缀）
    xs2 = []
    for i in range(len(floats)):
        v = floats[i]
        if not math.isfinite(v):
            break
        if not xs2:
            xs2.append(round(v, 2))
        else:
            if v > xs2[-1] - 1e-6:  # 非严格递增也接受极小抖动
                xs2.append(round(v, 2))
            else:
                break
    N = len(xs2)
    ys2 = []
    if N >= 50 and (len(floats) - N) >= N:
        for j in range(N):
            y = floats[N + j]
            if not math.isfinite(y):
                ys2 = []
                break
            ys2.append(y)
        if ys2 and len(ys2) == N and is_reasonable_x(xs2):
            return {f"{file_name}": {"data": [xs2, normalize_y(ys2)], "columns_title": ["Positions", "Intensities"]}}

    raise ValueError("二进制兜底解析失败")

def read_raw(file_name: str, file_content: bytes):
    """
    解析 XRD 原始 RAW 文本文件（两列：位置 强度），强度归一化到 0-100。
    支持空格/制表符/逗号分隔；跳过无法解析的行。
    返回格式与其他读取函数一致：
    {
        "<file_name>": {
            "data": [x_list, y_list],
            "columns_title": ["Positions", "Intensities"],
        }
    }
    """
    content = file_content.decode("utf-8", errors="ignore").splitlines()

    x_list = []
    intensities_list = []

    for line in content:
        stripped = line.strip()
        if not stripped:
            continue

        # 使用通用分隔符拆分（空格/制表符/逗号）
        parts = re.split(r'[,\s\t]+', stripped)
        parts = [p for p in parts if p]
        if len(parts) >= 2:
            try:
                x = round(float(parts[0]), 2)
                y = float(parts[1])
            except ValueError:
                # 非数值行（如头信息）跳过
                continue
            x_list.append(x)
            intensities_list.append(y)

    if not x_list or not intensities_list:
        raise ValueError(f"No valid numeric data found in {file_name}")

    max_intensity = max(intensities_list)
    if max_intensity <= 0:
        y_list = [0.0 for _ in intensities_list]
    else:
        y_list = [round((i * 100.0) / max_intensity, 1) for i in intensities_list]

    data = [x_list, y_list]
    return {
        f"{file_name}": {
            "data": data,
            "columns_title": ["Positions", "Intensities"],
        }
    }

def read_xy(file_name: str, file_content: bytes):
    content = file_content.decode("utf-8").split('\n')

    intensities_list = []
    x_list = []
    y_list = []

    for line in content:
        data = line.strip().split()
        if len(data) >= 2:
            x = round(float(data[0]), 2)
            y = float(data[1])
            x_list.append(x)
            intensities_list.append(y)

    max_intensity = max(intensities_list)
    for i in intensities_list:
        y_list.append(round(((i * 100.0) / max_intensity), 1))
    data = [x_list, y_list]
    return {
        f"{file_name}": {
            "data": data,
            "columns_title": ["Positions", "Intensities"],
        }
    }


def read_asc(file_name: str, file_content: bytes):
    content = file_content.decode("utf-8").split('\n')

    intensities_list = []
    x_list = []
    y_list = []

    for line in content:
        data = line.strip().split()
        if len(data) >= 2:
            x = round(float(data[0]), 2)
            y = float(data[1])
            x_list.append(x)
            intensities_list.append(y)

    max_intensity = max(intensities_list)
    for i in intensities_list:
        y_list.append(round(((i * 100.0) / max_intensity), 1))
    data = [x_list, y_list]
    return {
        f"{file_name}": {
            "data": data,
            "columns_title": ["Positions", "Intensities"],
        }
    }


def read_txt(file_name: str, file_content: bytes):
    content = file_content.decode("utf-8", errors="ignore").split('\n')

    intensities_list = []
    x_list = []
    y_list = []

    # 标记是否进入数据区（可选：某些格式有 [Data] 标记）
    in_data_section = False

    for line in content:
        stripped = line.strip()

        # 跳过空行
        if not stripped:
            continue

        # 跳过注释行（以 ; 或 # 开头）
        if stripped.startswith((';', '#')):
            continue

        # 跳过元数据段标记（以 [ 开头）
        if stripped.startswith('['):
            # 如果遇到 [Data]，标记进入数据区
            if 'Data' in stripped:
                in_data_section = True
            continue

        # 跳过包含 '=' 的键值对行（例如 Value=XRD）
        if '=' in stripped and not in_data_section:
            continue

        # 尝试解析为两列数值
        data = stripped.replace(',', ' ').split()
        if len(data) >= 2:
            try:
                x = round(float(data[0]), 2)
                y = float(data[1])
                x_list.append(x)
                intensities_list.append(y)
            except ValueError:
                # 无法转换为数值，跳过
                continue

    if not intensities_list:
        raise ValueError(f"No valid numeric data found in {file_name}")

    max_intensity = max(intensities_list)
    for i in intensities_list:
        y_list.append(round(((i * 100.0) / max_intensity), 1))

    data = [x_list, y_list]
    return {
        f"{file_name}": {
            "data": data,
            "columns_title": ["Positions", "Intensities"],
        }
    }

def read_mdi(file_name: str, file_content: bytes):
    """
    解析 MDI 文本格式：
    - 跳过首行日期等非数值行
    - 识别包含 header 的行（形如：start step ... N end ...）
      示例：3.000  0.02000  1.0  2850  60.000  2850
    - 后续行读取强度（多列整数/浮点），总数达到 N 或读到文件末尾
    - 用 start + i*step 生成 2θ，强度归一化到 0–100
    """
    text = file_content.decode("utf-8", errors="ignore")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    start = None
    step = None
    end = None
    N = None
    header_idx = None

    def to_float_list(s):
        parts = re.findall(r'[-+]?\d+(?:\.\d+)?', s.replace(',', ' '))
        vals = []
        for p in parts:
            try:
                vals.append(float(p))
            except Exception:
                pass
        return vals

    # 找 header
    for i, ln in enumerate(lines):
        nums = to_float_list(ln)
        if len(nums) >= 4:
            s0, st = nums[0], nums[1]
            n_candidate = int(round(nums[3]))
            end_candidate = nums[4] if len(nums) >= 5 else None
            if (0.0 <= s0 <= 180.0) and (0.0 < st <= 1.0) and (50 <= n_candidate <= 200000) and (end_candidate is None or 0.0 <= end_candidate <= 180.0):
                start, step, N, end, header_idx = s0, st, n_candidate, end_candidate, i
                break

    if header_idx is None or start is None or step is None:
        raise ValueError("MDI: 未找到有效的头信息（start/step/N）")

    # 收集强度
    intensities = []
    for ln in lines[header_idx + 1:]:
        for tok in re.split(r'[,\s\t]+', ln):
            if not tok:
                continue
            try:
                v = float(tok)
                intensities.append(v)
            except Exception:
                continue
        if N is not None and len(intensities) >= N:
            break

    if not intensities:
        raise ValueError("MDI: 未读取到有效强度数据")

    # 如果读取到的数量与 N 不一致，以实际数量为准
    if N is None or len(intensities) != N:
        N = len(intensities)

    # 生成 2θ
    x_list = [round(start + i * step, 2) for i in range(N)]
    y_raw = intensities[:N]

    m = max(y_raw) if y_raw else 0.0
    y_list = [0.0] * N if m <= 0 else [round((v * 100.0) / m, 1) for v in y_raw]

    return {
        f"{file_name}": {
            "data": [x_list, y_list],
            "columns_title": ["Positions", "Intensities"],
        }
    }

def parse_file(file_name: str, file_content: bytes):
    suffix = file_name.split(".")[-1]
    if suffix in ["xrdml", "XRDML", "Xrdml"]:
        return read_xrdml(file_name, file_content)
    elif suffix in ["xy", "XY"]:
        return read_xy(file_name, file_content)
    elif suffix in ["asc", "ASC"]:
        return read_asc(file_name, file_content)
    elif suffix in ['txt']:
        return read_txt(file_name, file_content)
    elif suffix in ['mdi', 'MDI']:
        return read_mdi(file_name, file_content)
    elif suffix in ['raw', 'RAW']:
        # 原逻辑优先
        try:
            return read_raw(file_name, file_content)
        except Exception as err:
            try:
                return _parse_binary_float_fallback(file_name, file_content)
            except Exception:
                raise err
    else:
        raise ValueError(f"Unsupported file type: {suffix}")

def parse_file(file_name: str, file_content: bytes):
    suffix = file_name.split(".")[-1]
    if suffix in ["xrdml", "XRDML", "Xrdml"]:
        return read_xrdml(file_name, file_content)
    elif suffix in ["xy", "XY"]:
        return read_xy(file_name, file_content)
    elif suffix in ["asc", "ASC"]:
        return read_asc(file_name, file_content)
    elif suffix in ['txt']:
        return read_txt(file_name, file_content)
    elif suffix in ['raw', 'RAW']:
        # 原逻辑优先
        try:
            return read_raw(file_name, file_content)
        except Exception as err:

            try:
                return _parse_binary_float_fallback(file_name, file_content)
            except Exception:
                raise err
    elif suffix in ['mdi', 'MDI']:
        return read_mdi(file_name, file_content)
    else:
        raise ValueError(f"Unsupported file type: {suffix}")


def pretreat(x, y):
    thetas = x
    test_list = y  # 纵轴
    v_test_list = []
    for i in range(len(test_list)):
        v0 = test_list[i]
        v00 = v0 + 1
        v1 = np.sqrt(v00)
        v2 = v1 + 1
        v3 = np.log(v2)
        v4 = v3 + 1
        v5 = np.log(v4)
        v_test_list.append(v5)

    n, m, c = len(test_list), 15, 0  # 15一般是峰区域的采样点数
    for p in range(1, m):
        w = []
        for i in range(p + 1, n - p):
            c1 = v_test_list[i]
            c2 = (v_test_list[i - p] + v_test_list[i + p]) / 2
            if c1 > c2 + c:
                v_test_list[i] = c2
            else:
                v_test_list[i] = c1
    dd = []
    for i in range(n):
        d = ((np.exp(np.exp(v_test_list[i]) - 1) - 1) ** 2 - 1)
        dd.append(d)

    new_test_list = []
    for k in range(n):
        a = test_list[k] - dd[k]
        new_test_list.append(a)
    do_new_test_list = []  # 去除背景之后的线
    for new_test_list_index in new_test_list:
        do_new_test_list.append(new_test_list_index)

    times = 11  # 用于迭代计算噪音范围值
    value_avg = 0
    value_std = 0
    for j in range(times):
        value_std = np.std(do_new_test_list)
        value_avg = np.average(do_new_test_list)
        a_del = []
        for i in range(len(do_new_test_list)):
            if do_new_test_list[i] - (value_avg + 3 * value_std) >= 0:
                a_del.append(i)
        a_del.reverse()
        for s in range(len(a_del)):
            del do_new_test_list[a_del[s]]
    baseline = []  # 0水平上的基线
    peak_standard = []  # 0水平上的峰值最低标准（标准差+3*均值）
    baselines = []  # 实际水平上的基线
    peaks_standard = []  # 实际水平上的峰值最低标准（标准差+3*均值）
    for k in range(len(new_test_list)):
        baseline.append(value_avg)
        peak_standard.append(value_avg + 3 * value_std)
        baselines.append(dd[k] + value_avg)
        peaks_standard.append(dd[k] + (value_avg + 3 * value_std))
    # --------------------------- #数据平滑#----------------------------------------------

    new5 = []
    for i in range(5, len(new_test_list) - 5):
        a5 = (new_test_list[i - 5] * 18 + new_test_list[i - 4] * -45 + new_test_list[i - 3] * -10 +
              new_test_list[
                  i - 2] * 60 + new_test_list[i - 1] * 120 + new_test_list[i] * 143 + new_test_list[
                  i + 1] * 120
              + new_test_list[i + 2] * 60 + new_test_list[i + 3] * -10 + new_test_list[i + 4] * -45 +
              new_test_list[
                  i + 5] * 18) / 429
        b5 = (test_list[i - 5] * -36 + test_list[i - 4] * 9 + test_list[i - 3] * 44 + test_list[
            i - 2] * 69 +
              test_list[i - 1] * 84 + test_list[i] * 89 + test_list[i + 1] * 84
              + test_list[i + 2] * 69 + test_list[i + 3] * 44 + test_list[i + 4] * 9 + test_list[
                  i + 5] * -36) / 429
        new5.append(a5)
    new5.insert(0, new_test_list[0])
    new5.insert(0, new_test_list[1])
    new5.insert(0, new_test_list[2])
    new5.insert(0, new_test_list[3])
    new5.insert(0, new_test_list[4])

    new7 = []
    for i in range(7, len(new_test_list) - 7):
        a7 = (new_test_list[i - 7] * -78 + new_test_list[i - 6] * -13 + new_test_list[i - 5] * 42 +
              new_test_list[i - 4] * 87 +
              new_test_list[i - 3] * 122 + new_test_list[i - 2] * 147 + new_test_list[i - 1] * 162 +
              new_test_list[i] * 167 +
              new_test_list[i + 1] * 162
              + new_test_list[i + 2] * 147 + new_test_list[i + 3] * 122 + new_test_list[i + 4] * 87 +
              new_test_list[i + 5] * 42 +
              new_test_list[i + 6] * -13 + new_test_list[i + 7] * -78) / 1105
        b7 = (new_test_list[i - 7] * 2145 + new_test_list[i - 6] * -2860 + new_test_list[i - 5] * -2937 +
              new_test_list[
                  i - 4] * -165 + new_test_list[i - 3] * 3755 + new_test_list[i - 2] * 7500 + new_test_list[
                  i - 1] * 10125 +
              new_test_list[i] * 11053 + new_test_list[i + 1] * 10125
              + new_test_list[i + 2] * 7500 + new_test_list[i + 3] * 3755 + new_test_list[i + 4] * -165 +
              new_test_list[
                  i + 5] * -2937 + new_test_list[i + 6] * -2860 + new_test_list[i + 7] * 2145) / 46189
        new7.append(a7)
    new7.insert(0, new_test_list[0])
    new7.insert(0, new_test_list[1])
    new7.insert(0, new_test_list[2])
    new7.insert(0, new_test_list[3])
    new7.insert(0, new_test_list[4])
    new7.insert(0, new_test_list[5])
    new7.insert(0, new_test_list[6])

    # -----------------------求一阶导数------------------------------------
    d0_new3 = []
    for i in range(3, len(new5) - 3):
        a3 = (new5[i - 3] * -3 + new5[i - 2] * -2 + new5[i - 1] * -1 + new5[i] * 0 + new5[i + 1] * 1 + new5[
            i + 2] * 2 + new5[i + 3] * 3) / 28
        b3 = (new5[i - 3] * 22 + new5[i - 2] * -67 + new5[i - 1] * -58 + new5[i] * 0 + new5[i + 1] * 58 +
              new5[
                  i + 2] * 67 + new5[i + 3] * 22) / 252
        d0_new3.append(a3)
    d0_new3.insert(0, 0)
    d0_new3.insert(0, 0)
    d0_new3.insert(0, 0)

    d_new5 = []
    for i in range(5, len(new7) - 5):
        a5 = (new7[i - 5] * -10530 + new7[i - 4] * 20358 + new7[i - 3] * 17082 + new7[i - 2] * 117 + new7[
            i - 1] * -15912 + new7[i] * -22230 + new7[i + 1] * -15912
              + new7[i + 2] * 117 + new7[i + 3] * 17082 + new7[i + 4] * 20358 + new7[
                  i + 5] * -10530) / 16731
        ay5 = (test_list[i - 5] * -10530 + test_list[i - 4] * 20358 + test_list[i - 3] * 17082 + test_list[
            i - 2] * 117 + test_list[i - 1] * -15912 + test_list[i] * -22230 + test_list[i + 1] * -15912
               + test_list[i + 2] * 117 + test_list[i + 3] * 17082 + test_list[i + 4] * 20358 + test_list[
                   i + 5] * -10530) / 16731
        b5 = (new7[i - 5] * 15 + new7[i - 4] * 6 + new7[i - 3] * -1 + new7[i - 2] * -6 + new7[i - 1] * -9 +
              new7[
                  i] * -10 + new7[i + 1] * -9
              + new7[i + 2] * -6 + new7[i + 3] * -1 + new7[i + 4] * 6 + new7[i + 5] * 15) / 429
        d_new5.append(b5)
    d_new5.insert(0, 0)
    d_new5.insert(0, 0)
    d_new5.insert(0, 0)
    d_new5.insert(0, 0)
    d_new5.insert(0, 0)

    p_00 = []  # 第一次筛选
    for k in range(1, len(d0_new3) - 1):
        if d0_new3[k - 1] > 0 and d0_new3[k] < 0:
            p_00.append(k)
    p_22 = []
    for l in range(1, len(d_new5) - 1):
        if d_new5[l] - d_new5[l - 1] < 0 and d_new5[l + 1] - d_new5[l] > 0:
            p_22.append(l)
    p1 = []
    p_y = []
    for i in p_22:
        if test_list[i] > peaks_standard[i]:
            p1.append(i)
            p_y.append(test_list[i])
    # 第3次
    list_11 = []
    for d_2 in p1:
        list_1 = []
        for n in range(100):
            if d_2 - n > 0:
                if d_new5[d_2 - n] > 0:
                    list_1.append(d_2 - n + 1)
                    break
        for nn in range(100):
            if d_2 + nn < len(d_new5):
                if d_new5[d_2 + nn] > 0:
                    list_1.append(d_2 + nn - 1)
                    break
        if len(list_1) == 2:
            list_11.append(list_1)

    cha_del = []
    for f0 in range(len(list_11)):

        cha = abs(list_11[f0][0] - list_11[f0][1])
        if cha <= 1.6:  # 面积参数
            cha_del.append(f0)
    cha_del.reverse()
    for f1 in range(len(cha_del)):
        del p1[cha_del[f1]]
        del list_11[cha_del[f1]]
    # 创建一个空列表，用于存储没有重复的子列表
    new_list = []
    # 创建一个空列表，用于存储被删除的子列表的索引值
    deleted_index = []
    # 遍历大列表中的每一个子列表
    for i in range(len(list_11)):
        # 如果当前子列表没有出现在之前的子列表中，则将其加入到新列表中
        if list_11[i] not in new_list:
            new_list.append(list_11[i])
        else:
            # 如果当前子列表已经在之前的子列表中出现过，则删除索引值大的那个子列表
            index = new_list.index(list_11[i])
            if i > index:
                deleted_index.append(i)
                new_list.pop(index)
                new_list.append(list_11[i])

    deleted_index.reverse()
    for i in deleted_index:
        del p1[i]

    zero = []
    for i in range(len(test_list)):
        zero.append(0)
    p_test_list = []
    for j in p1:
        p_test_list.append(test_list[j])
    p0_true = []
    step_size = thetas[1] - thetas[0]
    for k0_index, k0 in enumerate(p1):
        a = thetas[0] + step_size * k0
        p0_true.append(a)

    for kkk in range(len(p1)):
        if test_list[p1[kkk] - 1] > test_list[p1[kkk]]:
            p1[kkk] = p1[kkk] - 1

    for k_index, k in enumerate(p1):
        a = thetas[0] + step_size * k

    for xiuzheng in range(len(p1)):
        bz_value = []
        bz_index = []
        if p1[xiuzheng] > 15 and p1[xiuzheng] < (len(test_list) - 15):
            for xz in range(p1[xiuzheng] - 10, p1[xiuzheng] + 10):
                bz_value.append(test_list[xz])
                bz_index.append(xz)
            p1[xiuzheng] = bz_index[bz_value.index(max(bz_value))]  # 保证了峰值点不会错位。
    del_xiuzhenglist = []
    for xiuzhplus in range(len(p1)):
        if p1[xiuzhplus] > 2 and p1[xiuzhplus] < len(test_list) - 2:
            if test_list[p1[xiuzhplus] - 1] > test_list[p1[xiuzhplus]] or test_list[p1[xiuzhplus] + 1] > \
                    test_list[p1[xiuzhplus]]:
                del_xiuzhenglist.append(xiuzhplus)
    del_xiuzhenglist.sort(reverse=True)
    for j in del_xiuzhenglist:
        del p1[j]
    label_intensity = []
    for p in p1:
        label_intensity.append(test_list[p])
    x_theta = [x[i] for i in p1]

    return baselines, peaks_standard, x_theta, label_intensity, p1  # 实际基线，实际噪音线、真实峰值横坐标、真实峰值纵坐标、峰值索引。##


def find_closest_index(a, list1):
    min_diff = float('inf')
    closest_index = None
    for i, num in enumerate(list1):
        diff = abs(num - a)
        if diff < min_diff:
            min_diff = diff
            closest_index = i
    return closest_index


def calculate_fwhm(x_values, y_values, peak_positions, peak_intensities):
    fwhm_values = []
    for peak_position, peak_intensity in zip(peak_positions, peak_intensities):
        # 寻找峰值对应的索引
        peak_index = np.argmin(np.abs(x_values - peak_position))

        # 确定峰值的左右半高度点
        half_max_intensity = peak_intensity / 2.0
        left_index = np.where(y_values[:peak_index] < half_max_intensity)[0][-1]
        right_index = np.where(y_values[peak_index:] < half_max_intensity)[0][0] + peak_index

        # 计算半峰宽
        fwhm = x_values[right_index] - x_values[left_index]
        fwhm_values.append(fwhm)

    return fwhm_values


def analyze_data(name, data):
    file_name = name
    x_all = [data[file_name]['data'][0]]
    y_all = [data[file_name]['data'][1]]
    baselines_list = []
    peaks_standard_list = []
    x_theta_list = []
    label_intensity_list = []
    p1_list = []
    features = []

    for i in range(len(x_all)):
        baselines, peaks_standard, x_theta, label_intensity, p1 = pretreat(x_all[i], y_all[i])

        baselines_list.append(baselines)
        peaks_standard_list.append(peaks_standard)
        x_theta_list.append(x_theta)
        label_intensity_list.append(label_intensity)
        p1_list.append(p1)

        baseline_peak = []
        for m in range(len(x_theta)):
            closest_index = find_closest_index(x_theta[m], x_all[i])
            base_p = baselines[closest_index]
            baseline_peak.append(label_intensity[m] - base_p)

        new_y = []
        for p in range(len(x_all[i])):
            new_y.append(y_all[i][p] - baselines[p])

        fwhm_values = calculate_fwhm(np.array(x_all[i]), np.array(new_y), np.array(x_theta),
                                     np.array(baseline_peak))

        feature = []
        for q in range(len(x_theta)):
            x_theta[q] = round(x_theta[q], 2)
            label_intensity[q] = round(label_intensity[q], 1)
            fwhm_values[q] = round(fwhm_values[q], 2)
            feature.append([x_theta[q], label_intensity[q], fwhm_values[q]])

        features.append(feature)

    # 从原始数据中提取所需的值
    features_2theta = [[sublist[0] for sublist in sublist_list] for sublist_list in features]
    features_FWHM = [[sublist[2] for sublist in sublist_list] for sublist_list in features]

    features_jinglichicun = []
    for i in range(len(features_2theta)):
        sublist_result = []

        for j in range(len(features_2theta[i])):
            a = (0.89 * 0.15406) / (
                    (features_FWHM[i][j] * 3.14159 / 180) * (math.cos(features_2theta[i][j] * 3.14159 / 360)))
            sublist_result.append(round(a, 2))
        features_jinglichicun.append(sublist_result)

    # 创建一个新列表，避免修改原始列表
    new_features = [[[item for item in sub_list] for sub_list in inner_list] for inner_list in features]

    # 遍历list1和list2，将list2的元素添加到list1中对应位置的子列表中
    for i in range(len(features)):
        for j in range(len(features[i])):
            new_features[i][j].append(features_jinglichicun[i][j])

    x_list = x_all[0]
    y_list = y_all[0]
    baseline_list = baselines_list[0]
    peak_standard_list = peaks_standard_list[0]
    theta_list = x_theta_list[0]
    intensity_list = label_intensity_list[0]
    data_list = [x_list, y_list, baseline_list, peak_standard_list, theta_list, intensity_list]
    return {
        "data": data_list,
        "features": new_features[0],
    }


def SM(all_tab, xcc_label, ycc_label):  # 传入all，待测样本的峰值信息,传出排序后的索引
    FOM = []
    y_label = sorted(ycc_label, reverse=True)
    b_label = sorted(zip(ycc_label, range(len(ycc_label))))  # 降序排列
    b_label.sort(key=lambda label_intensity: label_intensity[0],
                 reverse=True)  # x[0]是因为在元组中，按a排序，a在第0位,这里的x不是前面的数组x，只是临时申请的变量
    theta_label = [label_x[1] for label_x in b_label]  # x[1]是因为在元组中，下标在第1位,c为列表元素的原始索引值。
    x_label = []  # 排名后的横轴坐标
    for thetas in theta_label:
        x_label.append(xcc_label[thetas])

    if len(x_label) >= 32:
        x_label = x_label[:32]
        y_label = y_label[:32]

    for paixu_index, paixu in enumerate(all_tab):
        xl = paixu[2]  # x值
        yl = paixu[3]  # y值
        xl_list = []
        yl_list = []

        if len(xl) == len(yl):
            for i in range(len(xl)):  # 字符串转float
                try:
                    xl_list.append(float(xl[i]))
                    yl_list.append(float(yl[i]))
                except:
                    continue
        else:
            ccc = len(xl) - len(yl)
            if ccc > 0:
                xl = xl[:(len(xl) - abs(ccc))]
            else:
                yl = yl[:(len(yl) - abs(ccc))]

            for i in range(len(xl)):  # 字符串转float
                try:
                    xl_list.append(float(xl[i]))
                    yl_list.append(float(yl[i]))
                except:
                    continue

        xl = list(dict.fromkeys(xl_list))
        yl = list(dict.fromkeys(yl_list))

        ys = sorted(yl, reverse=True)
        bs = sorted(zip(yl, range(len(yl))))  # 降序排列
        bs.sort(key=lambda yl: yl[0], reverse=True)  # x[0]是因为在元组中，按a排序，a在第0位,这里的x不是前面的数组x，只是临时申请的变量

        cs = [Insity_y[1] for Insity_y in bs]  # x[1]是因为在元组中，下标在第1位,c为列表元素的原始索引值。
        xs = []  # 排名后的横轴坐标
        for index_Is in cs:
            xs.append(xl[index_Is])

        if len(xs) >= 32:
            xs = xs[:32]
            ys = ys[:32]

        if len(x_label) >= len(xs):
            len_diff = len(x_label) - len(xs)  # 因为以lable为标准，所以需要补短，但是不需要去长
            for add in range(len(x_label) - len_diff, len(x_label)):
                xs.append(x_label[add] + 0.3)
                ys.append(0.001)
        else:
            xs = xs[:len(x_label)]
            ys = ys[:len(y_label)]

        ##算法3
        def calculate_similarity(exp_peaks, std_peaks, tolerance=0.2):
            exp_positions, exp_intensities = exp_peaks
            std_positions, std_intensities = std_peaks

            # 创建匹配向量
            match_exp_intensities = []
            match_std_intensities = []

            for i, exp_pos in enumerate(exp_positions):
                matched = False
                for j, std_pos in enumerate(std_positions):
                    if abs(exp_pos - std_pos) <= tolerance:
                        match_exp_intensities.append(exp_intensities[i])
                        match_std_intensities.append(std_intensities[j])
                        matched = True
                        break
                if not matched:
                    match_exp_intensities.append(exp_intensities[i])
                    match_std_intensities.append(0)

            # 处理未匹配的标准峰
            for j, std_pos in enumerate(std_positions):
                if not any(abs(exp_pos - std_pos) <= tolerance for exp_pos in exp_positions):
                    match_exp_intensities.append(0)
                    match_std_intensities.append(std_intensities[j])

            # 转换为numpy数组
            # match_exp_intensities = np.array(match_exp_intensities)
            # match_std_intensities = np.array(match_std_intensities)

            # 计算欧氏距离
            if np.any(match_exp_intensities) and np.any(match_std_intensities):
                distance = math.sqrt(sum((x - y) ** 2 for x, y in zip(match_exp_intensities, match_std_intensities)))
                similarity = 1 / (1 + distance)  # 将欧氏距离转换为相似度
            else:
                similarity = 0  # 无匹配时，相似度为0

            return similarity

        experiment_peaks = (np.array(x_label), np.array(y_label))
        st_data = (np.array(xs), np.array(ys))

        score = calculate_similarity(experiment_peaks, st_data, tolerance=0.2)
        FOM.append(score)

    # Step 1: 对 FOM 按降序排序
    Similarity_p = sorted(FOM, reverse=True)
    s = list(zip(FOM, range(len(FOM))))
    s.sort(key=lambda Similarity: Similarity[0], reverse=True)
    sc = [Similarity[1] for Similarity in s]
    Similarity_p_guiyi = []
    # max_Similarity_p = max(Similarity_p)
    # min_Similarity_p = min(Similarity_p)
    for i in Similarity_p:
        # a = ((i - min_Similarity_p) / (max_Similarity_p - min_Similarity_p)) * 100
        Similarity_p_guiyi.append(i)

    return sc, Similarity_p_guiyi


def many_picture(xy, name, all_tab):  # 批量处理图的代码
    names = [name]
    x_all, y_all = [xy[0]], [xy[1]]

    sub = []
    subs = []
    c = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j', 'k', 'l', 'm', 'n', ]
    for k in range(len(names)):  # 相当于是给后面的html民命，区别在于一个是检索对比的图，一个是寻峰展示的图
        ks = c[k]
        kss = c[k] + "1"
        sub.append(ks)
        subs.append(kss)  # 相当于
    features = []
    information_paixu_all = []
    xy_infor_all = []
    xccc_label = []
    yccc_label = []
    new_features = []
    for i_index, i in enumerate(x_all):

        def huitu(x, y, sample_name, ii):  # 横坐标，纵坐标，名字，索引。这部分执行完意味着寻峰工作结束
            xplot = []
            yplot = []  # 后面画图
            xmin = round(min(x), 2)  # 坐标系范围设置
            xmax = round(max(x), 2)
            interval = [[0, 10], [10, 20], [20, 30], [30, 40], [40, 50], [50, 60], [60, 70], [70, 80], [80, 90],
                        [90, 100],
                        [100, 110], [110, 120], [120, 130], [130, 140], [140, 150], [150, 160], [160, 170], [170, 180],
                        [180, 190], ]
            integer_begin = 0
            integer_after = 0
            for inter in interval:
                if xmin >= inter[0] and xmin < inter[1]:
                    integer_begin = inter[0]
                if xmax >= inter[0] and xmax < inter[1]:
                    integer_after = inter[1]
            if integer_after > 90:
                integer_after = 90
            for k in range(len(x)):
                xplot.append(x[k])
                yplot.append(y[k])
            x_baseline, x_peaks, xc_label, yc_label, p_index = pretreat(x, y)  # 实际基线，实际噪音线、真实峰值横坐标、真实峰值纵坐标、峰值索引。

            def find_closest_index(a, list1):
                min_diff = float('inf')
                closest_index = None
                for i, num in enumerate(list1):
                    diff = abs(num - a)
                    if diff < min_diff:
                        min_diff = diff
                        closest_index = i
                return closest_index

            baseline_peak = []
            for i in range(len(xc_label)):
                closest_index = find_closest_index(xc_label[i], x)
                base_p = x_baseline[closest_index]
                baseline_peak.append(yc_label[i] - base_p)

            new_y = []
            for i in range(len(x)):
                new_y.append(y[i] - x_baseline[i])

            def calculate_fwhm(x_values, y_values, peak_positions, peak_intensities):
                fwhm_values = []
                for peak_position, peak_intensity in zip(peak_positions, peak_intensities):
                    # 寻找峰值对应的索引
                    peak_index = np.argmin(np.abs(x_values - peak_position))

                    # 确定峰值的左右半高度点
                    half_max_intensity = peak_intensity / 2.0
                    left_index = np.where(y_values[:peak_index] < half_max_intensity)[0][-1]
                    right_index = np.where(y_values[peak_index:] < half_max_intensity)[0][0] + peak_index

                    # 计算半峰宽
                    fwhm = x_values[right_index] - x_values[left_index]
                    fwhm_values.append(fwhm)

                return fwhm_values

            fwhm_values = calculate_fwhm(np.array(x), np.array(new_y), np.array(xc_label),
                                         np.array(baseline_peak))

            # 将峰值信息保存在feature列表中，并且返回
            feature = []
            for i in range(len(xc_label)):
                xc_label[i] = round(xc_label[i], 1)
                yc_label[i] = round(yc_label[i], 1)
                fwhm_values[i] = round(fwhm_values[i], 2)
                feature.append([i, xc_label[i], yc_label[i], fwhm_values[i]])
            # =======================================================

            return feature, xc_label, yc_label, sample_name

        feature1, xcc_label, ycc_label, sample_name = huitu(x_all[i_index], y_all[i_index], names[i_index], i_index)

        xccc_label.append(xcc_label)
        yccc_label.append(ycc_label)
        features.append(feature1)  # 长度、横坐标、纵坐标
        sc, FOM_paixu = SM(all_tab, xcc_label, ycc_label)  # 计算相似分数

        information_paixu = []  # 排序后的相似度以及索引
        xy_infor = []
        xy_infor.append(all_tab[sc[0]][1])
        xy_infor.append(all_tab[sc[0]][2])
        xy_infor.append(all_tab[sc[0]][3])

        x_charts = [xy_infor[1]]
        y_charts = [xy_infor[2]]

        xy_infors = []
        for s in sc:
            xxyy = []
            xxyy.append(all_tab[s][0])
            xxyy.append(all_tab[s][1])
            xxyy.append(all_tab[s][2])
            xxyy.append(all_tab[s][3])
            xy_infors.append(xxyy)

        for suoyin_index, suoyin in enumerate(sc):
            information_every = []
            information_every.append(all_tab[suoyin][0])
            information_every.append(all_tab[suoyin][1])
            information_every.append(all_tab[suoyin][4])
            information_every.append(all_tab[suoyin][5])
            if FOM_paixu[suoyin_index] * 1000 < 50:
                information_every.append(round(FOM_paixu[suoyin_index] * 2000, 2))
            else:
                information_every.append(round(FOM_paixu[suoyin_index] * 1000, 2))
            # information_every.append(suoyin_index)
            information_paixu.append(information_every)
        information_paixu.insert(0, ["", sample_name, "", "", ""])
        information_paixu_all.append(information_paixu)
        xy_infor_all.append(xy_infors)

        x00 = []
        y00 = []
        for jj in range(len(x_charts)):
            x0 = []
            y0 = []
            for i in range(len(x_charts[jj])):
                xx = []
                yy = []
                xx.append(x_charts[jj][i])
                xx.append(x_charts[jj][i])
                yy.append(0)
                yy.append(y_charts[jj][i])
                x0.append(xx)
                y0.append(yy)
            x00.append(x0)
            y00.append(y0)

        # 从原始数据中提取所需的值
        features_2theta = [[sublist[1] for sublist in sublist_list] for sublist_list in features]
        features_FWHM = [[sublist[3] for sublist in sublist_list] for sublist_list in features]

        features_jinglichicun = []
        for i in range(len(features_2theta)):
            sublist_result = []

            for j in range(len(features_2theta[i])):
                a = (0.89 * 0.15406) / (
                        (features_FWHM[i][j] * 3.14159 / 180) * (math.cos(features_2theta[i][j] * 3.14159 / 360)))
                sublist_result.append(round(a, 2))
            features_jinglichicun.append(sublist_result)

        # 创建一个新的列表，避免修改原始列表
        new_features = [[[item for item in sub_list] for sub_list in inner_list] for inner_list in features]

        # 遍历list1和list2，将list2的元素添加到list1中对应位置的子列表中
        for i in range(len(features)):
            for j in range(len(features[i])):
                new_features[i][j].append(features_jinglichicun[i][j])

    return all_tab, information_paixu_all, xy_infor_all, names, sub, new_features, subs, x_all, y_all, xccc_label, yccc_label


def parse_chemical_formula(formula):
    import re
    from collections import defaultdict
    elements = defaultdict(int)
    pattern = re.compile(r'([A-Z][a-z]*)(\d*)')
    matches = pattern.findall(formula)
    for (element, count) in matches:
        if count == '':
            count = 1
        else:
            count = int(count)
        elements[element] += count
    return elements


def PTF(condition, all_data):
    def filter_chemicals(data, conditions):
        filtered_data = []

        for entry in data:
            chem_formula = entry[1]
            elements = parse_chemical_formula(chem_formula)

            # Check condition[3]: exclude chemicals containing any of these elements
            if any(elem in elements for elem in conditions[3]):
                continue

            # Check condition[2]: include only chemicals containing all of these elements
            if conditions[2] and not all(elem in elements for elem in conditions[2]):
                continue

            # Check condition[1]: include chemicals containing any of these elements
            if conditions[1] and not any(elem in elements for elem in conditions[1]):
                continue

            filtered_data.append(entry)
        return filtered_data

    filtered_data = filter_chemicals(all_data, condition)
    return filtered_data


def xuanze_deal(conditionlist, x_labels, y_labels, all, names):  # 选择进一步分析处理的代码

    new_all = PTF(conditionlist, all)  # 经过元素表筛选的新的列表集合
    # 计算相似度
    new_information_all = []

    for muti in range(len(x_labels)):
        x_label = x_labels[muti]
        y_label = y_labels[muti]
        sc, FOM_paixu = SM(new_all, x_label, y_label)
        new_information = []

        for s_index, s in enumerate(sc):
            new = []
            # new.append(s_index+1)
            new.append(new_all[s][0])
            new.append(new_all[s][6])
            new.append(new_all[s][4])
            new.append(new_all[s][5])
            if FOM_paixu[s_index] * 1000 < 50:
                new.append(round(FOM_paixu[s_index] * 2000, 2))
            else:
                new.append(round(FOM_paixu[s_index] * 1000, 2))
            new_information.append(new)
        new_information.insert(0, ["", names[muti], "", "", ""])
        new_information_all.append(new_information)

    return new_information_all


def get_info_from_db(database):
    all_pipei_results = []
    for data in database:
        all_pipei_results.append([data[0], data[-1], data[-3], data[-2]])
    return all_pipei_results


def fx_match(selected_rows, all_tab, result):
    formulas = []
    for i in selected_rows:
        ele = i.split('\n')[0]
        for idx, j in enumerate(all_tab):
            if ele.split()[2] == j[0]:
                formulas.append(idx)
    all_tab = [all_tab[idx] for idx in formulas]

    name_list = list(result.keys())
    score_list = []
    for key in name_list:
        x_all, y_all = [result[key]["data"][0]], [result[key]["data"][1]]
        names = ['None']
        sub = []
        subs = []
        c = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j', 'k', 'l', 'm', 'n', ]
        for k in range(len(names)):  # 相当于是给后面的html民命，区别在于一个是检索对比的图，一个是寻峰展示的图
            ks = c[k]
            kss = c[k] + "1"
            sub.append(ks)
            subs.append(kss)  # 相当于
        for i_index, i in enumerate(x_all):
            def huitu(x, y, sample_name, ii):  # 横坐标，纵坐标，名字，索引。这部分执行完意味着寻峰工作结束
                xplot = []
                yplot = []  # 后面画图
                xmin = round(min(x), 2)  # 坐标系范围设置
                xmax = round(max(x), 2)
                interval = [[0, 10], [10, 20], [20, 30], [30, 40], [40, 50], [50, 60], [60, 70], [70, 80], [80, 90],
                            [90, 100],
                            [100, 110], [110, 120], [120, 130], [130, 140], [140, 150], [150, 160], [160, 170],
                            [170, 180],
                            [180, 190], ]
                integer_begin = 0
                integer_after = 0
                for inter in interval:
                    if xmin >= inter[0] and xmin < inter[1]:
                        integer_begin = inter[0]
                    if xmax >= inter[0] and xmax < inter[1]:
                        integer_after = inter[1]
                if integer_after > 90:
                    integer_after = 90
                for k in range(len(x)):
                    xplot.append(x[k])
                    yplot.append(y[k])
                x_baseline, x_peaks, xc_label, yc_label, p_index = pretreat(x, y)  # 实际基线，实际噪音线、真实峰值横坐标、真实峰值纵坐标、峰值索引。

                def find_closest_index(a, list1):
                    min_diff = float('inf')
                    closest_index = None
                    for i, num in enumerate(list1):
                        diff = abs(num - a)
                        if diff < min_diff:
                            min_diff = diff
                            closest_index = i
                    return closest_index

                baseline_peak = []
                for i in range(len(xc_label)):
                    closest_index = find_closest_index(xc_label[i], x)
                    base_p = x_baseline[closest_index]
                    baseline_peak.append(yc_label[i] - base_p)

                new_y = []
                for i in range(len(x)):
                    new_y.append(y[i] - x_baseline[i])

                def calculate_fwhm(x_values, y_values, peak_positions, peak_intensities):
                    fwhm_values = []
                    for peak_position, peak_intensity in zip(peak_positions, peak_intensities):
                        # 寻找峰值对应的索引
                        peak_index = np.argmin(np.abs(x_values - peak_position))

                        # 确定峰值的左右半高度点
                        half_max_intensity = peak_intensity / 2.0
                        left_index = np.where(y_values[:peak_index] < half_max_intensity)[0][-1]
                        right_index = np.where(y_values[peak_index:] < half_max_intensity)[0][0] + peak_index

                        # 计算半峰宽
                        fwhm = x_values[right_index] - x_values[left_index]
                        fwhm_values.append(fwhm)

                    return fwhm_values

                fwhm_values = calculate_fwhm(np.array(x), np.array(new_y), np.array(xc_label),
                                             np.array(baseline_peak))

                # 将峰值信息保存在feature列表中，并且返回
                feature = []
                for i in range(len(xc_label)):
                    xc_label[i] = round(xc_label[i], 1)
                    yc_label[i] = round(yc_label[i], 1)
                    fwhm_values[i] = round(fwhm_values[i], 2)
                    feature.append([i, xc_label[i], yc_label[i], fwhm_values[i]])
                # =======================================================

                return feature, xc_label, yc_label, sample_name

            feature1, xcc_label, ycc_label, sample_name = huitu(x_all[i_index], y_all[i_index], names[i_index], i_index)
            sc, FOM_paixu = SM(all_tab, xcc_label, ycc_label)
            score_list += FOM_paixu

    combined_list = list(zip(name_list, score_list))
    sorted_combined_list = sorted(combined_list, key=lambda x: x[1], reverse=True)  # reverse=True 表示降序排列
    name_list_sorted, score_list_sorted = zip(*sorted_combined_list)
    result_list = []
    for ind in range(len(name_list_sorted)):
        if score_list_sorted[ind] * 1000 < 50:
            result_list.append([ind, name_list_sorted[ind], round(score_list_sorted[ind] * 2000, 2)])
        else:
            result_list.append([ind, name_list_sorted[ind], round(score_list_sorted[ind] * 1000, 2)])
    result_list = pd.DataFrame(result_list, columns=["No.", "Name", "Importance"])
    return result_list, all_tab


XRD_DATA = get_data_from_database()
