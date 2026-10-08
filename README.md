# Kurly Allergy Care AX — MVP

팀 우버 · 가족 프로필 연동 식품 알레르기 맞춤 장바구니 챗봇.
컬리 내부 데이터·UI를 받을 수 없어, **크롬 확장 프로그램**이 kurly.com 위에 챗봇 패널을 얹고 **로컬 백엔드**가 판정과 장바구니 구성을 맡는다.

```
kurly.com (크롬 확장: 챗봇·가족 프로필 패널, 상품 페이지 판정 배지, 컬리 장바구니 담기)
        │  background.js 가 API 호출 대행
        ▼
FastAPI (127.0.0.1:8000)
  LangGraph Agent : parse_intent → resolve_conditions → build_cart | edit_cart → revalidate → respond → remember
  LangChain       : Structured Output 으로 자연어 → ShoppingIntent (조건 구조화)
                    + 새 추천마다 Rule Engine 통과 후보 중에서 고르기 (app/selector.py)
  Rule Engine     : 19종 알레르겐 사전 대조, PASS / BLOCK / UNVERIFIED (판정은 여기서만)
  SQLite          : 계정·가족 프로필·로컬 상품 DB
```

## 실행

### 1. 백엔드

`backend\run.bat` 을 더블클릭한다. 처음 한 번은 가상환경과 패키지를 설치한다.

**LLM 키 (필수 챌린지: LangChain 챗봇)** — 무료 Gemini 키를 쓴다.

1. https://aistudio.google.com/apikey 에서 Google 계정으로 키 발급 (카드 등록 없음)
2. `backend\.env` 를 메모장으로 열어 `GOOGLE_API_KEY=` 뒤에 붙여 넣고 저장 (`.env` 는 제출 코드에 포함되지 않는다)
3. `backend` 폴더에서 점검: `.venv\Scripts\python scripts\check_llm.py` → 시연 문장 7개가 LLM 경로로 해석되는지 표시
4. `run.bat` 을 다시 켠다

