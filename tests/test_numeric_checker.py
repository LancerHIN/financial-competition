from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.numeric_checker import (  # noqa: E402
    insurance_payout,
    parse_amount_to_yuan,
    parse_percent,
    per_share_dividend,
    ratio_of,
    threshold_check,
    yoy_change,
)


def test_per_share_dividend_per10_normalized():
    d = per_share_dividend("每10股派发现金红利43元")
    assert d is not None
    assert d["per_share"] == 4.3
    assert d["per10"] == 43.0


def test_per_share_dividend_already_per_share():
    d = per_share_dividend("每股0.43元")
    assert d is not None and d["per_share"] == 0.43 and d["base"] == 1.0


def test_per_share_dividend_shorthand():
    d = per_share_dividend("10派4.3")
    assert d is not None and d["per_share"] == 0.43


def test_per_share_dividend_none_when_no_structure():
    assert per_share_dividend("分红政策保持稳定") is None
    assert per_share_dividend(None) is None


def test_ratio_8yi_over_120yi():
    r = ratio_of("8亿元", "120亿元")
    assert r is not None
    assert round(r["ratio_pct"], 2) == 6.67


def test_threshold_75_gt_70():
    chk = threshold_check("75%", "70%")
    assert chk is not None
    assert chk["exceeds"] is True
    assert chk["relation"] == ">"


def test_two_thirds_percent():
    assert round(parse_percent("三分之二") * 100, 4) == 66.6667


def test_majority_half():
    assert parse_percent("过半数") == 0.5


def test_cn_percent_seventy():
    assert parse_percent("百分之七十") == 0.7


def test_amount_unit_normalization():
    assert parse_amount_to_yuan("8亿元") == 8e8
    assert parse_amount_to_yuan("1,234.56万元") == 12345600.0
    assert parse_amount_to_yuan("500元") == 500.0
    assert parse_amount_to_yuan("3千元") == 3000.0
    assert parse_amount_to_yuan("2百万元") == 2_000_000.0


def test_yoy_change_growth():
    change = yoy_change("777,102,455,000.00元", "602,315,354,000.00元")
    assert change is not None
    assert change["direction"] == "增长"
    assert round(change["rate_pct"], 2) == 29.02


def test_insurance_payout_max():
    payout = insurance_payout(["10万元", "8万元", "12万元"], "max")
    assert payout is not None
    assert payout["result"] == 120000.0


def test_insurance_payout_min():
    payout = insurance_payout(["10万元", "8万元", "12万元"], "min")
    assert payout["result"] == 80000.0
