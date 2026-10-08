"""LLM 연결 점검: .env 의 키로 LangChain 조건 구조화가 실제로 되는지 확인한다.

사용법 (backend 폴더에서):
    .venv\\Scripts\\python scripts\\check_llm.py

키 값은 화면에 출력하지 않는다.
"""
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND / ".env")

from app import intent  # noqa: E402

# 시연에 쓸 문장. (문장, 기대하는 action) — LLM 결과가 규칙 파서와 크게 다른지 함께 본다.
SAMPLES = [
    ("만두 2만원 이내 3개", "create_cart"),
    ("우유·달걀 없이 밀키트 3개", "create_cart"),
    ("첫째랑 아빠 먹을 저녁거리 3만원 안에서 4개 골라줘", "create_cart"),
    ("2번 상품 빼줘", "remove_item"),
    ("만두 대신 라면", "replace_item"),
    ("더 저렴하게", "cheaper"),
    ("알레르기 있는 아이 간식은 어떻게 고르면 좋아?", "question"),
]
MEMBERS = ["첫째", "아빠", "엄마"]
CART = [{"name": "[비비고] 청양 고기만두 200g"}, {"name": "[비비고] 진한 김치고기만두 200g"}]
FIELDS = ["action", "target_profiles", "excluded_allergens", "category_or_purpose", "keywords",
          "budget", "item_count", "item_index", "from_keyword", "to_keyword"]


def short(intent_dict: dict) -> str:
    return ", ".join(f"{k}={intent_dict[k]}" for k in FIELDS if intent_dict.get(k) not in (None, [], ""))


def main() -> int:
    model = intent.llm_model_name()
    if not model:
        print("LLM 키가 없습니다. backend\\.env.example 을 .env 로 복사하고 GOOGLE_API_KEY 를 채워 주세요.")
        return 1
    print(f"사용 모델: {model}\n")

    failures = 0
    for text, expected in SAMPLES:
        cart = CART if expected in {"remove_item", "replace_item", "cheaper"} else []
        llm_result, parser = intent.parse(text, MEMBERS, cart)
        rule_result = intent.rule_parse(text, MEMBERS, cart)
        ok = parser == "llm" and llm_result["action"] == expected
        failures += not ok
        answered = f"  ({intent.last_model})" if parser == "llm" else ""
        print(f"[{'OK ' if ok else '확인'}] {text}{answered}")
        if parser != "llm":
            print(f"      LLM 호출 실패 → 규칙 파서 사용: {intent.last_error}")
        print(f"      LLM : {short(llm_result)}")
        print(f"      규칙: {short(intent._clean(rule_result))}\n")

    if failures:
        print(f"{failures}개 문장을 확인해 주세요. 호출 실패가 '429'/'RESOURCE_EXHAUSTED'면 무료 한도 초과입니다 —"
              " 잠시 뒤 다시 하거나 LLM_MODEL=google_genai:gemini-flash-lite-latest 로 바꿔 보세요.")
    else:
        print("모든 문장이 LLM(LangChain) 경로로 해석됐습니다.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
