"""
PDF deep extraction — 후보×demographic 지지율 + 폴 메타 추출 (D-2).

Anthropic Claude Vision API. claude-sonnet-4-6은 PDF 직접 입력 지원.

차단 사항(2026-05): NESDC PDF 첨부는 모두 로그인 필수. PTPoll은 pdf_inbox 패턴 —
사용자가 어떤 경로로든 PDF 확보 후 `data/pdf_inbox/` 디렉토리에 두면 자동 처리.

비용: claude-sonnet-4-6 PDF 1장 ~$0.01-0.05 (페이지·표 수에 비례).
검증된 정확도: 합성 한국어 폴 PDF 22/22 셀 100% (2026-05-06).
"""
import base64
import json
import logging
import os
import re
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# 메타 + 세그먼트 한 번에 추출 (호출 비용 절감)
EXTRACTION_PROMPT = """이 한국어 여론조사 보고서 PDF에서 다음 두 가지를 추출하세요:

1. 폴 메타정보:
   - agency: 조사기관명
   - date: 조사일 또는 공표일 (YYYY-MM-DD, 기간이면 종료일)
   - region: 조사지역 (전국 / 서울 / 경기 등)
   - sample_size: 표본 크기 (정수)
   - method: 조사방법 (전화면접/ARS/혼합 등)
   - election: 선거명 또는 조사 주제

2. demographic(인구통계) 별 후보·정당 지지율 표:
   - 성별 (남/여) × 후보별 지지율
   - 연령대별 (18~29세 / 30대 / 40대 / 50대 / 60대 / 70세 이상 등)
   - 지역별 (서울/경기/인천/충청/호남/영남/강원/제주 또는 권역)
   - 직업별, 학력별, 소득별 (있는 경우)

출력은 다음 JSON 한 객체만 (다른 텍스트 금지):
```json
{
  "meta": {
    "agency": "...", "date": "YYYY-MM-DD", "region": "...",
    "sample_size": 1000, "method": "...", "election": "..."
  },
  "segments": [
    {"group": "GENDER", "segment": "남", "results": {"후보A": 42.5, "후보B": 38.0}},
    {"group": "AGE", "segment": "30대", "results": {"후보A": 35.0, "후보B": 45.5}},
    {"group": "REGION", "segment": "서울", "results": {"후보A": 50.2, "후보B": 30.1}}
  ]
}
```

규칙:
1. group은 GENDER/AGE/REGION/OCCUPATION/EDUCATION/INCOME 중 하나 (영문 대문자)
2. segment는 PDF 표기 한글 그대로 (예: "30대", "수도권")
3. results는 {후보·정당명: 백분율(float)} dict — % 기호 빼고 숫자만
4. 무응답·기타·잘 모름·없음은 제외 (실 후보·정당만)
5. meta 필드 못 찾으면 null
6. segments 표가 없으면 빈 배열
7. JSON 객체 외 다른 텍스트 절대 금지

PDF 추출 시작:"""


def _build_pdf_content(pdf_path_or_url: str) -> Dict:
    if pdf_path_or_url.startswith(("http://", "https://")):
        return {
            "type": "document",
            "source": {"type": "url", "url": pdf_path_or_url},
        }
    with open(pdf_path_or_url, "rb") as f:
        data = base64.standard_b64encode(f.read()).decode("ascii")
    return {
        "type": "document",
        "source": {"type": "base64", "media_type": "application/pdf", "data": data},
    }


def _parse_response(text: str) -> Dict:
    """{...} JSON 객체 추출."""
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        return json.loads(m.group(1))
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        return json.loads(m.group(0))
    return {"meta": {}, "segments": []}


