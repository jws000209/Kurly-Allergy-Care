"""Colab에 올릴 백엔드 묶음(kac_backend.zip)과 노트북을 만든다.

사용법 (kurly-allergy-care 폴더에서):
    backend\\.venv\\Scripts\\python colab\\make_colab_package.py

zip에는 코드·상품 데이터·테스트만 넣는다. API 키(.env)·DB·가상환경은 넣지 않는다.
"""
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "colab"
ZIP_PATH = OUT_DIR / "kac_backend.zip"
NB_PATH = OUT_DIR / "KurlyAllergyCare_LangChain_Colab.ipynb"

INCLUDE = ["backend/app/*.py", "backend/data/kurly_products.json", "backend/scripts/check_llm.py",
           "backend/tests/*.py", "backend/tests/mock_products.json", "backend/requirements.txt"]


def make_zip() -> None:
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
        for pattern in INCLUDE:
            for path in sorted(ROOT.glob(pattern)):
                zf.write(path, path.relative_to(ROOT).as_posix())
    names = zipfile.ZipFile(ZIP_PATH).namelist()
    assert not any(n.endswith(".env") or n.endswith(".sqlite3") for n in names)
    print(f"{ZIP_PATH.name}: {len(names)}개 파일")


_ids = iter(range(1, 1000))


def md(text: str) -> dict:
    return {"cell_type": "markdown", "id": f"cell{next(_ids)}", "metadata": {}, "source": text.strip("\n").splitlines(keepends=True)}


def code(text: str) -> dict:
    return {"cell_type": "code", "id": f"cell{next(_ids)}", "metadata": {}, "execution_count": None, "outputs": [],
            "source": text.strip("\n").splitlines(keepends=True)}


