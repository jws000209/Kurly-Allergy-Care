# 표시사항 이미지 OCR (label_ocr)

`label_images/` 의 표시사항 이미지에서 상품마다 아래를 뽑는다.

| 열 | 내용 |
|---|---|
| `product_id` | 상품 번호 |
| `원재료명_내용` | 원재료명(및 함량) 칸의 글자 |
| `함유_알레르기` | '○○ 함유', '알레르기 유발물질: ○○' 표시의 알레르기 (19종 표준명) |
| `같은시설_알레르기` | '○○를 사용한 제품과 같은 제조시설에서 제조', '○○ 혼입 가능' 의 알레르기 |
| `원재료명_알레르겐` | 원재료명 안에서 찾은 알레르기 (함유 표시를 OCR이 놓쳤을 때의 보조 근거) |
| `원재료명_있음` · `알레르기함유_있음` · `같은시설_있음` | 3가지 문구가 보이는지 |
| `근거문장` | 알레르기를 뽑은 문장 (확인용) |

알레르기 이름·오탈자 교정·함유와 혼입의 구분은 `kurly-allergy-care/backend/app/extractor.py` 를 그대로 가져다 써서 Rule Engine과 기준을 맞췄다.

## 폴더 위치

저장소 안(`kurly-allergy-care/label_ocr`)이나 데이터 폴더 안(`마켓컬리 데이터/label_ocr`) 어디에 있어도 된다. 바탕화면 `핵심프로젝트 AI` 폴더 기준으로 `마켓컬리 데이터/`(`kurly_products_v2.csv`, `label_images.csv`, `label_images/`)와 `kurly-allergy-care/backend` 를 찾는다 (`paths.py`).

- 이미지(`label_images/`, 약 8.4GB)는 저장소에 없다. OCR을 다시 돌리려면 `마켓컬리 데이터`에 있어야 한다.
- 저장소의 `ocr_output/` 에는 GitHub 파일 한도(100MB) 때문에 압축본(`*.jsonl.gz`)만 있다. 코드가 압축본을 그대로 읽으므로 풀지 않아도 `build_result.py`·`evaluate.py`가 돈다. OCR을 이어서 돌리면 자동으로 풀어서 이어 쓴다.
- `result/` 는 이 OCR 결과로 만든 표 (10/08).

## 실행 순서

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

.venv\Scripts\python ocr_windows.py                                 # 1) Windows OCR, 전체 이미지, 컬러 1배 (약 25분)
.venv\Scripts\python ocr_windows.py --scale 2 --targets processed   # 2) Windows OCR, 가공품, 흑백 2배 (약 1시간 50분)
.venv\Scripts\python ocr_rapid.py                                   # 3) RapidOCR, 가공품, 흑백 + 좌표 (그래픽카드, 약 2시간)
.venv\Scripts\python build_result.py                                # 4) 판정·추출 → result/ (약 1분)
.venv\Scripts\python ocr_rapid.py --extra                           # 5) 3가지가 안 보인 상품만 회전·대비 보정해 다시 읽기 (약 5분)
.venv\Scripts\python build_result.py                                # 6) 다시 만들기
.venv\Scripts\python evaluate.py         # (확인) 컬리 텍스트 알레르기 정보와 대조한 정확도
```

- OCR(1~3, 5)은 끊겨도 다시 실행하면 이어서 한다 (`ocr_output/*.jsonl` 에 이미지 한 장당 한 줄).
- OCR을 다시 돌리지 않고 규칙만 고쳤다면 `build_result.py`만 다시 실행하면 된다.
- 엔진마다 놓치는 곳이 달라서 `build_result.py`는 판독 4개(`ocr_output/*.jsonl`)를 모두 합쳐서 찾는다.

## 파일

| 파일 | 하는 일 |
|---|---|
| `paths.py` | 경로 모음 |
| `imgutil.py` | 이미지 불러오기 · 폭 맞추기 · 긴 이미지 겹쳐 자르기 |
| `jobs.py` | 대상 이미지 고르기 · 여러 프로세스 실행 · 이어서 하기 |
| `ocr_windows.py` | Windows 내장 OCR (한국어) |
| `ocr_rapid.py` | RapidOCR (PaddleOCR PP-OCRv5 한국어 모델, DirectML로 그래픽카드) |
| `classify.py` | 원재료(신선) 상품 판정, 3가지 문구 유무 정규식 |
| `extract_label.py` | 원재료명 · 함유 · 같은 시설 알레르기 추출 정규식 |
| `build_result.py` | 위 결과를 합쳐 `result/` 표 만들기 |
| `evaluate.py` | 정확도 확인 |

## 원재료 상품 판정 (`classify.py`)

카테고리 소분류가 신선 원물(채소 전체, 쌀·과일, 생고기, 생선·해산물)이고 상품명에 양념·훈제·건조·가공 표시가 없으면 원재료 상품으로 보고 판정에서 뺀다. '구이용·불고기·장조림·볶음탕용'처럼 손질 용도만 적은 생고기는 원재료로 본다.

## 정확도와 한계

### 전처리 · 엔진 비교 (10/08, 컬리 텍스트 알레르기 정보가 있는 표본 120개 상품)

| 조합 | 함유 재현율 | 같은 시설 재현율 |
|---|---|---|
| Windows 컬러 1배 | 41% | 31% |
| Windows 흑백 2배 (LANCZOS) | 56% | 37% |
| Windows Otsu 이진화 2배 | 32% | 22% |
| Windows 적응형 이진화 2배 | 27% | 26% |
| RapidOCR 컬러 | 74% | 61% |
| RapidOCR 흑백 1배 | 76% | 62% |
| RapidOCR Otsu 이진화 2배 | 50% | 51% |
| Windows 흑백 2배 + RapidOCR | 85% | 68% |

- 이진화는 두 엔진 모두 나빠졌다 (색 박스 안 흰 글씨 '우유 함유' 같은 표시가 지워진다) → 쓰지 않는다.
- 흑백 + 확대는 도움이 된다. 보간법(바이큐빅·LANCZOS) 차이는 거의 없다.

### 전체 결과 정확도 (판독 4개 합침, `evaluate.py`)

컬리 텍스트 알레르기 정보가 있는 상품 약 4,600개와 대조:

| | 정밀도 | 재현율 |
|---|---|---|
| 함유 알레르기 | 80% | 78% |
| 같은 시설 알레르기 | 72% | 74% |
| 함유 + 원재료명에서 찾은 알레르기 | 76% | 83% |

- 정답(컬리 텍스트)에도 함유·시설 문구가 빠진 상품이 있어, '오탐'으로 센 것 중 일부는 이미지에 실제로 있는 알레르기다 (표본 확인). 실제 정밀도는 이보다 높다.
- 원재료명은 정답이 없어 표본으로만 확인했다: 짧은 원재료명과 표 형식은 대체로 맞고, 작은 글씨는 오탈자가 많다. **원재료명 내용은 참고용**이다.

### 한계

- 세로로 돌아간 표시사항, 폭이 아주 작은 이미지(500px 안팎), 세로 2만 px 넘는 상세 이미지 속 작은 표시사항은 놓치기 쉽다.
- `result/label_ocr_missing.csv` 의 '이미지 확인 필요' 상품은 단일 원료라 실제로 표시가 없는 것과 OCR이 못 읽은 것이 섞여 있다.
- OCR 결과로 알레르기를 판정에 쓰려면 Rule Engine 데이터에 넣기 전에 사람이 확인해야 한다 (놓친 알레르기는 PASS 오판으로 이어진다).
