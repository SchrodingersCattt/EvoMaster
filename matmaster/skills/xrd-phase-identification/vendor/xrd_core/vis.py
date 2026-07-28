import math
from typing import Optional, List, Dict, Any, Union
from copy import deepcopy
from .results import XRDResult

# 原始设计画布高度（像素）
_BASE_CANVAS_HEIGHT = 4923.0
# 目标前端画布高度（像素）
_TARGET_CANVAS_HEIGHT = 400.0
# 缩放因子（仅用于给前端提供建议宽高，不再用于字号/线宽缩放）
_SCALE = _TARGET_CANVAS_HEIGHT / _BASE_CANVAS_HEIGHT

# 原始样式常量
_ORIG_STYLE = {
    "font_size_title": 36,
    "font_size_label": 34,
    "font_size_legend": 32,
    "font_family": "Arial, 黑体, sans-serif",
    "colors": [
        "#9BA098",
        "#C9AF94",
        "#A6A8BC",
        "#B5A896",
        "#8E9AAF",
        "#D4A5A5",
    ],
    "line_width": 2.5,
    "axis_line_width": 3,
    "symbol_size": 10,
    "peak_color": "#2F4F4F",
    "baseline_color": "#A6A8BC",
    "sample_color": "#D4756E",
    "chart_width": 1200,
    "chart_height": 900
}

# 全局样式（固定为 400px 容器下的视觉规格）
STYLE_CONFIG = {
    "font_size_title": 22,          # 轴名/标题字号
    "font_size_label": 20,          # 坐标刻度字号
    "font_size_legend": 22,         # 图例字号
    "font_family": "Arial, 黑体, sans-serif",
    "colors": _ORIG_STYLE["colors"],
    "line_width": 3.0,              # 曲线粗细
    "axis_line_width": 2.0,         # 坐标轴线粗
    "symbol_size": 6,               # 峰点大小
    "peak_color": _ORIG_STYLE["peak_color"],
    "baseline_color": _ORIG_STYLE["baseline_color"],
    "sample_color": _ORIG_STYLE["sample_color"],
    "chart_width": int(round(_ORIG_STYLE["chart_width"] * _SCALE)),
    "chart_height": int(round(_TARGET_CANVAS_HEIGHT))
}