CELLS = [
    md("""
# Kurly Allergy Care AX — LangChain·LangGraph 챗봇 (Colab)

팀 우버 · 가족 프로필 연동 식품 알레르기 맞춤 장보기 챗봇의 **LLM 부분**을 Colab에서 실행합니다.

| 역할 | 담당 |
|---|---|
| 자연어 요청 → 조건(대상 프로필·제외 성분·상품 유형·예산·개수) 구조화 | **LLM** (LangChain Structured Output, Gemini 무료 API) |
| 대화 상태·흐름 제어 (생성 → 수정 → 재검증) | **LangGraph** |
| 알레르기 판정 (PASS / BLOCK / UNVERIFIED) | **Rule Engine** (19종 알레르겐 사전, LLM이 관여하지 않음) |
| 상품 데이터 | 팀이 수집한 컬리 상품 4,062개 (13개 대분류) |

**준비물**
1. `kac_backend.zip` (이 노트북과 같은 `colab` 폴더에 있음)
2. Gemini API 키 — 왼쪽 🔑(보안 비밀) 메뉴에 이름 `GOOGLE_API_KEY` 로 저장하고 *노트북 액세스*를 켜 주세요. 없으면 실행 중에 입력창이 뜹니다.
"""),
    md("## 1. 패키지 설치"),
    code("""
%pip install -q langchain langgraph langchain-google-genai langchain-groq python-dotenv fastapi httpx pytest pandas
"""),
    md("## 2. 백엔드 코드·데이터 불러오기\n`kac_backend.zip` 을 업로드합니다 (이미 `/content` 에 있으면 건너뜀)."),
    code("""
import os, sys, zipfile

try:
    import google.colab  # noqa: F401
    IN_COLAB = True
except ImportError:
    IN_COLAB = False

BASE = "/content/kac" if IN_COLAB else os.path.abspath("_kac_run")
ZIP = "/content/kac_backend.zip" if IN_COLAB else os.path.abspath("kac_backend.zip")

if not os.path.exists(ZIP):
    from google.colab import files
    uploaded = files.upload()  # kac_backend.zip 선택
    ZIP = "/content/" + next(iter(uploaded))

zipfile.ZipFile(ZIP).extractall(BASE)
os.chdir(os.path.join(BASE, "backend"))
sys.path.insert(0, os.getcwd())
os.environ["KAC_DB_PATH"] = os.path.join(BASE, "kac_colab.sqlite3")  # 실행할 때마다 새 DB
if os.path.exists(os.environ["KAC_DB_PATH"]):
    os.remove(os.environ["KAC_DB_PATH"])
print("작업 폴더:", os.getcwd())
"""),
    md("## 3. Gemini API 키 설정\n키 값은 출력하지 않습니다."),
    code("""
import getpass

key = None
if IN_COLAB:
    try:
        from google.colab import userdata
        key = userdata.get("GOOGLE_API_KEY")
    except Exception:
        key = None
else:  # 로컬에서 노트북을 돌릴 때는 backend/.env 를 읽는다
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(ZIP)), "..", "backend", ".env"))
    key = os.getenv("GOOGLE_API_KEY")
if not key:
    key = getpass.getpass("GOOGLE_API_KEY 입력: ")
os.environ["GOOGLE_API_KEY"] = key
print("키 설정 완료 (길이", len(key), ")")
"""),
    md("## 4. 상품 DB 준비"),
    code("""
from app import db, intent
from app.allergens import ALLERGEN_NAMES

db.init_db()
products = db.list_products()
print("상품 수:", len(products), "| 알레르겐 사전:", len(ALLERGEN_NAMES), "종")
print("LLM 시도 순서:", intent.llm_models())
"""),
    md("""
## 5. LangChain 조건 구조화 (Structured Output)
같은 문장을 **LLM**과 **규칙 기반 파서**로 각각 해석해 비교합니다. 무료 등급이 혼잡(503)하면 다음 모델로 넘어가고, 모두 실패하면 규칙 파서로 대체합니다.
"""),
    code("""
import pandas as pd

MEMBERS = ["첫째", "아빠", "엄마"]
CART = [{"name": "[비비고] 청양 고기만두 200g"}, {"name": "[비비고] 진한 김치고기만두 200g"}]
SAMPLES = [
    "만두 2만원 이내 3개",
    "우유·달걀 없이 밀키트 3개",
    "첫째랑 아빠 먹을 저녁거리 3만원 안에서 4개 골라줘",
    "2번 상품 빼줘",
    "만두 대신 라면",
    "더 저렴하게",
    "알레르기 있는 아이 간식은 어떻게 고르면 좋아?",
]
FIELDS = ["action", "target_profiles", "excluded_allergens", "category_or_purpose", "budget", "item_count",
          "item_index", "from_keyword", "to_keyword"]

def brief(d):
    return ", ".join(f"{k}={d[k]}" for k in FIELDS if d.get(k) not in (None, [], ""))

rows = []
for text in SAMPLES:
    cart = CART if any(w in text for w in ["빼줘", "대신", "저렴"]) else []
    llm_result, parser = intent.parse(text, MEMBERS, cart)
    rows.append({"문장": text, "파서": parser, "응답 모델": intent.last_model if parser == "llm" else "-",
                 "LLM 해석": brief(llm_result),
                 "규칙 해석": brief(intent._clean(intent.rule_parse(text, MEMBERS, cart)))})
pd.set_option("display.max_colwidth", None)
pd.DataFrame(rows)
"""),
    md("""
## 6. LangGraph 대화 흐름 — 추천 생성 → 수정 → 재검증
가족 프로필(첫째 아들: 우유·알류, 아빠: 땅콩)을 만들고 실제 대화를 이어 갑니다. 추천 상품은 모두 Rule Engine을 통과한 것만 나옵니다.
관계·순서를 등록해 두면 "큰애", "남편"처럼 불러도 알아듣고, 모르는 사람이면 추천 전에 되묻습니다.
"""),
    code("""
from app import agent

user = db.login("콜랩데모")
first = db.create_member(user["id"], "첫째 아들", ["우유", "알류"], False, relation="아들", order="첫째")
dad = db.create_member(user["id"], "아빠", ["땅콩"], False, relation="아빠")

def say(message, members, thread="demo"):
    result = agent.chat(user["id"], thread, message, [m["id"] for m in members])
    print(f"🙋 {message}")
    print(f"🤖 {result['reply']}")
    print(f"   (조건 해석: {result['parser']} · {result['llm']})")
    for i, item in enumerate(result["cart"], 1):
        warn = f" · 주의: {', '.join(item['cross_matched'])} 혼입 가능" if item["cross_matched"] else ""
        group = f"[{item['group']}] " if item.get("group") else ""
        print(f"   {i}. {group}{item['name']} | {item['price']:,}원 | {item['status']}{warn}")
    print()
    return result

say("만두 2만원 이내 3개", [first])
say("2번 상품 빼줘", [first])
say("만두 대신 라면", [first])
_ = say("더 저렴하게", [first])
"""),
    code("""
# 여러 프로필을 함께 고르면 알레르겐 합집합으로 검증
r = say("땅콩 없이 밀키트 3개 3만원 이내", [first, dad], thread="family")
print("회피 알레르겐:", r["constraints"]["avoid"])

# 예산이 모자라도 알레르기 조건은 완화하지 않고 조건 변경을 요청
_ = say("라면 3천원 이내 5개", [first], thread="budget")

# 대상이 없으면 추천하지 않고 되묻기
_ = say("만두 3개 담아줘", [], thread="nobody")
"""),
    md("""
### 상황 맞춤 · 묶음 추천 · 없는 상품 유형 · 되묻기
- "소풍" 같은 상황은 그 추천과 그 추천을 고치는 요청에만 적용되고, 다음 새 요청에서는 사라집니다.
- "점심이랑 과자"처럼 여러 종류를 말하면 종류별로 따로 고르고, 상품 DB에 없는 종류는 알려 줍니다.
"""),
    code("""
_ = say("아빠와 아들이 소풍을 가려고 해. 소풍때 먹을 점심이랑 과자 추천해줘", [], thread="picnic")
_ = say("2번 상품 빼줘", [], thread="picnic")            # 소풍 조건 유지
_ = say("큰애랑 남편 저녁 밀키트 3개", [], thread="picnic")  # 새 요청 → 소풍 조건 사라짐, 별칭으로 대상 연결
_ = say("라면하고 만두 2개씩 2만원 이내", [], thread="picnic")
_ = say("엄마랑 아빠 밀키트 2개", [], thread="mom")        # 등록되지 않은 사람 → 되묻기
"""),
    md("""
## 7. Rule Engine 판정 근거
LLM이 아니라 컬리 `알레르기정보` 원문을 사전으로 대조한 결과입니다.
"""),
    code("""
from app.rule_engine import judge, combine_profiles

avoid = combine_profiles([first])
for p in [x for x in products if x["name"].startswith("[차려낸] 햄 가득")][:1] + products[:3]:
    v = judge(p, avoid["avoid"], avoid["strict"])
    print(p["name"])
    print("  표시문:", p["allergy_label"][:120])
    print("  함유:", p["contains"], "| 혼입 가능:", p["cross"])
    print("  → 첫째 기준", v["status"], "-", v["reason"], "\\n")
"""),
    md("## 8. LangGraph 구조"),
    code("""
graph = agent.agent.get_graph()
print(graph.draw_mermaid())
try:
    from IPython.display import Image, display
    display(Image(graph.draw_mermaid_png()))
except Exception as e:
    print("그림 변환은 건너뜀:", type(e).__name__)
"""),
    md("## 9. (선택) 자동 테스트\n추출·19종 매칭·합집합·예산/개수·수정 재검증·정보 누락 제외를 검사합니다. 테스트는 LLM 키 없이 규칙 파서로 돕니다."),
    code("""
!cd {os.getcwd()} && python -m pytest -q
"""),
]


def make_notebook() -> None:
    nb = {"cells": CELLS, "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                                       "language_info": {"name": "python"}, "colab": {"provenance": []}},
          "nbformat": 4, "nbformat_minor": 5}
    NB_PATH.write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{NB_PATH.name}: 셀 {len(CELLS)}개")


if __name__ == "__main__":
    make_zip()
    make_notebook()
