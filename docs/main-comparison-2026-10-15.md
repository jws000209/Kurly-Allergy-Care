# main 비교 점검 — 2026-10-15

## 비교 환경

- 현재 작업: feat/langgraph-supervisor, 8e35378. 원격 동일 브랜치와 일치.
- 비교본: origin/main, 6d18ce9. 별도 detached worktree `../kurly-allergy-care-main-review`에 내려받음.
- 현재 브랜치를 전환하거나 병합하지 않았으며, 실사용 DB/외부 GPT/실제 컬리 담기를 사용하지 않음.

## 실제 병합 충돌 시뮬레이션

`git merge-tree --write-tree HEAD origin/main`은 충돌을 반환함. 작업 파일과 브랜치 HEAD는 변경되지 않음.

- AGENTS.md / Handoff.md / README.md
- backend/app/agent.py / cart_composer.py / intent.py
- colab/kac_backend.zip

원격 cart_builder.py 수정과 로컬 cart_composer.py 이름 변경이 겹치므로 원격 파일을 그대로 덮어쓰지 않아야 함. ZIP은 소스 통합 뒤 재생성할 대상.

## 원격 변경의 실제 필요성

| 변경 | 현재 확인 결과 | 반영 판단 |
|---|---|---|
| replacement_terms | 같은 묶음 분류에서 만두→치킨으로 가격 교체될 수 있음 | 현재 cart_composer에 맞춰 선택 반영 권장 |
| 목록이 빈 뒤 숫자·예산만 바꿀 때 update_constraints | 마지막 상품을 뺀 뒤 ‘2개로’에서 대화 추가 회피 조건이 사라짐 | 조건 연속성 버그 수정으로 반영 권장 |
| 수정 요청의 추가 회피 추출 보충 | 가짜 LLM 누락 및 실패 사례는 현재 코드에서도 통과 | 기존 _finalize/allergy_scope와 중복 도입하지 말 것 |
| /api/cart/validate | 현재 API 없음 | 담기 전 최신 프로필·추가 회피 재검증 도입 권장 |
| profileRevision 및 담기 작업 검증 | 현재 확장에 없음 | API와 background/content를 함께 반영해야 함 |

원격 검증 API의 excluded_allergens 상한19는 현재39종 체계에 맞게 검토해야 함. 공개 판정 설명에는 현 presentation 정책을 유지하고, RAG 비활성 설정·GPT 모델·프로필 그룹/이름생략·선택후보10개·보조빼기 안내를 덮어쓰지 말 것.

## 검증

- main 원본 backend/tests/test_safety_edits.py: **6 passed in 0.93s**.
- main 원본 extension/tests/cart-validation.test.cjs: **12 passed**. Chrome/fetch는 모의 객체이며 실제 담기 아님.
- 같은 백엔드 테스트를 현재 코드에 임시로 복사해 실행: **1 passed, 5 failed**. 기존 회귀 전체가 실패했다는 의미는 아님.
- 실패: 저렴한 교체에서 상품 종류 변경, 번호만 지정한 교체에서 후보를 찾지 못함, 목록을 비운 뒤 추가 회피 누락, 담기 검증 API 관련2건.
- 통과: LLM이 명시 회피를 빠뜨리거나 호출 실패해도 보충하는 사례.
- 임시 테스트 파일은 실행 후 제거함. 실제 API키·실사용DB·운영 서버에 변경 없음.

## 권장 다음 단계

전체 merge/cherry-pick보다 필요한 변경을 현재 코드에 수동 이식하는 편이 적절함. 우선 담기 검증 API+확장 연결, 다음으로 동일유형 대체와 빈목록 후 조건 유지. 기존의 ‘교체를 신규추천으로 단순화하지 않는다’는 결정을 유지해야 함. 이번 작업에서는 비교·검증만 수행했고 기능 수정은 반영하지 않음.