def _nice_ticks_for_sample_range(data_min: float, data_max: float) -> tuple:
    """
    根据主样品的扫描范围，计算 ECharts x 轴的 min/max/interval。
    - x 轴最小值下取整到 0（不小于 0）
    - x 轴最大值上取整到 5 的倍数
    - 主刻度间隔取 5 或 10（保持约 5-7 个刻度）
    返回 (axis_min, axis_max, interval)
    """
    if data_max < data_min:
        data_min, data_max = data_max, data_min

    # 下限不小于 0，上限向上取整到最近的 5 的倍数
    axis_min = max(0, int(math.floor(data_min)))
    axis_max = int(math.ceil(data_max))
    # 归并到 5 的倍数
    axis_min = (axis_min // 5) * 5
    axis_max = ((axis_max + 4) // 5) * 5  # 向上取到 5 的倍数

    # 选择间隔：优先 10，若刻度数过少则用 5
    span = axis_max - axis_min
    if span <= 0:
        return axis_min, axis_min + 5, 5

    # 试用 10 间隔
    interval = 10
    ticks = (span // interval) + 1
    if ticks < 5:  # 刻度太少，降低到 5
        interval = 5
        ticks = (span // interval) + 1
    elif ticks > 7:  # 刻度太多，提高到 20（极端情况）
        interval = 20
        ticks = (span // interval) + 1
        if ticks < 5:
            interval = 10

    # 防止 axis_min 超出数据下界太多：如样品最小>0但我们用了 0，不调整以保持规则示例
    return axis_min, axis_max, interval

def calculate_axis_interval(data_min: float, data_max: float, target_ticks: int = 6) -> tuple:
    """
    保留旧接口，但内部使用主样品范围规则（5 的倍数，约 5-7 个刻度）。
    """
    return _nice_ticks_for_sample_range(data_min, data_max)

class BaseChart:
    # ...existing code...
    def set_x_axis_range(self, x_data: List[float]):
        if not x_data:
            return
        x_min, x_max = min(x_data), max(x_data)
        axis_min, axis_max, interval = _nice_ticks_for_sample_range(x_min, x_max)
        self.option["xAxis"]["min"] = axis_min
        self.option["xAxis"]["max"] = axis_max
        self.option["xAxis"]["interval"] = interval

class BaseChart:
    def __init__(self, title: str = ""):
        self.title = title
        self.option = self._get_base_option()

    def _get_base_option(self) -> Dict[str, Any]:
        return {
            "title": {
                "text": self.title,
                "left": "left",
                "top": 8,
                "textStyle": {
                    "fontFamily": STYLE_CONFIG["font_family"],
                    "fontSize": STYLE_CONFIG["font_size_title"],
                    "fontWeight": "normal"
                }
            },
            "tooltip": {
                "trigger": "axis",
                "axisPointer": {"type": "cross"},
                "textStyle": {"fontFamily": STYLE_CONFIG["font_family"]}
            },
            "legend": {
                "data": [],
                "top": 8,
                "textStyle": {
                    "fontSize": STYLE_CONFIG["font_size_legend"],
                    "fontFamily": STYLE_CONFIG["font_family"]
                },
            },
            "grid": {
                "top": 48,
                "bottom": 48,
                "left": 64,
                "right": 32,
                "containLabel": False,
                "show": True,
                "borderWidth": STYLE_CONFIG["axis_line_width"],
                "borderColor": "#000"
            },
            "xAxis": {
                "name": "2θ (degree)",
                "type": "value",
                "nameLocation": "center",
                "nameGap": 26,
                "nameTextStyle": {
                    "fontSize": STYLE_CONFIG["font_size_title"],
                    "fontFamily": STYLE_CONFIG["font_family"],
                    "fontWeight": "normal"
                },
                "axisLine": {
                    "show": True,
                    "lineStyle": {
                        "color": "#000",
                        "width": STYLE_CONFIG["axis_line_width"]
                    }
                },
                "axisTick": {
                    "show": True,
                    "lineStyle": {
                        "color": "#000",
                        "width": STYLE_CONFIG["axis_line_width"]
                    }
                },
                "minorTick": {
                    "show": True,
                    "splitNumber": 2,
                    "length": 4,
                    "lineStyle": {
                        "color": "#000",
                        "width": STYLE_CONFIG["axis_line_width"] * 0.6
                    }
                },
                "axisLabel": {
                    "fontSize": STYLE_CONFIG["font_size_label"],
                    "fontFamily": STYLE_CONFIG["font_family"],
                    "color": "#000",
                    "showMinLabel": True,
                    "showMaxLabel": True
                },
                "splitLine": {"show": False},
                "min": None,
                "max": None,
                "interval": None,
                "z": 40
            },
            "yAxis": {
                "name": "Intensity (a.u.)",
                "type": "value",
                "nameLocation": "center",
                "nameGap": 10,
                "nameTextStyle": {
                    "fontSize": STYLE_CONFIG["font_size_title"],
                    "fontFamily": STYLE_CONFIG["font_family"],
                    "fontWeight": "normal"
                },
                "axisLine": {
                    "show": True,
                    "lineStyle": {
                        "color": "#000",
                        "width": STYLE_CONFIG["axis_line_width"]
                    }
                },
                "axisTick": {"show": False},
                "axisLabel": {"show": False},
                "splitLine": {"show": False},
                "scale": False,
                "min": 0,
                "max": None,
                "z": 40
            },
            "series": [],
            "toolbox": {
                "feature": {
                    "dataZoom": {"yAxisIndex": "none"},
                    "restore": {},
                    "saveAsImage": {}
                }
            },
            "color": STYLE_CONFIG["colors"]
        }

    def set_x_axis_range(self, x_data: List[float]):
        if not x_data:
            return
        x_min, x_max = min(x_data), max(x_data)
        axis_min, axis_max, interval = calculate_axis_interval(x_min, x_max, target_ticks=6)
        self.option["xAxis"]["min"] = axis_min
        self.option["xAxis"]["max"] = axis_max
        self.option["xAxis"]["interval"] = interval

    def set_y_axis_padding(self, y_data: List[List], padding_ratio: float = 0.05):
        y_values = [y for _, y in y_data if y is not None]
        if not y_values:
            return
        y_max = max(y_values)
        self.option["yAxis"]["min"] = 0
        self.option["yAxis"]["max"] = y_max * (1 + padding_ratio)

    def add_series(self, series_config: Dict[str, Any]):
        # 确保曲线图层在坐标轴下（轴 z=40，series 使用更低 z/zlevel）
        series_config.setdefault("zlevel", -1)
        series_config.setdefault("z", 0)
        if series_config.get("type") == "line":
            ls = series_config.setdefault("lineStyle", {})
            ls.setdefault("width", STYLE_CONFIG["line_width"])
            ls.setdefault("color", ls.get("color", STYLE_CONFIG["sample_color"]))
            series_config["lineStyle"] = ls
            series_config.setdefault("symbol", "none")
        if series_config.get("type") == "scatter":
            series_config.setdefault("symbolSize", STYLE_CONFIG["symbol_size"])
        self.option["series"].append(series_config)
        if "name" in series_config:
            self.option["legend"]["data"].append(series_config["name"])

    def set_y_axis_name(self, name: str):
        self.option["yAxis"]["name"] = name

    def get_option(self) -> Dict[str, Any]:
        return deepcopy(self.option)

class XRDVis:
    def __init__(self, data: Union[XRDResult, Dict]) -> None:
        self.data = data

    def get_default_echart_option(self, data, remove_baseline):
        data = data['data'] if isinstance(data, dict) else data.data
        x_data = data[0]
        y_data1 = data[1]
        y_data2 = data[2]

        line_data1 = [[x, y] for x, y in zip(x_data, y_data1)]
        line_data2 = [[x, y] for x, y in zip(x_data, y_data2)]
        scatter_data = [[x, y] for x, y in zip(data[4], data[5])]

        if remove_baseline != 'Non_removal baseline':
            line_data1 = [[x, y - b] for (x, y), b in zip(zip(x_data, y_data1), y_data2)]
            scatter_data = [[x, y - y_data2[x_data.index(x)]] for x, y in zip(data[4], data[5]) if x in x_data]

        chart = BaseChart(title=" ")
        chart.set_x_axis_range(x_data)

        all_y_data = line_data1.copy()
        if remove_baseline == 'Non_removal baseline':
            all_y_data.extend(line_data2)
        all_y_data.extend(scatter_data)
        chart.set_y_axis_padding(all_y_data, padding_ratio=0.05)

        chart.add_series({
            "name": "Sample data",
            "type": "line",
            "data": line_data1,
            "symbol": "none",
            "lineStyle": {"width": STYLE_CONFIG["line_width"], "color": STYLE_CONFIG["sample_color"]}
        })

        if remove_baseline == 'Non_removal baseline':
            chart.add_series({
                "name": "Baseline",
                "type": "line",
                "data": line_data2,
                "symbol": "none",
                "lineStyle": {"width": STYLE_CONFIG["line_width"], "color": STYLE_CONFIG["baseline_color"]}
            })

        chart.add_series({
            "name": "Peaks",
            "type": "scatter",
            "data": scatter_data,
            "symbolSize": STYLE_CONFIG["symbol_size"],
            "itemStyle": {"color": STYLE_CONFIG["peak_color"]}
        })

        return chart.get_option()

    def get_echart_option(self, remove_baseline: str) -> Optional[dict]:
        return self.get_default_echart_option(self.data, remove_baseline)

    @classmethod
    def pipei_results_see(cls, selected_rows, all_tab, x_all, y_all, name_list, choose_index):
        choose_stan_x = []
        choose_stan_y = []
        formulas = []

        for i in selected_rows:
            ele = i.split('\n')[0]
            for j in all_tab:
                if ele.split()[2] == j[0]:
                    choose_stan_x.append([float(j2) for j2 in j[2] if j2 != "None" and j2 is not None])
                    choose_stan_y.append([float(j3) for j3 in j[3] if j3 != "None" and j3 is not None])
                    formulas.append(j[-1])

        if choose_stan_x:
            x_combined = sorted(set().union(*choose_stan_x, x_all[choose_index]))
        else:
            x_combined = sorted(list(set(x_all[choose_index])))

        y_mapped = []
        for stan_x, stan_y in zip(choose_stan_x, choose_stan_y):
            mapped_y = [[x, stan_y[stan_x.index(x)]] if x in stan_x else [x, None] for x in x_combined]
            y_mapped.append(mapped_y)

        line_mapped_y = [[x, y_all[choose_index][x_all[choose_index].index(x)]] if x in x_all[choose_index] else [x, None] for x in x_combined]

        chart = BaseChart(title="Phase Identification Results")
        chart.set_x_axis_range(x_combined)

        all_y_data = line_mapped_y.copy()
        for y_data in y_mapped:
            all_y_data.extend(y_data)
        chart.set_y_axis_padding(all_y_data, padding_ratio=0.05)

        for idx, y_data in enumerate(y_mapped):
            chart.add_series({
                "name": f"{formulas[idx]}",
                "type": "bar",
                "data": y_data,
                "barWidth": "2"
            })

        chart.add_series({
            "name": name_list[choose_index],
            "type": "line",
            "data": line_mapped_y,
            "symbol": "none",
            "lineStyle": {
                "width": STYLE_CONFIG["line_width"],
                "color": STYLE_CONFIG["sample_color"]  # 样品曲线颜色
            },
            "connectNulls": True
        })

        return chart.get_option()

    @classmethod
    def get_nav_search_result(cls, echarts_option: dict):
        echarts_option = deepcopy(echarts_option)
        echarts_option["title"] = {}
        echarts_option["silent"] = True
        echarts_option["tooltip"] = {"show": False}
        echarts_option["grid"] = {"top": 10, "left": 25, "bottom": 20}
        echarts_option["xAxis"].update({"name": "", "axisLabel": {"fontSize": 8}})
        echarts_option["yAxis"].update({"name": "", "axisLabel": {"fontSize": 8}})
        return echarts_option

    @classmethod
    def pipei_results_see_fx(cls, selected_rows, all_tab, result):
        name_list = list(result.keys())
        x_all = []
        y_all = []
        formulas = []

        # 选中样品（主样品）数据
        for i in selected_rows:
            ele = i.split('\n')[1]
            for j in name_list:
                if ele.replace("Name          ", "") == j:
                    x_all = [float(j2) for j2 in result[j]["data"][0] if j2 != "None" and j2 is not None]
                    y_all = [float(j3) for j3 in result[j]["data"][1] if j3 != "None" and j3 is not None]
                    formulas.append(j)

        # 数据库对照（参考）曲线，仅用于叠加显示，不影响坐标范围
        choose_stan_x = [[float(j2) for j2 in all_tab[0][2] if j2 != "None" and j2 is not None]]
        choose_stan_y = [[float(j3) for j3 in all_tab[0][3] if j3 != "None" and j3 is not None]]

        # 坐标范围严格取主样品扫描范围（不受参考曲线影响）
        chart = BaseChart(title=f"Reverse Match: {all_tab[0][0]}")
        chart.set_y_axis_name("Intensity(%)")
        chart.set_x_axis_range(x_all)  # 使用主样品的原始 x_all 计算范围与刻度

        # 主样品线条按原始序列生成（不填充 null，不扩展长度）
        line_mapped_y = [[x, y] for x, y in zip(x_all, y_all)]

        # 参考曲线按主样品 x_all 映射；超出主样品范围的点不加入
        y_mapped = []
        stan_x = choose_stan_x[0] if choose_stan_x else []
        stan_y = choose_stan_y[0] if choose_stan_y else []
        if stan_x and stan_y:
            ref_points = []
            for x in x_all:
                if x in stan_x:
                    ref_points.append([x, stan_y[stan_x.index(x)]])
                # 如果参考库不包含该 x，则跳过（不填充 None），保证长度与主样品一致或更短
            y_mapped.append(ref_points)

        # y 轴上限用主样品值计算，避免参考棒图抬高范围
        chart.set_y_axis_padding(line_mapped_y, padding_ratio=0.05)

        # 绘制参考曲线（bar），颜色与样式独立，仅在主样品范围内显示
        for idx, y_data in enumerate(y_mapped):
            chart.add_series({
                "name": f"{all_tab[0][0]}",
                "type": "bar",
                "data": y_data,
                "barWidth": "2%"
            })

        # 绘制主样品线条（line），使用样品颜色
        chart.add_series({
            "name": formulas[0] if formulas else "Sample",
            "type": "line",
            "data": line_mapped_y,
            "symbol": "none",
            "lineStyle": {
                "width": STYLE_CONFIG["line_width"],
                "color": STYLE_CONFIG["sample_color"]
            },
            "connectNulls": False  # 不连接缺失点，保持真实范围
        })

        return chart.get_option()
