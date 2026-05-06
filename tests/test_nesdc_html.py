"""NESDC HTML 파서 검증 — 네트워크 미사용 (HTML fixture 인라인)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.collectors.nesdc_html import (
    _TableExtractor,
    _to_int,
    parse_meta,
    parse_sample_demographics,
)


SAMPLE_HTML = """
<html><body>
<table id="meta">
  <tr><th>등록 글번호</th><td>16360</td></tr>
  <tr><th>여론조사 명칭</th><td>지방선거 여론조사</td></tr>
  <tr><th>조사기관명</th><td>조원씨앤아이</td></tr>
  <tr><th>조사지역</th><td>경기도</td></tr>
  <tr><th>조사일시</th><td>2026-05-04 14시 ~ 2026-05-05</td></tr>
</table>
<table id="size">
  <tr><th>표본의 크기</th></tr>
  <tr><th>구분</th><th>조사완료 사례수(명)</th><th>가중값 적용 기준</th></tr>
  <tr><td>전체</td><td>802</td><td>802</td></tr>
  <tr><td>성별</td><td>남</td><td>476</td><td>403</td></tr>
  <tr><td>여</td><td>326</td><td>399</td></tr>
  <tr><td>연령대별</td><td>18~29세</td><td>96</td><td>123</td></tr>
  <tr><td>30대</td><td>93</td><td>131</td></tr>
  <tr><td>40대</td><td>143</td><td>146</td></tr>
  <tr><td>50대</td><td>177</td><td>159</td></tr>
  <tr><td>60대</td><td>172</td><td>137</td></tr>
  <tr><td>70세 이상</td><td>121</td><td>106</td></tr>
  <tr><td>지역별</td><td>1권역</td><td>132</td><td>119</td></tr>
  <tr><td>2권역</td><td>65</td><td>50</td></tr>
</table>
</body></html>
"""


def _parse(html):
    p = _TableExtractor()
    p.feed(html)
    return p.tables


class TestTableExtractor(unittest.TestCase):
    def test_parses_simple_table(self):
        tables = _parse("<table><tr><td>a</td><td>b</td></tr></table>")
        self.assertEqual(tables, [[["a", "b"]]])

    def test_handles_th_and_td(self):
        tables = _parse("<table><tr><th>H</th><td>D</td></tr></table>")
        self.assertEqual(tables, [[["H", "D"]]])

    def test_strips_whitespace(self):
        tables = _parse("<table><tr><td>  hello  </td></tr></table>")
        self.assertEqual(tables[0][0][0], "hello")

    def test_multiple_tables(self):
        tables = _parse(SAMPLE_HTML)
        self.assertEqual(len(tables), 2)


class TestToInt(unittest.TestCase):
    def test_strips_non_digits(self):
        self.assertEqual(_to_int("802명"), 802)
        self.assertEqual(_to_int("1,234"), 1234)
        self.assertEqual(_to_int(""), None)
        self.assertEqual(_to_int("---"), None)

    def test_returns_none_for_empty(self):
        self.assertIsNone(_to_int(""))


class TestParseMeta(unittest.TestCase):
    def test_extracts_korean_keys(self):
        tables = _parse(SAMPLE_HTML)
        meta = parse_meta(tables)
        self.assertEqual(meta.get("조사기관명"), "조원씨앤아이")
        self.assertEqual(meta.get("조사지역"), "경기도")
        self.assertIn("2026-05-04", meta.get("조사일시", ""))


class TestParseSampleDemographics(unittest.TestCase):
    def test_extracts_total(self):
        tables = _parse(SAMPLE_HTML)
        d = parse_sample_demographics(tables)
        self.assertEqual(d["TOTAL"]["전체"], 802)

    def test_extracts_gender(self):
        tables = _parse(SAMPLE_HTML)
        d = parse_sample_demographics(tables)
        self.assertEqual(d["GENDER"]["남"], 476)
        self.assertEqual(d["GENDER"]["여"], 326)

    def test_extracts_age_groups(self):
        tables = _parse(SAMPLE_HTML)
        d = parse_sample_demographics(tables)
        self.assertEqual(d["AGE"]["18~29세"], 96)
        self.assertEqual(d["AGE"]["30대"], 93)
        self.assertEqual(d["AGE"]["70세 이상"], 121)

    def test_extracts_region(self):
        tables = _parse(SAMPLE_HTML)
        d = parse_sample_demographics(tables)
        self.assertEqual(d["REGION"]["1권역"], 132)
        self.assertEqual(d["REGION"]["2권역"], 65)

    def test_handles_missing_table(self):
        d = parse_sample_demographics([])
        self.assertEqual(d, {"TOTAL": {}, "GENDER": {}, "AGE": {}, "REGION": {}})

    def test_handles_table_without_size_marker(self):
        """첫 행이 '표본의 크기'가 아니면 빈 결과."""
        tables = _parse("<table><tr><td>다른표</td></tr></table>")
        d = parse_sample_demographics(tables)
        self.assertEqual(d["TOTAL"], {})


if __name__ == "__main__":
    unittest.main()
