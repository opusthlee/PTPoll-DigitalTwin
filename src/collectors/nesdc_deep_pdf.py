"""
NESDC PDF deep extraction — 후보×demographic 지지율 추출 (D-2).

NESDC 공개 정책: 후보 지지율 by demographic은 PDF에만 존재.
HTML(`nesdc_html.py`)은 sample 분포(N수)만 보유 — D-1에서 처리.

이 모듈은 Vision API를 사용한 PDF 표 추출용. 현재는 인터페이스 정의만 있고,
실제 호출은 ANTHROPIC_API_KEY + `pip install anthropic` 필요.

향후 구현 시:
1. PDF 다운로드 (NESDC 첨부파일 URL — list/detail 페이지에 FileDown 링크 존재)
2. PDF → 이미지 변환 (Claude Vision은 PDF 직접 입력도 지원)
3. Vision API로 표 추출 (구조화 prompt)
4. 결과 → MEASURES_IN_SEGMENT links

비용 추정: PDF당 ~$0.01-0.05.
"""
from typing import Dict, List, Protocol


class SegmentSupportExtractor(Protocol):
    """후보×demographic 지지율 추출기 인터페이스.

    구현체는 PDF/이미지를 입력받아 표준 segment-support 형식 반환.
    형식:
      [
        {"group": "AGE", "segment": "30대",
         "results": {"홍길동": 35.0, "이순신": 38.5}},
        {"group": "GENDER", "segment": "남",
         "results": {"홍길동": 40.0, "이순신": 42.0}},
        ...
      ]
    """

    def extract(self, pdf_url: str) -> List[Dict]: ...


class AnthropicVisionExtractor:
    """Claude Vision API 기반 추출기 — 미구현 stub.

    활성화하려면:
        pip install anthropic
        export ANTHROPIC_API_KEY=sk-ant-...

    그 후 이 클래스의 extract() 구현 (Anthropic SDK + Vision messages).
    """

    def __init__(self, api_key: str = None, model: str = "claude-opus-4-7"):
        import os
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.model = model

    def extract(self, pdf_url: str) -> List[Dict]:
        if not self.api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY 미설정. D-2 활성화 절차:\n"
                "  1) pip install anthropic\n"
                "  2) export ANTHROPIC_API_KEY=sk-ant-...\n"
                "  3) AnthropicVisionExtractor.extract() 구현 (Vision API call)"
            )
        raise NotImplementedError(
            "Vision API 호출 미구현. PDF→이미지→Anthropic Messages API 호출 후 "
            "structured output parsing 필요."
        )
