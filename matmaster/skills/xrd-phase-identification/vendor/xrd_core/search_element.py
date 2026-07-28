from .vis import XRDVis
from .parse import (
    parse_file,
    analyze_data,
    many_picture,
    xuanze_deal,
    PTF,
    XRD_DATA,
    chemistry_subscript,
    get_info_from_db,
    fx_match,
)
from .results import XRDResult
import pandas as pd

def deal_result(df):
    df = df[df["Importance"] != ""].copy()  # 过滤掉空值行
    df["Importance"] = df["Importance"].astype(float)
    first_value = df["Importance"].max()
    if first_value > 40:
        increase = 90 - int(first_value) + (int(first_value) % 10)
    elif 30 < first_value <= 40:
        increase = 80 - int(first_value) + (int(first_value) % 10)
    elif 20 < first_value <= 30:
        increase = 70 - int(first_value) + (int(first_value) % 10)
    elif 10 < first_value <= 20:
        increase = 60 - int(first_value) + (int(first_value) % 10)
    else:
        increase = 50 - int(first_value) + (int(first_value) % 10)

    df["Importance"] = round(df["Importance"] + increase, 2)
    return df

# 可装饰缓存器
def search_in_database(chem, xy, file_name):
    all_tab = XRD_DATA
    if not chem[0] and not chem[1] and not chem[2]:
        (
            all_zt,
            information_paixu_all,
            xy_infor_all,
            names,
            sub,
            features,
            subs,
            x_all,
            y_all,
            xccc_label,
            yccc_label,
        ) = many_picture(xy, file_name, all_tab)
        column_names = [
            "Reference code",
            "Chemical formula",
            "Crystal system",
            "Space group",
            "Importance",
        ]
        all_pipei_results = pd.DataFrame(
            information_paixu_all[0][:20], columns=column_names
        )
        all_pipei_results = all_pipei_results.map(
            lambda x: f"{x:.4g}" if isinstance(x, float) else x
        )
        cols = list(all_pipei_results.columns)
        cols.insert(1, cols.pop(-1))
        all_pipei_results = all_pipei_results[cols]
    else:
        all_tab = PTF(chem, all_tab)
        (
            all_zt,
            information_paixu_all,
            xy_infor_all,
            names,
            sub,
            features,
            subs,
            x_all,
            y_all,
            xccc_label,
            yccc_label,
        ) = many_picture(xy, file_name, all_tab)
        new_paixu = xuanze_deal(chem, xccc_label, yccc_label, all_zt, names)
        column_names = [
            "Reference code",
            "Chemical formula",
            "Crystal system",
            "Space group",
            "Importance",
        ]
        all_pipei_results = pd.DataFrame(new_paixu[0][:20], columns=column_names)
        # 格式化浮点数，保留原始小数位数
        all_pipei_results = all_pipei_results.map(
            lambda x: f"{x:.4g}" if isinstance(x, float) else x
        )
        cols = list(all_pipei_results.columns)
        cols.insert(1, cols.pop(-1))
        all_pipei_results = all_pipei_results[cols]
    all_pipei_results = deal_result(all_pipei_results)
    return all_pipei_results, all_tab, x_all, y_all

def selected_row_chart_option(index_list, all_pipei_results, all_tab, x_all, y_all,file_name ):
    selected_rows = []
    for record in index_list:
        index = record
        if index >= 0:
            selected_rows.append(str(all_pipei_results.iloc[index]))

    if not selected_rows:
        selected_rows.append(str(all_pipei_results.iloc[0]))
    options = XRDVis.pipei_results_see(
        selected_rows, all_tab, x_all, y_all, [file_name], 0
    )
    return options

async def search_elements(chem, xy, file_name, result: XRDResult, key_unique,index_list=[],curve_index=[]):
    if key_unique != "fx":
        # 正向检索
        all_pipei_results, all_tab, x_all, y_all = search_in_database(chem, xy, file_name)

        if len(all_pipei_results) > 0:
            selected_rows = [str(all_pipei_results.iloc[1])]
            options = XRDVis.pipei_results_see(
                selected_rows, all_tab, x_all, y_all, [file_name], 0
            )
            name = all_pipei_results["Chemical formula"].iloc[1]
            name = "".join(chemistry_subscript(name))
            await result.add_search_result(name, chem, options)

        # 在Streamlit中展示表格
        columns = list(all_pipei_results.columns)
        table = all_pipei_results.to_dict(orient="records")
        options = selected_row_chart_option(index_list, all_pipei_results, all_tab, x_all, y_all,file_name)
        chart_key = f"{key_unique}-search-chart-{index_list}"
        return table, columns, options, chart_key
    else:
        # 反向匹配
        all_tab = XRD_DATA
        if chem[0] or chem[1] or chem[2]:
            all_tab = PTF(chem, all_tab)
        all_pipei_results = get_info_from_db(all_tab)
        column_names = [
            "Reference code",
            "Chemical formula",
            "Crystal system",
            "Space group",
        ]
        all_pipei_results = pd.DataFrame(
            all_pipei_results, columns=column_names
        )
        all_pipei_results = all_pipei_results.map(
            lambda x: f"{x:.4g}" if isinstance(x, float) else x
                )

        # 返回第一张表格
        First_table_data = all_pipei_results
        First_key = "XRD-search-results-list-table-fx_" + key_unique
        if index_list == []:
        # 第一张图
            return First_table_data.to_dict(orient="records"), First_key
        # Index 选了谱图
        selected_rows = []
        print(index_list)
        for record in index_list:
            if record >=  0:
                selected_rows.append(str(all_pipei_results.iloc[record]))

        selected_rows_fx = selected_rows
        if selected_rows_fx is not None:
            result_list_show, all_tab_show = fx_match(selected_rows_fx, all_tab, result)
            result_list_show = result_list_show.map(
                lambda x: f"{x:.4g}" if isinstance(x, float) else x
            )
            result_list_show = deal_result(result_list_show)
            second_key = "XRD-search-results-list-table-fx-new_" + key_unique
            if curve_index == []:
                # 返回第二张表
                return result_list_show.to_dict(orient="records"), second_key

            selected_rows_1 = []

            for record_1 in curve_index:
                index_1 = record_1
                if index_1 >= 0:
                    selected_rows_1.append(str(result_list_show.iloc[index_1]))

            selected_rows_fx_1 = selected_rows_1
            if selected_rows_fx_1 is not None:
                options_fx = XRDVis.pipei_results_see_fx(
                    selected_rows_fx_1, all_tab_show, result
                )
                # 返回第三张图
                return options_fx,"echarts_" + key_unique
