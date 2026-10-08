# Kurly Allergy Care AX

마켓컬리 상품 페이지에서 가족 알레르기 프로필을 확인하고, 대화로 조건에 맞는 장보기 상품을 추천하는 Chrome 확장 프로그램과 FastAPI 백엔드입니다.

2026-10-08 기준 **아이템 기반 협업 필터링**, **이 상품을 산 사람이 산 다른 상품 추천**, **콘텐츠 기반 추천**을 구현했습니다. 사용자 요청에서 말한 사용자 기반 기능은 구매자들의 다른 구매 상품을 활용하는 방식으로 반영했습니다.

알레르기 판정은 현재 **기존 19종 사전 + Rule Engine**입니다. 향후 **생성형 AI + RAG**를 사용할 예정이며, 제공된 XLSX 39종 사전은 아직 판정과 프로필에 연결하지 않았습니다. 다음 작업자는 [Handoff.md](Handoff.md)와 [AGENTS.md](AGENTS.md)를 먼저 확인하세요.

## 실행

Windows PowerShell, 프로젝트 루트 기준입니다. Python 3.13.9에서 검증했습니다.

```powershell
py -m venv backend/.venv
backend/.venv/Scripts/python.exe -m pip install -r backend/requirements.txt
Copy-Item backend/.env.example backend/.env
Set-Location backend
.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

기존 `.env`가 있으면 복사 단계를 건너뜁니다. 키가 없어도 규칙 파서와 추천 알고리즘이 실행됩니다. LLM을 사용하려면 `.env`에 제공사의 키와 필요 시 `LLM_MODEL=제공사:모델`을 설정합니다. 자동 시도 목록은 `app/intent.py`의 `DEFAULT_MODELS`에 있으며 실제 모델 가용성은 제공사와 설정에 따라 달라집니다.

현재 로컬 `backend/.env`에는 사용자 요청에 따라 이전 프로젝트 `../kurly-allergy-care(구)/backend/.env`의 설정을 복사했습니다. Gemini·Groq 키가 설정되어 있으며, Gemini `google_genai:gemini-flash-lite-latest`로 기존 조건 해석 점검 7개를 통과했습니다. 키는 Git에서 제외되고 문서에 기록하지 않습니다. 현재 설정을 유지하려면 위 `.env.example` 복사 명령을 실행하지 마세요.

이미 가상환경이 준비된 경우 `backend/run.bat`으로도 실행할 수 있습니다.

- 상태: `http://127.0.0.1:8000/api/health`
- API 문서·직접 호출: `http://127.0.0.1:8000/docs`
- LLM 점검: `backend`에서 `.venv/Scripts/python.exe scripts/check_llm.py`

Chrome의 `chrome://extensions`에서 개발자 모드를 켜고 **압축해제된 확장 프로그램을 로드합니다**로 `extension` 폴더를 선택합니다. `https://www.kurly.com`을 열면 알레르기 케어 패널이 표시됩니다. 확장 코드 수정 후에는 확장 프로그램과 컬리 페이지를 모두 새로고침합니다.

## 사용 흐름

1. 컬리 상단 사용자 이름을 로컬 계정에 연결합니다. 가족 이름·관계·순서·별칭·회피 성분·엄격 모드를 등록합니다.
2. 프로필을 선택하고 상품 상세 페이지를 열면 표시문을 판정하고 상품과 조회 행동을 저장합니다.
3. `첫째 간식 2만원 이내 5개`, `만두 대신 라면`, `2번 상품 빼줘`, `더 저렴하게`처럼 요청합니다.
4. 알레르기 검증을 통과한 후보에 검색 관련도·상황·개인화·예산·최소 구매 수량을 적용합니다. 추천 생성·수정 후 전체를 재검증합니다.
5. **장바구니에 추가**는 상품 작업 탭에서 컬리 자체 버튼을 사용합니다. 성공 시에만 `cart` 행동을 기록합니다. 옵션 상품은 사용자가 상품 페이지에서 선택합니다.

추천 노출은 구매나 선호로 기록하지 않습니다. 확장은 `view`와 `cart`를 수집합니다. `like`와 `purchase`는 API로 명시적으로 기록할 수 있으며, 실제 결제 완료 자동 추적은 아직 없습니다. **구매 기반 추천을 사용하려면 실제 구매 이력을 `purchase`로 입력해야 합니다.**

## 구조와 코드 분석

```text
Chrome content.js (Shadow DOM 패널, 페이지 읽기, 프로필, 장바구니 작업)
  → background.js (API 중계, 작업 탭 관리)
  → FastAPI main.py
      → LangGraph agent.py
          parse_intent → resolve_conditions
            → build_cart / edit_cart / answer / ask_profile
            → revalidate → respond → remember
      → cart_builder.py: 후보 검색·알레르기 필터·예산·수량
      → recommender.py: 아이템 협업·함께 구매·콘텐츠 추천
      → selector.py: 검증 통과 후보 중 LLM 선택, 실패 시 코드로 선택
      → extractor.py + rule_engine.py: 현재 알레르기 판정
      → db.py: SQLite 계정·가족·상품·행동 이력
```