- 기본 모델은 `google_genai:gemini-flash-lite-latest`, 실패하면 `gemini-flash-latest` → (키가 있으면) Groq 순으로 넘어간다. 특정 모델을 먼저 쓰려면 `.env` 의 `LLM_MODEL`.
- 예비: Groq 무료 키(https://console.groq.com/keys)를 `GROQ_API_KEY` 에 넣으면 `groq:qwen/qwen3.8-27b` 를 쓴다 (Gemini가 모두 실패하면 넘어감).
- 키가 없거나 호출이 실패(한도 초과 등)하면 규칙 기반 파서로 대체한다. 어느 쪽으로 처리했는지는 패널에 표시하지 않고, `/api/chat` 응답의 `parser`·`llm`·`picked_by` 와 `http://127.0.0.1:8000/api/health` 의 `llm`·`llm_last_error` 로 확인한다.

### (선택) Google Colab에서 LLM 부분만 실행

`colab/KurlyAllergyCare_LangChain_Colab.ipynb` 를 Colab에서 열고, 왼쪽 🔑 보안 비밀에 `GOOGLE_API_KEY` 를 저장(노트북 액세스 켜기)한 뒤 위에서부터 실행한다. 2번 셀에서 `colab/kac_backend.zip` 을 업로드한다.
코드를 고쳤으면 `backend\.venv\Scripts\python colab\make_colab_package.py` 로 zip·노트북을 다시 만든다.

### 2. 크롬 확장

1. 크롬 주소창에 `chrome://extensions` 입력
2. 오른쪽 위 **개발자 모드** 켜기
3. **압축해제된 확장 프로그램을 로드합니다** → `extension` 폴더 선택
4. https://www.kurly.com 을 열면 오른쪽 아래에 **알레르기 케어** 버튼이 보인다

코드를 고친 뒤에는 `chrome://extensions` 에서 확장을 새로고침하고 컬리 탭도 새로고침한다.

## 시연 순서 (5분)

1. 마켓컬리에 로그인(이름을 자동으로 불러옴) → **가족 프로필** 탭에서 `첫째`(우유·알류), `아빠`(땅콩) 추가
2. 컬리 상품 상세 페이지 열기 → 버튼과 패널 위쪽에 PASS / BLOCK 판정 표시 (컬리의 `알레르기정보` 를 읽어 판정)
3. 챗봇: `만두 2만원 이내 3개` → 추천 상품과 검증 내역 표시 → **장바구니에 추가** 를 누르면 컬리 장바구니에 바로 담김
4. `2번 상품 빼줘` → `만두 대신 라면` → `더 저렴하게` : 수정할 때마다 전체 재검증
5. 대상에 `아빠` 추가 선택 후 다시 요청 → 알레르겐 합집합 적용
6. `라면 3천원 이내 5개` → 알레르기 조건은 유지하고 예산·개수 변경을 요청
7. 패널 위쪽 **장바구니** 버튼, 또는 챗봇에 `장바구니 보여줘` → 컬리 장바구니(https://www.kurly.com/cart)로 이동

## 설계 원칙 (기획서 반영)

| 원칙 | 구현 위치 |
|---|---|
| 알레르기 판정은 LLM이 아니라 Rule Engine | `app/rule_engine.py`. LLM 출력은 조건(`ShoppingIntent`)뿐이고 19종 사전 밖의 성분명은 버린다 |
| 여러 프로필 선택 시 합집합 | `rule_engine.combine_profiles` |
| 대상 연결은 규칙으로, 모르면 되묻기 | `app/profiles.py`. LLM은 사람 표현만 뽑고, 별칭 표(이름·관계·순서·직접 별칭)로 프로필에 연결. 없거나 애매하면 추천하지 않고 되묻는다 |
| LLM은 Rule Engine 통과 상품 안에서만 고름 | `app/selector.py`. 새 추천마다(같은 대화에서 보여 준 상품은 뒤로 돌림), 검색 점수 상위 25개 후보에서 번호로 고르고 이유를 붙인다. 후보 밖 번호는 버리고, 개수·예산은 코드가 다시 맞추고, 실패하면 점수 순. 담은 뒤 전체 재검증 |
| 상황(소풍 등)은 그 추천에만 | `app/situations.py`. 새 추천 요청이 오면 초기화, 알려진 상황은 규칙 표·나머지만 LLM 제안 낱말. 알레르기 판정과 무관 |
| 없는 상품 유형은 알리기 | `cart_builder.missing_terms`. 상품 DB에 없는 유형은 다른 상품으로 채우지 않고 안내 |
| 정보 누락 상품은 자동 장바구니에서 제외 | 원재료·표시 정보가 없으면 `UNVERIFIED`, 후보에서 빠진다 |
| 조건이 안 맞아도 알레르기 조건은 완화하지 않음 | `cart_builder.build` 는 개수를 줄이고 사용자에게 조건 변경을 요청 |
| 수정할 때마다 재검증 | 그래프의 `revalidate` 노드가 모든 수정 뒤에 실행 |
| 로컬 상품 DB + 갱신일 | `products.last_checked_at` |

`혼입 가능`·`같은 제조시설` 표시는 함유와 구분해 저장한다. 기본은 PASS + 주의 문구이고, 프로필에서 **엄격 모드**를 켜면 BLOCK 이다.

## 상품 데이터

- 팀이 수집·전처리한 `마켓컬리 데이터/kurly_products_v2_final.csv`(4,015개, 13개 대분류)에 선물세트 47개를 더해 4,062개를 쓴다. 가져오기 명령은 `backend/scripts/import_kurly_csv.py` 맨 위 설명 참고.
- CSV를 바꾸면 `backend` 폴더에서 아래를 실행해 `backend/data/kurly_products.json` 을 다시 만든다. 서버를 켤 때 DB에 없는 상품이 추가된다.
  `.venv\Scripts\python scripts\import_kurly_csv.py [CSV 경로]`
- 알레르겐은 CSV의 `contains_allergens` 열이 아니라 `allergy_info` 원문을 서버 추출기로 다시 읽어 판정한다. 대조해 보니 `contains_allergens` 에는 "같은 제조시설" 알레르겐까지 함유로 섞인 행이 있었다.
- 확장을 켠 채 컬리 상품 상세 페이지를 열면 그 상품도 로컬 DB에 저장되어(`source = kurly-page`) 추천 후보가 된다.
- `backend/tests/mock_products.json` 의 가상 상품 62개는 테스트 전용이다.

## 폴더

```
backend/
  app/allergens.py     19종 사전 (동의어·오탐 방지 표현)
  app/extractor.py     원재료명·표시 문구 → 알레르겐 추출
  app/rule_engine.py   PASS / BLOCK / UNVERIFIED 판정
  app/intent.py        Shopping Intent Parser (LangChain + 규칙 기반 대체 파서)
  app/cart_builder.py  후보 검색·순위, 장바구니 구성, 재검증
  app/agent.py         LangGraph 상태 그래프
  app/db.py            SQLite
  app/main.py          FastAPI
  scripts/import_kurly_csv.py  수집 CSV → data/kurly_products.json
  data/kurly_products.json     상품 시드 (컬리 4,062개)
  tests/test_mvp.py    기획서의 검증 테스트 항목
extension/             크롬 확장 (manifest v3)
```

테스트: `backend` 폴더에서 `.venv\Scripts\python -m pytest`

## 알려진 한계

- 계정은 컬리 상단 메뉴의 "OOO 님"에서 읽은 이름으로 구분한다. 별도 인증은 없고, 컬리 화면 구조가 바뀌면 `extension/content.js`의 `readKurlySession`을 고쳐야 한다.
- 대화 상태(LangGraph `MemorySaver`)는 메모리에 있어 서버를 재시작하면 사라진다. 그때는 패널의 **새 대화**를 누른다.
- "장바구니에 추가"는 상품 페이지를 뒤에서 열어 컬리의 "장바구니 담기" 버튼을 대신 누르고 탭을 닫는다. 컬리 자체 버튼을 쓰므로 회원·비회원 장바구니 모두 컬리가 처리한다.
  - 옵션을 골라야 하는 상품(이름에 "택1", "맵기선택" 등)은 추천 순위를 뒤로 미루고, 버튼 대신 **옵션 고르기** 링크로 상품 페이지를 연다. 이름에 표시가 없는 옵션 상품은 담기를 시도한 뒤 상품 페이지를 앞으로 띄운다.
  - 담은 뒤 지금 보고 있는 컬리 탭의 장바구니 숫자는 새로고침해야 바뀐다.
  - 컬리 상품 페이지 구조가 바뀌면 `extension/content.js` 맨 위의 작업 탭 코드를 고쳐야 한다.
- 컬리 페이지 구조가 바뀌면 `extension/content.js` 의 `readPageProduct` 를 고쳐야 한다.
- 구매 전에는 실제 상품의 원재료·알레르기 표시를 확인해야 한다. 이 서비스는 의학적 판단을 대신하지 않는다.
