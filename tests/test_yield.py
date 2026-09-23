"""POST /api/v1/stoichiometry/yield 的 HTTP API 测试。

测试反应均选用其它测试文件未提交过的方程式（H2S 燃烧、SO3 合成、
Li 氧化、葡萄糖发酵等），避免幂等缓存造成跨文件的顺序依赖。
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

PATH = "/api/v1/stoichiometry/yield"


def _body(
    reactants,
    products,
    amounts,
    actual_yields,
):
    return {
        "reactants": reactants,
        "products": products,
        "amounts": [{"species": s, "moles": m} for s, m in amounts],
        "actual_yields": [{"species": s, "moles": m} for s, m in actual_yields],
    }


# 2H2S + 3O2 -> 2SO2 + 2H2O
H2S_BODY = dict(
    reactants=["H2S", "O2"],
    products=["SO2", "H2O"],
)


# ------------------------------------------------------------ 成功路径
def test_basic_yields_with_limiting_reactant():
    # 2 mol H2S + 4 mol O2 -> ξ=1（H2S 限量），两种产物理论各 2 mol
    resp = client.post(
        PATH,
        json=_body(
            ["H2S", "O2"], ["SO2", "H2O"],
            [("H2S", "2"), ("O2", "4")],
            [("SO2", "1.5"), ("H2O", "2")],
        ),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["reaction_extent"] == {"value": "1", "unit": "mol"}
    assert data["limiting_reactants"] == ["H2S"]
    assert data["normalized"]["equation"] == "2H2S + 3O2 -> 2H2O + 2SO2"

    products = {p["normalized"]: p for p in data["products"]}
    so2 = products["SO2"]
    assert so2["coefficient"] == 2
    assert so2["theoretical_yield"] == "2"
    assert so2["actual_yield"] == "1.5"
    assert so2["yield_percent"] == "75"
    assert so2["yield_percent_reason"] is None

    water = products["H2O"]
    assert water["theoretical_yield"] == "2"
    assert water["actual_yield"] == "2"
    assert water["yield_percent"] == "100"
    assert water["yield_percent_reason"] is None

    # 所有十进制数值均以字符串返回
    assert isinstance(so2["yield_percent"], str)


def test_yields_above_100_percent_are_preserved():
    # 实际产量高于理论值：不做截断，保留 > 100% 的收率
    resp = client.post(
        PATH,
        json=_body(
            ["H2S", "O2"], ["SO2", "H2O"],
            [("H2S", "2"), ("O2", "4")],
            [("SO2", "2.4"), ("H2O", "2.02")],
        ),
    )
    assert resp.status_code == 200
    products = {p["normalized"]: p for p in resp.json()["products"]}
    assert products["SO2"]["yield_percent"] == "120"
    assert products["H2O"]["yield_percent"] == "101"
    assert all(p["yield_percent_reason"] is None for p in resp.json()["products"])


def test_zero_feed_yields_null_percent_with_reason():
    # 投料全部为 0 -> ξ=0 -> 理论产量为 0，收率不可计算，返回 null + 原因
    resp = client.post(
        PATH,
        json=_body(
            ["H2S", "O2"], ["SO2", "H2O"],
            [("H2S", "0"), ("O2", "0")],
            [("SO2", "0"), ("H2O", "0.1")],
        ),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["reaction_extent"]["value"] == "0"
    assert data["limiting_reactants"] == ["H2S", "O2"]
    for product in data["products"]:
        assert product["theoretical_yield"] == "0"
        assert product["yield_percent"] is None
        assert product["yield_percent_reason"]
        assert "理论产量为 0" in product["yield_percent_reason"]
        assert "ξ = 0" in product["yield_percent_reason"]
    # 实际产量仍如实回显，即便理论值为 0
    actuals = {p["normalized"]: p["actual_yield"] for p in data["products"]}
    assert actuals == {"H2O": "0.1", "SO2": "0"}


def test_partial_zero_feed_only_zero_theoretical_products_are_null():
    # 只有部分反应物投料为 0 时 ξ 仍为 0，所有生成物理论产量都为 0
    resp = client.post(
        PATH,
        json=_body(
            ["H2S", "O2"], ["SO2", "H2O"],
            [("H2S", "3"), ("O2", "0")],
            [("SO2", "0"), ("H2O", "0")],
        ),
    )
    data = resp.json()
    assert data["reaction_extent"]["value"] == "0"
    assert data["limiting_reactants"] == ["O2"]
    assert all(p["yield_percent"] is None for p in data["products"])


def test_repeating_decimal_yield_percent_is_exact_string():
    # Fe2O3 + 3CO -> 2Fe + 3CO2；各投 1 mol -> CO 限量，ξ=1/3：
    # Fe 理论 2/3 mol；实际 0.1 -> 0.1/(2/3)*100 = 15（有限小数精确值）
    resp = client.post(
        PATH,
        json=_body(
            ["Fe2O3", "CO"], ["Fe", "CO2"],
            [("Fe2O3", "1"), ("CO", "1")],
            [("Fe", "0.1"), ("CO2", "0.1")],
        ),
    )
    assert resp.status_code == 200
    products = {p["normalized"]: p for p in resp.json()["products"]}
    assert products["Fe"]["theoretical_yield"] == "0.6666666666666666666666666667"
    assert products["Fe"]["yield_percent"] == "15"
    # CO2 理论 1 mol
    assert products["CO2"]["theoretical_yield"] == "1"
    assert products["CO2"]["yield_percent"] == "10"

    # 真正的无限循环收率：2C2H6 + 7O2 -> 4CO2 + 6H2O；各投 1 mol ->
    # O2 限量 ξ=1/7，H2O 理论 6/7；实际 0.1 -> 0.1/(6/7)*100 = 35/3 =
    # 11.666…，触发 28 位有效数字银行家舍入
    resp2 = client.post(
        PATH,
        json=_body(
            ["C2H6", "O2"], ["CO2", "H2O"],
            [("C2H6", "1"), ("O2", "1")],
            [("CO2", "0"), ("H2O", "0.1")],
        ),
    )
    products2 = {p["normalized"]: p for p in resp2.json()["products"]}
    assert products2["H2O"]["theoretical_yield"] == (
        "0.8571428571428571428571428571"
    )
    assert products2["H2O"]["yield_percent"] == (
        "11.66666666666666666666666667"  # 35/3，28 位有效数字
    )
    assert products2["CO2"]["yield_percent"] == "0"


def test_decimal_inputs_avoid_binary_float_artifacts():
    # Mg(OH)2 + 2HCl -> MgCl2 + 2H2O；各 0.1 mol -> HCl 限量 ξ=0.05，
    # H2O 理论 0.1；实际 0.1 -> 100%，不得出现 99.999… 之类二进制尾巴
    resp = client.post(
        PATH,
        json=_body(
            ["Mg(OH)2", "HCl"], ["MgCl2", "H2O"],
            [("Mg(OH)2", "0.1"), ("HCl", "0.1")],
            [("MgCl2", "0.03"), ("H2O", "0.1")],
        ),
    )
    products = {p["normalized"]: p for p in resp.json()["products"]}
    assert products["H2O"]["theoretical_yield"] == "0.1"
    assert products["H2O"]["yield_percent"] == "100"
    # MgCl2 理论 0.05；0.03/0.05*100 = 60
    assert products["MgCl2"]["theoretical_yield"] == "0.05"
    assert products["MgCl2"]["yield_percent"] == "60"


def test_tied_limiting_reactants_feed_in_stoichiometric_ratio():
    # 2SO2 + O2 -> 2SO3；2 mol SO2 + 1 mol O2 恰好按系数比 -> 并列限量
    resp = client.post(
        PATH,
        json=_body(
            ["SO2", "O2"], ["SO3"],
            [("SO2", "2"), ("O2", "1")],
            [("SO3", "1.8")],
        ),
    )
    data = resp.json()
    assert data["reaction_extent"]["value"] == "1"
    assert data["limiting_reactants"] == ["O2", "SO2"]
    product = data["products"][0]
    assert product["normalized"] == "SO3"
    assert product["theoretical_yield"] == "2"
    assert product["yield_percent"] == "90"


def test_species_matched_by_normalized_formula_aliases():
    # 等价写法（括号风格、空白、电荷写法）按规范化物种匹配：
    # MnO4^- + 8H^+ + 5e^- -> Mn^2+ + 4H2O
    # 投料 0.1 / 0.8 / 0.5 恰好按系数比，ξ=0.1；Mn2+ 理论 0.1、H2O 理论 0.4
    resp = client.post(
        PATH,
        json=_body(
            ["MnO4-", "H+", "e-"], ["Mn2+", "H2O"],
            [("MnO4^-", "0.1"), ("H^+", "0.8"), ("e", "0.5")],
            [("Mn^2+", "0.08"), ("H2 O", "0.36")],
        ),
    )
    assert resp.status_code == 200
    products = {p["normalized"]: p for p in resp.json()["products"]}
    assert products["Mn^2+"]["input"] == "Mn^2+"
    assert products["Mn^2+"]["theoretical_yield"] == "0.1"
    assert products["Mn^2+"]["yield_percent"] == "80"
    assert products["H2O"]["input"] == "H2 O"
    assert products["H2O"]["theoretical_yield"] == "0.4"
    assert products["H2O"]["yield_percent"] == "90"


def test_charged_products_over_100_percent_preserved():
    # 同上半反应，实际 Mn2+ 多于理论 -> 收率 > 100% 原样保留
    resp = client.post(
        PATH,
        json=_body(
            ["MnO4-", "H+", "e-"], ["Mn2+", "H2O"],
            [("MnO4^-", "0.1"), ("H^+", "0.8"), ("e", "0.5")],
            [("Mn2+", "0.11"), ("H2O", "0.4")],
        ),
    )
    products = {p["normalized"]: p for p in resp.json()["products"]}
    assert products["Mn^2+"]["yield_percent"] == "110"
    assert products["H2O"]["yield_percent"] == "100"


def test_actual_yield_zero_is_zero_percent_not_null():
    resp = client.post(
        PATH,
        json=_body(
            ["H2S", "O2"], ["SO2", "H2O"],
            [("H2S", "2"), ("O2", "3")],
            [("SO2", "0"), ("H2O", "0")],
        ),
    )
    data = resp.json()
    assert data["reaction_extent"]["value"] == "1"
    assert all(p["yield_percent"] == "0" for p in data["products"])
    assert all(p["yield_percent_reason"] is None for p in data["products"])


def test_scientific_notation_and_underscores_accepted():
    resp = client.post(
        PATH,
        json=_body(
            ["SO2", "O2"], ["SO3"],
            [("SO2", "2e-1"), ("O2", "1_000")],
            [("SO3", "1.5e-1")],
        ),
    )
    assert resp.status_code == 200
    product = resp.json()["products"][0]
    # ξ = 0.2/2 = 0.1（SO2 限量），SO3 理论 0.2；0.15/0.2*100 = 75
    assert product["theoretical_yield"] == "0.2"
    assert product["actual_yield"] == "0.15"
    assert product["yield_percent"] == "75"


# ------------------------------------------------------------ 顺序/写法无关性
def test_results_stable_across_order_and_notation():
    payload_a = _body(
        ["Mg(OH)2", "HCl"], ["MgCl2", "H2O"],
        [("Mg(OH)2", "0.2"), ("HCl", "0.2")],
        [("MgCl2", "0.08"), ("H2O", "0.16")],
    )
    payload_b = _body(
        ["HCl", "Mg [OH]2"], ["H2 O", "MgCl2"],
        [(" HCl", "0.20"), ("Mg(OH)2", "0.200")],
        [("H2O", "0.160"), ("MgCl2", "0.080")],
    )
    a = client.post(PATH, json=payload_a).json()
    b = client.post(PATH, json=payload_b).json()
    assert a["reaction_id"] == b["reaction_id"]
    assert a["reaction_extent"] == b["reaction_extent"]
    assert a["limiting_reactants"] == b["limiting_reactants"]

    def computed(blob):
        return sorted(
            (
                p["normalized"],
                p["coefficient"],
                p["theoretical_yield"],
                p["actual_yield"],
                p["yield_percent"],
                p["yield_percent_reason"],
            )
            for p in blob["products"]
        )

    assert computed(a) == computed(b)


def test_reaction_id_matches_balance_and_batch_endpoints():
    balance = client.post(
        "/api/v1/balance",
        json={"reactants": ["H2S", "O2"], "products": ["SO2", "H2O"]},
    ).json()
    yielded = client.post(
        PATH,
        json=_body(
            ["O2", "H2S"], ["H2O", "SO2"],
            [("O2", "9"), ("H2S", "4")],
            [("H2O", "3"), ("SO2", "3")],
        ),
    ).json()
    assert balance["reaction_id"] == yielded["reaction_id"]


def test_cached_flag_reflects_balance_cache():
    body = _body(
        ["Li", "O2"], ["Li2O"],
        [("Li", "4"), ("O2", "1")],
        [("Li2O", "1.9")],
    )
    first = client.post(PATH, json=body).json()
    second = client.post(PATH, json=body).json()
    assert first["cached"] is False
    assert second["cached"] is True
    # 4Li + O2 -> 2Li2O；恰好按计量比，理论 Li2O = 2，收率 95%
    assert first["products"][0]["theoretical_yield"] == "2"
    assert first["products"][0]["yield_percent"] == "95"


# ------------------------------------------------------------ 结构化错误
def test_missing_amount_is_invalid_request():
    resp = client.post(
        PATH,
        json=_body(
            ["H2S", "O2"], ["SO2", "H2O"],
            [("H2S", "2")],
            [("SO2", "1"), ("H2O", "1")],
        ),
    )
    assert resp.status_code == 422
    details = resp.json()["error"]["details"]
    assert details["amounts.missing"] == ["O2"]


def test_missing_actual_yield_is_invalid_request():
    resp = client.post(
        PATH,
        json=_body(
            ["H2S", "O2"], ["SO2", "H2O"],
            [("H2S", "2"), ("O2", "3")],
            [("SO2", "1")],
        ),
    )
    assert resp.status_code == 422
    details = resp.json()["error"]["details"]
    assert details["actual_yields.missing"] == ["H2O"]


def test_duplicate_amount_and_actual_entries_aggregated():
    resp = client.post(
        PATH,
        json={
            **H2S_BODY,
            "amounts": [
                {"species": "H2S", "moles": "2"},
                {"species": "H2S", "moles": "1"},
                {"species": "O2", "moles": "3"},
            ],
            "actual_yields": [
                {"species": "SO2", "moles": "1"},
                {"species": "SO2", "moles": "2"},
            ],
        },
    )
    assert resp.status_code == 422
    details = resp.json()["error"]["details"]
    assert details["amounts.duplicate"] == ["H2S"]
    assert details["actual_yields.duplicate"] == ["SO2"]
    # 生成物 H2O 同时缺失
    assert details["actual_yields.missing"] == ["H2O"]


def test_unknown_species_in_amounts_and_actuals():
    resp = client.post(
        PATH,
        json=_body(
            ["H2S", "O2"], ["SO2", "H2O"],
            [("H2S", "2"), ("N2", "3")],
            [("SO2", "1"), ("NH3", "1")],
        ),
    )
    assert resp.status_code == 422
    details = resp.json()["error"]["details"]
    assert details["amounts.unknown"] == ["N2"]
    assert details["amounts.missing"] == ["O2"]
    assert details["actual_yields.unknown"] == ["NH3"]
    assert details["actual_yields.missing"] == ["H2O"]


@pytest.mark.parametrize("bad", ["-1", "-0.0", "abc", "1.2.3", "NaN", "Infinity",
                                 "1e10001", "1e-10001"])
def test_invalid_amount_moles_values(bad):
    resp = client.post(
        PATH,
        json=_body(
            ["H2S", "O2"], ["SO2", "H2O"],
            [("H2S", bad), ("O2", "3")],
            [("SO2", "1"), ("H2O", "1")],
        ),
    )
    assert resp.status_code == 422
    details = resp.json()["error"]["details"]
    assert details["amounts.invalid_moles"] == [{"species": "H2S", "moles": bad}]


@pytest.mark.parametrize("bad", ["-2", "NaN", "abc", ""])
def test_invalid_actual_moles_values(bad):
    body = _body(
        ["H2S", "O2"], ["SO2", "H2O"],
        [("H2S", "2"), ("O2", "3")],
        [("SO2", "1"), ("H2O", "1")],
    )
    body["actual_yields"][1]["moles"] = bad
    resp = client.post(PATH, json=body)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "INVALID_REQUEST"


def test_moles_must_be_strings_not_json_numbers():
    body = _body(
        ["H2S", "O2"], ["SO2", "H2O"],
        [("H2S", "2"), ("O2", "3")],
        [("SO2", "1"), ("H2O", "1")],
    )
    body["amounts"][0]["moles"] = 2
    resp = client.post(PATH, json=body)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "INVALID_REQUEST"

    body2 = _body(
        ["H2S", "O2"], ["SO2", "H2O"],
        [("H2S", "2"), ("O2", "3")],
        [("SO2", "1"), ("H2O", "1")],
    )
    body2["actual_yields"][0]["moles"] = 0.5
    resp2 = client.post(PATH, json=body2)
    assert resp2.status_code == 422
    assert resp2.json()["error"]["code"] == "INVALID_REQUEST"


def test_unparseable_species_in_amounts_is_parse_error():
    resp = client.post(
        PATH,
        json=_body(
            ["H2S", "O2"], ["SO2", "H2O"],
            [("Xx", "2"), ("O2", "3")],
            [("SO2", "1"), ("H2O", "1")],
        ),
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "FORMULA_PARSE_ERROR"
    assert body["error"]["details"] == {"field": "amounts.species", "input": "Xx"}


def test_unparseable_species_in_actual_yields_is_parse_error():
    resp = client.post(
        PATH,
        json=_body(
            ["H2S", "O2"], ["SO2", "H2O"],
            [("H2S", "2"), ("O2", "3")],
            [("Zz", "1"), ("H2O", "1")],
        ),
    )
    assert resp.status_code == 400
    details = resp.json()["error"]["details"]
    assert details["field"] == "actual_yields.species"
    assert details["input"] == "Zz"


def test_non_unique_balance_reuses_structured_error():
    resp = client.post(
        PATH,
        json=_body(
            ["C", "O2"], ["CO", "CO2"],
            [("C", "1"), ("O2", "1")],
            [("CO", "1"), ("CO2", "1")],
        ),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "NON_UNIQUE_BALANCE"


def test_reaction_side_duplicate_reuses_conflict():
    resp = client.post(
        PATH,
        json=_body(
            ["H2S", "H2S"], ["SO2"],
            [("H2S", "2"), ("H2S", "1")],
            [("SO2", "1")],
        ),
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "DUPLICATE_SPECIES"


@pytest.mark.parametrize(
    "payload",
    [
        # 缺 amounts
        {"reactants": ["H2S"], "products": ["SO2"],
         "actual_yields": [{"species": "SO2", "moles": "1"}]},
        # 缺 actual_yields
        {"reactants": ["H2S"], "products": ["SO2"],
         "amounts": [{"species": "H2S", "moles": "1"}]},
        # 空 amounts / actual_yields
        {"reactants": ["H2S"], "products": ["SO2"], "amounts": [],
         "actual_yields": [{"species": "SO2", "moles": "1"}]},
        {"reactants": ["H2S"], "products": ["SO2"],
         "amounts": [{"species": "H2S", "moles": "1"}], "actual_yields": []},
        # 空反应式
        {"reactants": [], "products": ["SO2"],
         "amounts": [], "actual_yields": [{"species": "SO2", "moles": "1"}]},
        # 条目缺字段
        {"reactants": ["H2S", "O2"], "products": ["SO2"],
         "amounts": [{"species": "H2S"}],
         "actual_yields": [{"species": "SO2", "moles": "1"}]},
        {"reactants": ["H2S", "O2"], "products": ["SO2"],
         "amounts": [{"species": "H2S", "moles": "1"},
                     {"species": "O2", "moles": "1"}],
         "actual_yields": [{"moles": "1"}]},
        # 空白物种 / 摩尔数
        {"reactants": ["H2S"], "products": ["SO2"],
         "amounts": [{"species": "  ", "moles": "1"}],
         "actual_yields": [{"species": "SO2", "moles": "1"}]},
    ],
)
def test_request_shape_validation(payload):
    resp = client.post(PATH, json=payload)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "INVALID_REQUEST"


def test_response_payload_shape():
    # 回归保护：响应字段集合稳定
    resp = client.post(
        PATH,
        json=_body(
            ["H2S", "O2"], ["SO2", "H2O"],
            [("H2S", "2"), ("O2", "3")],
            [("SO2", "1"), ("H2O", "1")],
        ),
    )
    data = resp.json()
    assert set(data) == {
        "reaction_id", "cached", "normalized",
        "reaction_extent", "limiting_reactants", "products",
    }
    assert set(data["products"][0]) == {
        "input", "normalized", "coefficient",
        "theoretical_yield", "actual_yield",
        "yield_percent", "yield_percent_reason",
    }