| 파일·폴더 | 역할 |
| --- | --- |
| `backend/app/main.py` | 요청 스키마, API, CORS, 시작 시 DB 초기화 |
| `backend/app/db.py` | 스키마·마이그레이션, 시드 동기화, 재추출, 최소 수량 보정, 행동 저장 |
| `backend/app/allergens.py` | 현재 19종 표준명·동의어·오탐 방지 표현 |
| `backend/app/extractor.py` | 함유·혼입 구분, 오탈자 보정, 판정 근거 유무 |
| `backend/app/rule_engine.py` | 회피 성분 합집합, 엄격 모드, PASS/BLOCK/UNVERIFIED |
| `backend/app/intent.py` | LangChain 구조화 출력, LLM 대체, 규칙 파서 |
| `backend/app/profiles.py` | 이름·관계·순서·별칭·가족 집합 해석, 불명확한 대상 되묻기 |
| `backend/app/situations.py` | 소풍·캠핑 등 상황의 우선·제외 낱말 |
| `backend/app/cart_builder.py` | 검색 관련도, 인기도·개인화 정렬, 소분류 다양성, 예산·수량·재검증 |
| `backend/app/recommender.py` | 아이템 유사도, 함께 구매한 상품, TF-IDF, 혼합 점수 |
| `backend/app/selector.py` | 상위 25개 후보 중 LLM 선택, 번호·중복 검증, 예산 재적용 |
| `backend/app/agent.py` | 대화·장바구니·묶음 추천·재추천, 계정별 대화 구분 |
| `backend/scripts/import_kurly_csv.py` | CSV 정제, 옵션 제외, 검토된 빈 표시 처리, 상품 시드 생성 |
| `backend/scripts/check_llm.py` | 실제 키로 조건 해석 점검 |
| `backend/tests/` | 기존 MVP 회귀 검증과 추천 알고리즘·API 검증 |
| `extension/` | Manifest V3, 패널, API 중계, 컬리 DOM·상품 정보 추출 |
| `colab/` | 실행 노트북, 백엔드 ZIP, 생성 스크립트 |
| `data/` | 사용자 제공 XLSX 알레르겐 사전 원본 |

## 추천 방식

상위 `../추천알고리즘_예시코드`의 음악 콘텐츠 추천과 영화 아이템 협업 필터링 노트북을 참고했습니다.

**아이템 기반 협업 필터링 (`mode=item`)**: 영화 예시와 같은 방식입니다. 사용자×상품 행동 행렬을 상품×사용자로 전치하고 상품 간 코사인 유사도를 계산합니다. 각 후보에 대해 사용자가 경험한 상품 중 유사도가 양수인 상위 20개를 선택해 `Σ(유사도 × 행동 가중치) / Σ(유사도) / 5`로 예측 선호를 계산합니다. `reference_product_id`를 지정하면 그 상품을 기준으로 계산합니다. 단일 기준 상품에서는 예시의 가중 평균 특성상 여러 후보의 예측값이 같을 수 있습니다.

**이 상품을 산 사람이 산 다른 상품 (`mode=user`)**: 지정한 기준 상품 또는 현재 사용자의 구매 상품을 구매한 다른 계정을 찾습니다. 그 계정들이 구매한 다른 상품을 구매자 비율로 추천합니다. 예를 들어 기준 상품 구매자 10명 중 7명이 B도 구매했다면 B의 점수는 0.7입니다. 현재 계정은 집계에서 제외하고, 기준 상품은 함께 구매 점수에서 제외합니다. 조회·선호·장바구니는 구매로 계산하지 않습니다. 이는 사용자 간 코사인 유사도 방식과 구분되는 구매자 행동 기반 추천입니다.

**콘텐츠 기반 (`mode=content`)**: 음악 예시의 콘텐츠 유사도·속성 가중치를 식품에 적용했습니다. Doc2Vec 대신 상품 데이터만으로 계산 가능한 TF-IDF를 사용합니다. 상품명 2배·분류 3배·원재료 1배 빈도로 한글/영문 낱말과 글자 2개 조각을 추출합니다. `TF=1+log(빈도)`, `IDF=1+log((1+상품수)/(1+문서빈도))`를 곱해 L2 정규화합니다. 행동으로 가중 합친 취향 벡터 또는 기준 상품과 코사인 유사도를 계산합니다. 알레르기 표시문은 취향 벡터에 넣지 않으며 상품 내용 변경 시 벡터 캐시가 갱신됩니다.

