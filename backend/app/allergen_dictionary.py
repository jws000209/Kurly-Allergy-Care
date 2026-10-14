"""Read-only XLSX evidence index. No embeddings, uploads, or DB mutations."""
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import os
from pathlib import Path
import re
import unicodedata

from openpyxl import load_workbook

from .allergens import ALLERGEN_NAMES

ROOT = Path(__file__).resolve().parents[2]
NAME_MAP = {"알류(가금류)": "알류", "루핀(루핀콩)": "루핀", "참마(마)": "참마"}


def normalize(text):
    return re.sub(r"[\s\-_'’]+", "", unicodedata.normalize("NFKC", text).casefold())


def dictionary_path():
    configured = os.getenv("KAC_ALLERGEN_DICTIONARY")
    if configured:
        return Path(configured).resolve()
    files = sorted((ROOT / "data").glob("알레르겐_원재료_매칭사전_v1*.xlsx"))
    if len(files) != 1:
        raise ValueError("알레르겐 사전을 하나만 두거나 KAC_ALLERGEN_DICTIONARY를 지정하세요")
    return files[0]


def rows(sheet):
    headers = next(sheet.values)
    for rowno, values in enumerate(sheet.values, 1):
        if rowno == 1 or not values or not values[0]:
            continue
        yield {str(key): value for key, value in zip(headers, values) if key}, rowno


@dataclass
class Dictionary:
    version: str
    filename: str
    entries: tuple
    exceptions: tuple
    master: dict
    sources: tuple
    guide: tuple

    def __post_init__(self):
        self.by_key = {}
        for entry in self.entries:
            if entry["key"]:
                self.by_key.setdefault(entry["key"], []).append(entry)
        self.pattern = re.compile("(?=(" + "|".join(re.escape(k) for k in sorted(self.by_key, key=len, reverse=True)) + "))")

    def retrieve(self, text, *, source="text"):
        from .allergen_context import contexts
        return [h for context in contexts(text, source) for h in self._retrieve_plain(context)]

    def _retrieve_plain(self, context):
        """Retrieve exact/contains evidence, preserving original row policy and provenance."""
        from .extractor import fix_typos
        text = fix_typos(context["quote"])
        query = normalize(text)
        tokens = {normalize(t) for t in re.split(r"[\s,()\[\]{}/·:;%]+", text) if t}
        tokens |= {re.sub(r"(를|을|와|과|등|이|가|은|는)$", "", t) for t in list(tokens)}
        hits = []
        keys = {m.group(1) for m in self.pattern.finditer(query)} | (tokens & self.by_key.keys())
        for entry in (e for k in sorted(keys) for e in self.by_key[k]):
            term = entry["key"]
            if not term:
                continue
            rule = entry["raw"].get("매칭규칙")
            # English words must not match inside unrelated words (egg / eggplant).
            english = bool(re.fullmatch(r"[a-z0-9]+", term))
            matched = term in tokens if rule == "EXACT" else term in query
            if term == "미소":
                matched = term in tokens
            if english:
                words = re.escape(str(entry["raw"]["원재료_매칭어"]).strip())
                matched = bool(re.search(r"(?<![a-z])" + words.replace(r"\ ", r"[\s\-]*") + r"(?![a-z])", text, re.I))
            if not matched:
                continue
            hit = {**entry, **context}
            # Negative declarations are not proof of absence, and need verification.
            negated = context["kind"] == "negative"
            hit["negated"] = negated
            hit["automatic"] = entry["automatic"] and context["kind"] in {"contains", "cross"}
            hits.append(hit)
        # Longer explicit ingredients suppress shorter substrings, e.g. lupin bean / bean.
        strong = [h for h in hits if h["automatic"]]
        def shadowed(hit):
            spans = [m.span() for m in re.finditer(re.escape(hit["key"]), query)]
            longer = [m.span() for s in strong if hit["key"] != s["key"] and hit["key"] in s["key"]
                      for m in re.finditer(re.escape(s["key"]), query)]
            return bool(spans) and all(any(a <= start and end <= b for a, b in longer) for start, end in spans)
        return [h for h in hits if not shadowed(h)]

    def exception_hits(self, text):
        query = normalize(text)
        tokens = {normalize(t) for t in re.split(r"[\s,()\[\]{}/·:;%]+", text)}
        aliases = {"E002": ["bean", "beans"], "E005": [], "E007": ["starch", "modified starch"],
                   "E008": ["flavoring", "flavouring", "natural flavor"],
                   "E009": ["vegetable oil", "vegetable fat"], "E015": ["refined oil"],
                   "E016": ["orange oil", "essential oil"], "E019": ["perilla leaf"]}
        return [e for e in self.exceptions if any(normalize(t) in query for t in
                re.split(r"\s*/\s*", str(e["raw"].get("문제표현", ""))) + aliases.get(e["id"], [])
                if len(normalize(t)) >= 2 or normalize(t) in tokens)]


@lru_cache(maxsize=4)
def _load(path, stamp, size):
    path = Path(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    workbook = load_workbook(path, read_only=False, data_only=True)
    try:
        master = {}
        for raw, row in rows(workbook["39_알레르겐_마스터"]):
            name = NAME_MAP.get(raw["표준알레르겐"], raw["표준알레르겐"])
            if name not in ALLERGEN_NAMES:
                raise ValueError("사전의 표준 알레르겐을 서비스 명칭에 연결할 수 없습니다")
            master[str(raw["알레르겐ID"])] = {"name": name, "raw": raw, "row": row}
        if len(master) != 39:
            raise ValueError("알레르겐 마스터는 39종이어야 합니다")
        entries = []
        for raw, row in rows(workbook["원재료_매칭사전"]):
            aid = str(raw["알레르겐ID"])
            if aid not in master:
                raise ValueError("사전 항목의 알레르겐 ID가 마스터에 없습니다")
            entries.append({"id": str(raw["사전ID"]), "allergen_id": aid, "name": master[aid]["name"],
                            "key": normalize(str(raw.get("정규화키") or raw.get("원재료_매칭어") or "")),
                            "automatic": raw.get("신뢰등급") == "A" and raw.get("자동매칭") == "Y"
                            and raw.get("수동검토") == "N" and raw.get("매칭규칙") in {"EXACT", "CONTAINS_SAFE"},
                            "sheet": "원재료_매칭사전", "row": row, "raw": raw})
        ids = [e["id"] for e in entries]
        if len(ids) != len(set(ids)):
            raise ValueError("중복 사전 ID")
        exceptions = tuple({"id": str(raw["규칙ID"]), "sheet": "예외_검토규칙", "row": row, "raw": raw}
                           for raw, row in rows(workbook["예외_검토규칙"]))
        return Dictionary(digest, path.name, tuple(entries), exceptions, master,
                          tuple(raw for raw, _ in rows(workbook["출처"])),
                          tuple(tuple(r) for r in workbook["사용가이드"].values))
    finally:
        workbook.close()


def get_dictionary():
    path = dictionary_path()
    stat = path.stat()
    return _load(str(path), stat.st_mtime_ns, stat.st_size)
