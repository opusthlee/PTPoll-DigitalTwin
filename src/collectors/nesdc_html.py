"""
NESDC 상세 페이지 HTML 스크래퍼 — stdlib만 사용.

NESDC가 공개하는 정보 구조:
- HTML 상세 페이지: 표본 demographic 분포 (성별·연령대·지역대별 사례수). 후보 지지율 X
- PDF (24시간 후 공개): 후보 지지율 by demographic. 별도 Vision API 필요

이 모듈은 HTML만 파싱. PDF 추출은 nesdc_pdf.py (Vision API) 별건.

확인된 ntt_id: 18544 (2026-04 샘플) — Table 1에 demographic sample 분포.
"""
import re
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional

BASE_URL = "https://www.nesdc.go.kr"
LIST_URL = f"{BASE_URL}/portal/bbs/B0000005/list.do"
VIEW_URL = f"{BASE_URL}/portal/bbs/B0000005/view.do"
MENU_NO = "200467"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "ko-KR,ko;q=0.9"}


class _TableExtractor(HTMLParser):
    """모든 <table>을 list[list[list[str]]]로 추출 (table → rows → cells)."""

    def __init__(self):
        super().__init__()
        self.tables: List[List[List[str]]] = []
        self._cur_table: Optional[List[List[str]]] = None
        self._cur_row: Optional[List[str]] = None
        self._cur_cell: Optional[str] = None
        self._in_cell = False

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._cur_table = []
        elif tag == "tr" and self._cur_table is not None:
            self._cur_row = []
        elif tag in ("td", "th") and self._cur_row is not None:
            self._cur_cell = ""
            self._in_cell = True

    def handle_endtag(self, tag):
        if tag == "table" and self._cur_table is not None:
            self.tables.append(self._cur_table)
            self._cur_table = None
        elif tag == "tr" and self._cur_row is not None:
            if self._cur_table is not None:
                self._cur_table.append(self._cur_row)
            self._cur_row = None
        elif tag in ("td", "th") and self._cur_row is not None:
            self._cur_row.append((self._cur_cell or "").strip())
            self._cur_cell = None
            self._in_cell = False

    def handle_data(self, data):
        if self._in_cell and self._cur_cell is not None:
            self._cur_cell += data


def _fetch_html(url: str, timeout: int = 15) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def list_recent_ntt_ids(page: int = 1, limit: int = 10) -> List[str]:
    """목록 페이지에서 최근 ntt_id N개 추출."""
    qs = urllib.parse.urlencode({"menuNo": MENU_NO, "pageIndex": str(page)})
    html = _fetch_html(f"{LIST_URL}?{qs}")
    ids = re.findall(r"nttId=(\d+)", html)
    seen = []
    for x in ids:
        if x not in seen:
            seen.append(x)
        if len(seen) >= limit:
            break
    return seen


def fetch_detail_tables(ntt_id: str) -> List[List[List[str]]]:
    qs = urllib.parse.urlencode({"nttId": ntt_id, "menuNo": MENU_NO})
    html = _fetch_html(f"{VIEW_URL}?{qs}")
    p = _TableExtractor()
    p.feed(html)
    return p.tables


def _to_int(s: str) -> Optional[int]:
    s = re.sub(r"[^\d]", "", s)
    return int(s) if s else None


def parse_sample_demographics(tables: List[List[List[str]]]) -> Dict[str, Dict[str, int]]:
    """
    Table 1 ('표본의 크기')에서 성별/연령대별/지역대별 사례수 추출.

    구조 (NESDC 표준):
      R0: ['표본의 크기']
      R1: ['구분', '조사완료 사례수(명)', '가중값 적용 기준 사례수(명)']
      R2: ['전체', N, N']
      R3: ['성별', '남', N, N']
      R4:        ['여', N, N']
      R5: ['연령대별', '18~29세', N, N']
      ...

    반환: {
        "GENDER": {"남": 476, "여": 326},
        "AGE":    {"18~29세": 96, "30대": 93, ...},
        "REGION": {"1권역": 132, ...},
        "TOTAL":  {"전체": 802},
    }
    weight-적용 사례수가 아닌 raw N 사용.
    """
    out: Dict[str, Dict[str, int]] = {"TOTAL": {}, "GENDER": {}, "AGE": {}, "REGION": {}}
    if not tables or len(tables) < 2:
        return out

    # 첫 번째 table에서 '표본의 크기' 찾기 (보통 index 1)
    target = None
    for t in tables[:5]:
        if t and t[0] and "표본의 크기" in (t[0][0] if t[0] else ""):
            target = t
            break
    if not target:
        return out

    current_group = None  # GENDER/AGE/REGION
    GROUP_MAP = {"성별": "GENDER", "연령대별": "AGE", "지역별": "REGION"}

    for row in target[2:]:  # skip "표본의 크기" + header
        if not row:
            continue
        first = row[0].strip()
        if first == "전체":
            n = _to_int(row[1]) if len(row) > 1 else None
            if n is not None:
                out["TOTAL"]["전체"] = n
            continue
        if first in GROUP_MAP:
            current_group = GROUP_MAP[first]
            # 같은 행에 첫 segment label이 함께 있는 경우 (R3: '성별', '남', N, N)
            if len(row) >= 3:
                label = row[1].strip()
                n = _to_int(row[2])
                if label and n is not None:
                    out[current_group][label] = n
            continue
        # 헤더 없이 segment + N (R4: '여', N, N)
        if current_group and len(row) >= 2:
            label = row[0].strip()
            n = _to_int(row[1])
            if label and n is not None:
                out[current_group][label] = n

    return out


def parse_meta(tables: List[List[List[str]]]) -> Dict[str, Any]:
    """Table 0에서 조사명·기간·기관 등 메타 추출."""
    meta: Dict[str, Any] = {}
    if not tables or not tables[0]:
        return meta
    for row in tables[0]:
        if len(row) >= 2:
            key = row[0].strip()
            val = " ".join(c.strip() for c in row[1:]).strip()
            if key and val:
                meta[key] = val
    return meta


def fetch_demographics(ntt_id: str) -> Dict[str, Any]:
    """단일 ntt_id의 demographic 분포 + 메타를 한 번에 가져오기."""
    tables = fetch_detail_tables(ntt_id)
    return {
        "ntt_id": ntt_id,
        "meta": parse_meta(tables),
        "demographics": parse_sample_demographics(tables),
        "table_count": len(tables),
    }


if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser()
    ap.add_argument("--ntt-id", help="단일 ntt_id 테스트")
    ap.add_argument("--list-page", type=int, default=1)
    ap.add_argument("--limit", type=int, default=5)
    args = ap.parse_args()

    if args.ntt_id:
        result = fetch_demographics(args.ntt_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        ids = list_recent_ntt_ids(args.list_page, args.limit)
        print(f"recent ntt_ids: {ids}")
        for nid in ids[:2]:
            print(f"\n=== {nid} ===")
            r = fetch_demographics(nid)
            print(json.dumps(r, ensure_ascii=False, indent=2))