**혼합 추천 (`mode=hybrid`, 기본)**: 아이템 40%·함께 구매 30%·콘텐츠 30%를 사용합니다. 해당 상품에 양수 점수가 있는 방식만 포함하고 가중치를 다시 정규화합니다. 단일 방식은 그 방식을 표시하고, 모든 근거가 없으면 인기도로 대체합니다. `item`과 `user` 단독 모드에서도 해당 점수가 없으면 콘텐츠 → 인기도로 대체합니다.

행동 가중치는 `view=1`, `like=3`, `cart=4`, `purchase=5`입니다. 아이템·콘텐츠 계산에서는 사용자·상품별 가장 강한 신호를 사용합니다. 동일 사용자·상품·행동 재요청은 타임스탬프만 갱신해 중복 가산을 막습니다. 구매 기반 추천은 구매자 집합을 사용하므로 중복 구매 횟수를 가산하지 않습니다. 취향 이력은 계정 단위이며 가족 구성원별 분리는 아직 없습니다.

대화의 생성·추가·교체에는 기본 혼합 추천을 적용합니다. 요청 관련도·상황 우선 여부·옵션 유무·실상품 여부를 먼저 유지한 뒤 개인화 점수 → 후기 수 → 판매량 순위로 정렬합니다. `더 저렴하게`는 가격순입니다. 최종 구성에는 소분류 다양성과 예산도 적용되므로 개인화 점수만의 내림차순은 아닙니다. LLM 키가 있으면 정렬된 상위 25개 후보 안에서 추가로 선택합니다.

대화는 재구매 상품을 유지합니다. 별도 추천 API는 기본적으로 경험한 상품을 제외하며 `exclude_seen=false`로 바꿀 수 있습니다. 지정한 기준 상품은 항상 제외합니다. 신규 사용자도 기준 상품을 지정하면 해당 상품의 이력·콘텐츠를 활용할 수 있습니다.

## API

| 메서드·경로 | 기능 |
| --- | --- |
| `GET /api/health`, `/api/allergens`, `/api/profile-options` | 상태, 현재 지원 성분, 프로필 선택지 |
| `POST /api/login` | 이름 기반 로컬 계정 생성·조회 |
| `GET/POST /api/users/{user_id}/members` | 가족 목록·추가 |
| `PUT/DELETE /api/members/{member_id}` | 프로필 변경·삭제 |
| `POST /api/aliases/preview` | 자동 별칭 미리보기 |
| `POST /api/chat` | 대화 추천·수정 (`user_id`, `thread_id`, `message`, `selected_member_ids`) |
| `POST /api/check` | 상품 판정·선택적 저장 |
| `GET /api/products` | 상품 목록 |
| `POST /api/products/{product_id}/min-ea` | 최소 구매 수량 보정 |
| `POST /api/users/{user_id}/interactions` | 행동 저장 (`product_id`, `event`) |
| `POST /api/recommendations` | 아이템·함께 구매·콘텐츠·혼합 추천 |

구매 이력 기록 요청 본문 예시입니다. 실제 구매가 확인된 상품에만 사용합니다.

```json
{"product_id": "실제상품ID", "event": "purchase"}
```

“이 상품을 산 사람이 산 상품” 추천 요청 예시입니다. ID는 로그인·프로필·상품 API에서 받은 값을 사용합니다.

```json
{
  "user_id": 1,
  "member_ids": [1],
  "mode": "user",
  "reference_product_id": "실제상품ID",
  "count": 5,
  "budget": 20000,
  "exclude_seen": true
}
```

`mode=item`은 아이템 협업 필터링, `mode=content`는 유사 콘텐츠 추천입니다. `keywords`에 `["간식"]`처럼 검색 조건을 넣을 수 있습니다. 응답의 `items`에는 `recommendation_score`, `recommendation_method`, `item_score`, `co_purchase_score`, `content_score`가 포함됩니다. `mode=user`의 실제 방식 표시는 `co_purchase`입니다. 대체 동작으로 요청 모드와 상품별 방식이 다를 수 있습니다. `notes`는 개수·예산 부족 등을 설명합니다.

`member_ids`가 비어 있으면 개인 알레르기 회피 조건은 적용되지 않습니다. 선택한 프로필은 해당 계정 소유인지 검증합니다. 모든 후보는 현재 판정 근거가 있어야 하고 최종 목록은 다시 검증합니다.

## 상품·DB·사전

상품 시드는 `backend/data/kurly_products.json`의 4,062개, 대분류 13개입니다. 테스트는 `backend/tests/mock_products.json`을 사용합니다. 기본 DB는 `backend/data/kac.sqlite3`이며 최초 실행 시 생성됩니다. 시작 시 시드 변경을 동기화하고 계정·프로필·행동을 보존합니다. 시드에서 빠진 `source=kurly` 상품은 제거하며 현재 없는 상품의 행동은 추천 계산에서 제외합니다.

