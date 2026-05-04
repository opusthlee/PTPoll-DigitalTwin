# PTPoll Twin Database Review Report

## 1. Summary Statistics
- **Objects Count:**
  - CANDIDATE: 3
  - POLL: 2
  - POLLSTER: 2
  - SEGMENT: 4
- **Links Count:**
  - CONDUCTED: 2
  - MEASURES: 6
  - MEASURES_IN_SEGMENT: 4

---
## 2. Objects Detail (Sample)
| ID | Type | Name | Properties |
| :--- | :--- | :--- | :--- |
| 1 | POLLSTER | 한국갤럽 | `{}` |
| 2 | POLL | 한국갤럽_2026-05-01_전국 | `{'date': '2026-05-01', 'region': '전국', 'sample_size': 1000, 'method': '전화면접', 'source_id': 1}` |
| 3 | CANDIDATE | 홍길동 | `{}` |
| 4 | CANDIDATE | 이순신 | `{}` |
| 5 | CANDIDATE | 세종대왕 | `{}` |
| 6 | POLLSTER | 리얼미터 | `{}` |
| 7 | POLL | 리얼미터_2026-05-03_전국 | `{'date': '2026-05-03', 'region': '전국', 'sample_size': 2500, 'method': 'ARS', 'source_id': 2}` |
| 8 | SEGMENT | AGE_20s | `{'category': 'AGE', 'label': '20s'}` |
| 9 | SEGMENT | AGE_30s | `{'category': 'AGE', 'label': '30s'}` |
| 10 | SEGMENT | REGION_Seoul | `{'category': 'REGION', 'label': 'Seoul'}` |
| 11 | SEGMENT | GENDER_Female | `{'category': 'GENDER', 'label': 'Female'}` |

---
## 3. Relationships & Values (Knowledge Graph)
| Source (From) | Relation (Link) | Target (To) | Values/Properties |
| :--- | :--- | :--- | :--- |
| 한국갤럽 | **CONDUCTED** | 한국갤럽_2026-05-01_전국 | `{}` |
| 한국갤럽_2026-05-01_전국 | **MEASURES** | 홍길동 | `{'support_rate': 42.5}` |
| 한국갤럽_2026-05-01_전국 | **MEASURES** | 이순신 | `{'support_rate': 38.2}` |
| 한국갤럽_2026-05-01_전국 | **MEASURES** | 세종대왕 | `{'support_rate': 10.1}` |
| 리얼미터 | **CONDUCTED** | 리얼미터_2026-05-03_전국 | `{}` |
| 리얼미터_2026-05-03_전국 | **MEASURES** | 홍길동 | `{'support_rate': 39.8}` |
| 리얼미터_2026-05-03_전국 | **MEASURES** | 이순신 | `{'support_rate': 41.5}` |
| 리얼미터_2026-05-03_전국 | **MEASURES** | 세종대왕 | `{'support_rate': 12.0}` |
| 한국갤럽_2026-05-01_전국 | **MEASURES_IN_SEGMENT** | AGE_20s | `{'홍길동': 28.5, '이순신': 41.2}` |
| 한국갤럽_2026-05-01_전국 | **MEASURES_IN_SEGMENT** | AGE_30s | `{'홍길동': 35.0, '이순신': 38.5}` |
| 한국갤럽_2026-05-01_전국 | **MEASURES_IN_SEGMENT** | REGION_Seoul | `{'홍길동': 45.2, '이순신': 32.1}` |
| 한국갤럽_2026-05-01_전국 | **MEASURES_IN_SEGMENT** | GENDER_Female | `{'홍길동': 30.5, '이순신': 44.8}` |