def _validate_segments(items) -> List[Dict]:
    if not isinstance(items, list):
        return []
    valid = []
    allowed = {"GENDER", "AGE", "REGION", "OCCUPATION", "EDUCATION", "INCOME"}
    for it in items:
        if not isinstance(it, dict):
            continue
        group = str(it.get("group", "")).upper()
        if group not in allowed:
            continue
        segment = str(it.get("segment", "")).strip()
        results = it.get("results", {})
        if not segment or not isinstance(results, dict) or not results:
            continue
        clean_results = {}
        for k, v in results.items():
            try:
                clean_results[str(k).strip()] = float(v)
            except (TypeError, ValueError):
                continue
        if clean_results:
            valid.append({"group": group, "segment": segment, "results": clean_results})
    return valid


def _validate_meta(meta) -> Dict:
    if not isinstance(meta, dict):
        return {}
    out = {}
    for key in ("agency", "date", "region", "method", "election"):
        v = meta.get(key)
        if v is not None and v != "" and str(v).lower() != "null":
            out[key] = str(v).strip()
    if "sample_size" in meta:
        try:
            out["sample_size"] = int(meta["sample_size"])
        except (TypeError, ValueError):
            pass
    # date YYYY-MM-DD 정규화
    if "date" in out:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", out["date"])
        if m:
            out["date"] = m.group(1)
        else:
            out.pop("date", None)
    return out


class AnthropicVisionExtractor:
    """Claude Vision PDF 추출기 — 한국어 여론조사 보고서."""

    DEFAULT_MODEL = "claude-sonnet-4-6"
    DEFAULT_MAX_TOKENS = 4096
    # Sonnet 4.6 가격 ($/1M tokens)
    PRICE_INPUT = 3.0
    PRICE_OUTPUT = 15.0

    def __init__(self, api_key: Optional[str] = None,
                 model: str = DEFAULT_MODEL,
                 max_tokens: int = DEFAULT_MAX_TOKENS):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY 미설정.\n"
                "  PTPoll/.cron.env에 ANTHROPIC_API_KEY=sk-ant-... 설정 후 source"
            )
        self.model = model
        self.max_tokens = max_tokens
        import anthropic
        self._client = anthropic.Anthropic(api_key=self.api_key)

    def extract(self, pdf_path_or_url: str) -> Dict:
        """PDF → {"meta", "segments", "usage", "cost_usd", "raw_response"}."""
        content = [
            _build_pdf_content(pdf_path_or_url),
            {"type": "text", "text": EXTRACTION_PROMPT},
        ]
        msg = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=[{"role": "user", "content": content}],
        )
        raw_text = "".join(b.text for b in msg.content if hasattr(b, "text"))
        try:
            parsed = _parse_response(raw_text)
        except json.JSONDecodeError as e:
            logger.error(f"[vision] JSON parse 실패: {e}")
            parsed = {"meta": {}, "segments": []}

        meta = _validate_meta(parsed.get("meta", {}))
        segments = _validate_segments(parsed.get("segments", []))

        cost = (msg.usage.input_tokens * self.PRICE_INPUT
                + msg.usage.output_tokens * self.PRICE_OUTPUT) / 1_000_000

        logger.info(
            f"[vision] {os.path.basename(pdf_path_or_url)[-50:]} → "
            f"{len(segments)} segments, agency={meta.get('agency','?')}, "
            f"in={msg.usage.input_tokens}, out={msg.usage.output_tokens}, ${cost:.4f}"
        )
        return {
            "meta": meta,
            "segments": segments,
            "usage": {
                "input_tokens": msg.usage.input_tokens,
                "output_tokens": msg.usage.output_tokens,
            },
            "cost_usd": cost,
            "raw_response": raw_text,
        }


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="PDF → meta + segment 추출")
    ap.add_argument("pdf", help="PDF 경로 또는 URL")
    ap.add_argument("--model", default=AnthropicVisionExtractor.DEFAULT_MODEL)
    ap.add_argument("--show-raw", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ex = AnthropicVisionExtractor(model=args.model)
    r = ex.extract(args.pdf)
    out = {"meta": r["meta"], "segments": r["segments"], "cost_usd": r["cost_usd"]}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    if args.show_raw:
        print(f"\n=== RAW ===\n{r['raw_response']}")