| 환경변수 | 기본값·역할 |
| --- | --- |
| `KAC_DB_PATH` | `backend/data/kac.sqlite3` |
| `KAC_SEED_PATH` | `backend/data/kurly_products.json` |
| `KAC_LIVE_MIN_EA_PATH` | `backend/data/min_ea_live.json`, 최소 수량 보정 |
| `LLM_MODEL` | 우선 사용할 `제공사:모델` |
| `GOOGLE_API_KEY`, `GROQ_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` | 선택한 LLM 제공사의 키 |

CSV 교체는 `backend`에서 실행합니다. `--blank-reviewed`는 팀이 빈 표시를 원물·단순 구성으로 검토한 데이터에만 사용합니다.

```powershell
.venv/Scripts/python.exe scripts/import_kurly_csv.py "CSV경로"
# 선택 옵션: --blank-reviewed --add-gift-sets "원본CSV" --min-ea "최소수량CSV" --keep-unverified
```

제공된 `data/알레르겐_원재료_매칭사전_v1.xlsx`는 읽기 전용으로 확인했습니다.

| 시트 | 내용 |
| --- | --- |
| `사용가이드` | 자동 매칭·검토 정책 |
| `39_알레르겐_마스터` | 알레르겐 39종 |
| `원재료_매칭사전` | 1,302개 매칭어, 사전ID·성분ID·정규화키·규칙·자동매칭·신뢰등급·검토 여부·근거·URL |
| `예외_검토규칙` | 20개 예외 규칙 |
| `출처` | 출처 5개 |

신뢰등급은 A 1,231개·B 51개·C 20개, 자동매칭 Y는 1,234개입니다. Y만 검사해서 A등급으로 취급하면 안 됩니다. 신뢰등급·자동매칭·수동검토·예외 규칙을 함께 적용해야 합니다. 원본은 수정하지 않았습니다.

RAG는 사전과 예외 규칙을 근거 ID·버전이 있는 검색 문서로 만들고, 원재료·표시문·선택 프로필에 필요한 근거를 검색해 생성형 AI의 구조화 판정을 받는 방향입니다. 19종 문자열과 39종 표준 ID의 매핑, 프로필 확장, 함유·혼입 구분, 근거 부족 시 UNVERIFIED, 기존 상품 재처리를 함께 구현해야 합니다. **현재 임베딩·벡터 DB·RAG 판정 호출은 구현하지 않았습니다.** 상세 후속 작업은 Handoff.md에 있습니다.

## 검증과 Colab

```powershell
Set-Location backend
.venv/Scripts/python.exe -m pytest -q
```

2026-10-08 전체 테스트 **44개 통과**. 기존 MVP 회귀 검증과 신규 추천 검증은 아이템 유사도·가중 평균, 함께 구매 집계의 구매 전용 조건, 콘텐츠 순위·캐시, 이력 중복·보존, 대체 동작, API 유효성, 알레르기·엄격 모드·정보 누락 제외, 예산·최소 수량, 대화 연결·계정별 상태 분리를 확인합니다. 최신 실행 결과는 Handoff.md에 기록합니다. 외부 LLM 실호출과 컬리 브라우저 장바구니 동작은 자동 테스트에 포함되지 않습니다.

Colab은 `colab/KurlyAllergyCare_LangChain_Colab.ipynb`와 `colab/kac_backend.zip`을 사용합니다. 코드 변경 후 루트에서 `backend/.venv/Scripts/python.exe colab/make_colab_package.py`로 재생성합니다. API 키·실사용 DB·가상환경·XLSX 사전은 묶음에 포함하지 않습니다.

## 현재 한계

- 이름 기반 로컬 계정이며 인증이 없습니다. 같은 표시 이름을 구분하지 못합니다. 공개 서비스 전에 인증·권한 검증이 필요합니다.
- 대화 상태는 메모리에 있어 서버 재시작 시 사라집니다. 프로필·행동은 SQLite에 남습니다.
- 실제 구매 데이터가 없으면 함께 구매 추천의 근거가 없습니다. 가중치는 초기 설정이며 실제 사용자 로그로 추천 품질을 평가하지 않았습니다.
- 상품·로그인·장바구니 버튼은 컬리 DOM과 `__NEXT_DATA__`에 의존합니다. 구조 변경 시 `extension/content.js` 점검이 필요합니다.
- 페이지 상품 저장이 시드의 가격·원재료 등을 빈 값으로 덮을 수 있고, 재시작 때 시드로 다시 맞춰질 수 있습니다. 우선순위 정책은 후속 과제입니다.
- 현재 PASS는 기존 19종 범위에서 회피 성분 미검출을 뜻합니다. XLSX 확장 성분·예외 정책과 생성형 AI·RAG 연결은 후속 구현입니다.
