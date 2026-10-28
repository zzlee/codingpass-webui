"""AI problem generation service for CodingPass (智慧出題 Agent).

Turns one natural-language teacher request into a *complete*, judge-verified
CodingPass problem pack, following the rules of
``codingpass-exam-skill.txt`` (the 出題指南 shipped with the repository):

1. **Prompt** — the skill file is injected as the system prompt and the local
   vLLM endpoint (OpenAI-compatible ``/v1/chat/completions``, model
   ``qwen3.8-flash-nvfp4`` by default) is asked to answer with **one strict
   JSON object** describing the whole problem (statement, reference solution,
   TeDa random generator, wording rules, sample cases...).
2. **Dual-Run validation** — the generated C++ reference is compiled inside
   the sandboxed :mod:`teda_engine` dual-run plumbing: every sample input is
   replayed and compared against the model's promised output, then
   ``generate_random_inputs`` + ``run_dual_run`` produce (by default) 30 hidden
   test cases whose expected answers come from the *reference*, never the LLM.
3. **Pack files** — the verified problem is materialised under
   ``<workspace>/CodingPass/Pxxxx_[題名]/`` with the exact 庫題卡 file names
   the offline importer (:mod:`problem_pack`) auto-discovers, plus an A4 HTML
   statement (``#c00000`` section headings, separated 範例輸入 / 範例輸出
   blocks) and a ``Pxxxx_[題名].zip`` bundle.
4. **Database import** — when *auto_import* is set and a session + teacher are
   supplied, the problem (samples + generated hidden cases, ref code, TeDa
   scripts, PDF path, exam flags) is committed to the database.

Environment overrides::

    CODINGPASS_SKILL_PATH       path of the 出題指南 skill file
    CODINGPASS_WORKSPACE_DIR    workspace root holding Pxxxx_[題名]/ folders
    VLLM_BASE_URL / AI_GEN_CHAT_URL   OpenAI-compatible chat endpoint
    VLLM_MODEL   / AI_GEN_MODEL       model id (default qwen3.8-flash-nvfp4)
    AI_GEN_TIMEOUT_SEC          chat budget (default 180s - packs are verbose)
    AI_GEN_MAX_TOKENS           completion budget (default 16384)
    AI_GEN_TEMPERATURE          sampling temperature (default 0.3)
    AI_GEN_PDF_TIMEOUT          LibreOffice HTML->PDF budget (default 120s)
    LIBREOFFICE_BINARY          libreoffice/soffice override

Every public entry point is **blocking** (HTTP + subprocess + compile + node);
inside FastAPI call it through ``starlette.concurrency.run_in_threadpool``.
"""

from __future__ import annotations

import html as _html
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Tuple, Union

import httpx

try:  # package-style launch: `uvicorn backend.main:app`
    from .. import teda_engine
    from ..config import (
        DEFAULT_STARTER_C,
        DEFAULT_STARTER_CPP,
        MAX_DESCRIPTION_CHARS,
        MAX_EXPECTED_CHARS,
        MAX_MEMORY_LIMIT_MB,
        MAX_SOURCE_CHARS,
        MAX_STDIN_CHARS,
        MAX_TEST_CASES,
        MAX_TIME_LIMIT_SEC,
        MIN_MEMORY_LIMIT_MB,
        MIN_TIME_LIMIT_SEC,
    )
    from ..models import Difficulty, Problem, TestCase, User
    from ..problem_pack import parse_sample_text
except ImportError:  # flat launch: `uvicorn main:app` / `python main.py`
    import teda_engine  # type: ignore[no-redef]
    from config import (  # type: ignore[no-redef]
        DEFAULT_STARTER_C,
        DEFAULT_STARTER_CPP,
        MAX_DESCRIPTION_CHARS,
        MAX_EXPECTED_CHARS,
        MAX_MEMORY_LIMIT_MB,
        MAX_SOURCE_CHARS,
        MAX_STDIN_CHARS,
        MAX_TEST_CASES,
        MAX_TIME_LIMIT_SEC,
        MIN_MEMORY_LIMIT_MB,
        MIN_TIME_LIMIT_SEC,
    )
    from models import Difficulty, Problem, TestCase, User  # type: ignore[no-redef]
    from problem_pack import parse_sample_text  # type: ignore[no-redef]

logger = logging.getLogger("codingpass.ai_problem_generator")

__all__ = [
    "AIProblemGenerationError",
    "generate_problem",
    "next_problem_code",
    "skill_prompt",
]

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = _BACKEND_DIR.parent

#: The 出題指南 that defines every CodingPass pack convention.
DEFAULT_SKILL_PATH = _REPO_ROOT / "codingpass-exam-skill.txt"

#: Workspace root that hosts ``CodingPass/Pxxxx_[題名]/`` folders.
DEFAULT_WORKSPACE_DIR = _REPO_ROOT / "CodingPass"

VLLM_MODEL: str = (
    os.environ.get("AI_GEN_MODEL", "").strip()
    or os.environ.get("VLLM_MODEL", "").strip()
    or "qwen3.8-flash-nvfp4"
)

VLLM_CHAT_URL: str = (
    os.environ.get("AI_GEN_CHAT_URL", "").strip()
    or (
        os.environ.get("VLLM_BASE_URL", "").strip().rstrip("/") + "/chat/completions"
        if os.environ.get("VLLM_BASE_URL", "").strip()
        else ""
    )
    or "http://localhost:8000/v1/chat/completions"
)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


#: Generating a whole JSON pack takes far longer than a chat review.
GEN_TIMEOUT_SEC: float = _env_float("AI_GEN_TIMEOUT_SEC", 180.0)
GEN_MAX_TOKENS: int = max(1024, int(_env_float("AI_GEN_MAX_TOKENS", 16384.0)))
GEN_TEMPERATURE: float = min(max(_env_float("AI_GEN_TEMPERATURE", 0.3), 0.0), 2.0)
PDF_TIMEOUT_SEC: float = _env_float("AI_GEN_PDF_TIMEOUT", 120.0)

#: How many times the model may retry a malformed JSON answer.
MAX_MODEL_ATTEMPTS = 2

#: Diagnostics (compiler output, model complaints) clipped to this many chars.
DETAIL_CLIP = 2_000

#: Anti-cheat baseline merged into every wording-rules file (skill §2.2/Step 5).
DEFAULT_WORDING_RULES = (
    "#term[0;skipComments skipStrings] system\n"
    "#term[0] <chrono>\n"
    "#term[0] <ctime>\n"
    "#regex[1~*;skipComments skipStrings] \\b(for|while)\\b\n"
)

#: ``P1001``-style problem codes; mirrors ``problem_pack._IDENTITY_RE``.
_PROBLEM_CODE_RE = re.compile(r"^([A-Za-z]{1,4})(\d{1,6})$")
_CODE_DIGITS_RE = re.compile(r"^([A-Za-z]{1,4})(\d+)$")
#: Folder / zip names such as ``P1204_萬年月曆`` or ``P1205.zip``.
_CODE_PREFIX_RE = re.compile(r"^P(\d+)", re.IGNORECASE)

#: Characters never allowed inside a generated folder/file component.
_UNSAFE_NAME_RE = re.compile(r"[\x00-\x1f/\\:*?\"<>|]+")


class AIProblemGenerationError(RuntimeError):
    """Generation failed outright (endpoint down, unusable JSON, bad pack)."""


class _ContractError(ValueError):
    """The model answered, but not with a usable problem JSON; triggers a retry."""


# --------------------------------------------------------------------------- #
# Skill prompt
# --------------------------------------------------------------------------- #

_SKILL_CACHE: Dict[str, str] = {}


def skill_path() -> Path:
    """Active 出題指南 path (``CODINGPASS_SKILL_PATH`` override honoured)."""
    raw = os.environ.get("CODINGPASS_SKILL_PATH", "").strip()
    return Path(raw) if raw else DEFAULT_SKILL_PATH


def skill_prompt() -> str:
    """Read (and cache) the CodingPass 出題指南 used as the system prompt.

    Raises:
        AIProblemGenerationError: the skill file is missing or unreadable —
            generating without it would silently break the 庫題卡 conventions.
    """
    path = skill_path()
    key = str(path)
    cached = _SKILL_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise AIProblemGenerationError(
            f"CodingPass skill prompt not readable at {path}: {exc}"
        ) from exc
    if not text.strip():
        raise AIProblemGenerationError(f"CodingPass skill prompt is empty: {path}")
    _SKILL_CACHE[key] = text
    return text


# --------------------------------------------------------------------------- #
# Small text helpers
# --------------------------------------------------------------------------- #


def _clip(text: Optional[str], limit: int = DETAIL_CLIP) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n[... clipped {len(text) - limit} characters]"


def _ensure_trailing_newline(text: str) -> str:
    """Judge payloads stay newline-terminated, like ``problem_pack`` does."""
    if text == "":
        return text
    normalised = text.replace("\r\n", "\n")
    return normalised if normalised.endswith("\n") else normalised + "\n"


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return str(value)


def _safe_component(name: Any, fallback: str = "題目") -> str:
    """Make ``name`` safe to use as one ``Pxxxx_[name]`` path component."""
    text = _coerce_text(name)
    text = _UNSAFE_NAME_RE.sub("", text)
    text = re.sub(r"\s+", "_", text).strip(" ._\u3000")
    text = text.replace("..", "_")[:60].strip(" ._\u3000")
    return text or fallback


def _clamp(value: float, low: float, high: float) -> float:
    return min(max(value, low), high)


def _rule_covers(rules: List[str], baseline: str) -> bool:
    """True when *rules* already constrains the same subject as *baseline*.

    Two rules are compared by directive head (``#term`` / ``#regex`` / ``#root``)
    and the trailing subject token, so ``#term[0] system`` covers
    ``#term[0;skipComments skipStrings] system`` but not ``#term[1] system``.
    """
    head = baseline.split("[", 1)[0].strip()
    subject = baseline.rsplit(" ", 1)[-1].strip()
    quantifier = re.search(r"\[([^\];]*)", baseline)
    wanted_zero = bool(quantifier and quantifier.group(1).strip() == "0")
    for entry in rules:
        entry_head = entry.split("[", 1)[0].strip()
        entry_subject = entry.rsplit(" ", 1)[-1].strip()
        if entry_head != head or entry_subject != subject:
            continue
        if wanted_zero:
            entry_quantifier = re.search(r"\[([^\];]*)", entry)
            if not entry_quantifier or entry_quantifier.group(1).strip() != "0":
                continue  # a *required* occurrence does not cover a ban
        return True
    return False


def _coerce_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        try:
            match = re.search(r"-?\d+(?:\.\d+)?", _coerce_text(value))
            return float(match.group(0)) if match else default
        except (TypeError, ValueError):
            return default


def _coerce_int(value: Any, default: int) -> int:
    return int(_coerce_float(value, float(default)))


# --------------------------------------------------------------------------- #
# Problem code auto-assignment
# --------------------------------------------------------------------------- #


def _codes_in_workspace(workspace: Path) -> List[int]:
    numbers: List[int] = []
    if not workspace.is_dir():
        return numbers
    try:
        entries = sorted(workspace.iterdir())
    except OSError:
        return numbers
    for entry in entries:
        match = _CODE_PREFIX_RE.match(entry.name)
        if match:
            try:
                numbers.append(int(match.group(1)))
            except ValueError:  # pragma: no cover - regex guarantees digits
                pass
    return numbers


def _codes_in_db(db: Any) -> List[int]:
    numbers: List[int] = []
    if db is None:
        return numbers
    try:
        rows = db.query(Problem.problem_code).filter(Problem.problem_code.isnot(None)).all()
    except Exception:  # SQLAlchemy error / closed session: degrade, never crash
        logger.warning("AI generator could not query problem codes from the database")
        return numbers
    for (code,) in rows:
        match = _CODE_DIGITS_RE.match(_coerce_text(code).strip())
        if match and match.group(1).upper() == "P":
            try:
                numbers.append(int(match.group(2)))
            except ValueError:  # pragma: no cover
                pass
    return numbers


def next_problem_code(db: Any = None, workspace_dir: Union[Path, str, None] = None) -> str:
    """Highest ``Pxxxx`` already used in *db* and/or *workspace_dir*, plus one.

    With neither source (or nothing found) this yields ``P1001``, the first
    number of the offline 庫題卡 convention.
    """
    workspace = Path(workspace_dir) if workspace_dir else default_workspace_dir()
    numbers = _codes_in_db(db) + _codes_in_workspace(workspace)
    highest = max(numbers) if numbers else 1000
    return f"P{highest + 1:04d}"


def default_workspace_dir() -> Path:
    """Workspace root holding the ``CodingPass/Pxxxx_[題名]`` folders."""
    raw = os.environ.get("CODINGPASS_WORKSPACE_DIR", "").strip()
    if raw:
        return Path(raw)
    return DEFAULT_WORKSPACE_DIR


# --------------------------------------------------------------------------- #
# vLLM client
# --------------------------------------------------------------------------- #


def _timeout(budget: float) -> httpx.Timeout:
    return httpx.Timeout(budget, connect=min(5.0, budget))


def _chat_completion(messages: List[Dict[str, str]]) -> str:
    """One non-streaming chat completion against the local vLLM server.

    Raises:
        AIProblemGenerationError: transport failure / non-2xx / empty answer.
    """
    body: Dict[str, Any] = {
        "model": VLLM_MODEL,
        "messages": messages,
        "temperature": GEN_TEMPERATURE,
        "max_tokens": GEN_MAX_TOKENS,
        "stream": False,
    }
    # Reasoning checkpoints burn the token budget on chain-of-thought; the
    # switch is vLLM-specific, so a 400 gets the same body resent without it
    # (same dance as ai_tutor._chat_completion).
    thinking_kwargs = {"enable_thinking": False}
    try:
        with httpx.Client(timeout=_timeout(GEN_TIMEOUT_SEC)) as client:
            response = client.post(
                VLLM_CHAT_URL, json={**body, "chat_template_kwargs": thinking_kwargs}
            )
            if response.status_code == 400:
                response = client.post(VLLM_CHAT_URL, json=body)
            response.raise_for_status()
            payload = response.json()
    except httpx.TimeoutException as exc:
        raise AIProblemGenerationError(
            f"the problem generator did not answer within {GEN_TIMEOUT_SEC:.0f}s "
            f"at {VLLM_CHAT_URL}"
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise AIProblemGenerationError(
            f"the problem generator answered HTTP {exc.response.status_code} "
            f"at {VLLM_CHAT_URL}"
        ) from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise AIProblemGenerationError(
            f"the problem generator is unreachable at {VLLM_CHAT_URL}: "
            f"{exc.__class__.__name__}: {exc}"
        ) from exc

    content = _completion_content(payload)
    if not content.strip():
        raise AIProblemGenerationError("the problem generator returned an empty answer")
    return content


def _completion_content(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    choice = choices[0]
    if not isinstance(choice, dict):
        return ""
    message = choice.get("message")
    if isinstance(message, dict):
        return _coerce_text(message.get("content"))
    return _coerce_text(choice.get("text"))


# --------------------------------------------------------------------------- #
# Strict-JSON recovery from chatty model answers
# --------------------------------------------------------------------------- #

_FENCE_RE = re.compile(r"```(?:json|javascript)?\s*(.*?)```", flags=re.DOTALL | re.IGNORECASE)


def _repair_json(text: str) -> str:
    r"""Escape raw control characters inside strings and drop trailing commas.

    Small checkpoints habitually embed real newlines in long ``description``
    strings and leave ``{... ,}`` trailing commas; both are legal-looking but
    fatal to ``json.loads``. The scan is string-aware, so content inside
    correctly-escaped strings is never mangled.
    """
    out: List[str] = []
    in_string = False
    escaped = False
    length = len(text)
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                out.append(char)
                escaped = False
            elif char == "\\":
                out.append(char)
                escaped = True
            elif char == '"':
                in_string = False
                out.append(char)
            elif char == "\n":
                out.append("\\n")
            elif char == "\r":
                out.append("\\r")
            elif char == "\t":
                out.append("\\t")
            else:
                out.append(char)
            continue
        if char == '"':
            in_string = True
            out.append(char)
            continue
        if char == ",":
            probe = index + 1
            while probe < length and text[probe] in " \t\r\n":
                probe += 1
            if probe < length and text[probe] in "]}":
                continue  # trailing comma: drop it
        out.append(char)
    return "".join(out)


def _first_json_object(text: str) -> str:
    """Balanced-brace substring of the first ``{...}`` object, or the whole text."""
    start = text.find("{")
    if start < 0:
        raise _ContractError("the answer contained no JSON object (no '{' at all)")
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    # Unterminated object: hand the tail over anyway; the repair + loads step
    # produces a friendlier error (and the retry feedback) than a silent give-up.
    return text[start:]


def _parse_model_json(raw: str) -> Dict[str, Any]:
    """Parse one model answer into the problem dict; raises :class:`_ContractError`."""
    candidates: List[str] = []
    fenced = _FENCE_RE.search(raw or "")
    if fenced:
        candidates.append(fenced.group(1))
    candidates.append(raw or "")
    last_error: Optional[Exception] = None
    for candidate in candidates:
        try:
            blob = _first_json_object(candidate.strip())
        except _ContractError as exc:
            last_error = exc
            continue
        for attempt in (blob, _repair_json(blob)):
            try:
                decoded = json.loads(attempt)
            except ValueError as exc:
                last_error = exc
                continue
            if not isinstance(decoded, dict):
                last_error = _ContractError(
                    f"the JSON root is a {type(decoded).__name__}, not an object"
                )
                continue
            return decoded
    raise _ContractError(
        f"the answer was not valid JSON ({last_error.__class__.__name__}: {last_error})"
        if last_error
        else "the answer was not valid JSON"
    )


# --------------------------------------------------------------------------- #
# Prompt construction
# --------------------------------------------------------------------------- #

_JSON_CONTRACT = """\
最終回覆必須是「單一 JSON 物件」本身，不得附加任何解釋文字、前後綴說明或多餘 JSON。
JSON 欄位規格（鍵名必須完全一致）：
{
  "problem_code": "Pxxxx 格式題號，例如 P1205",
  "title": "題名（繁體中文，勿含 / \\ : * ? \\" < > | 等路徑字元）",
  "description": "題目背景與完整題意（Markdown，不含輸入輸出格式）",
  "input_format": "輸入說明（資料型態、範圍、EOF 多筆與否）",
  "output_format": "輸出說明（格式、無解輸出、換行規則）",
  "time_limit_sec": 3.0,
  "memory_limit_mb": 256,
  "starter_code_cpp": "給學生的 C++ 空骨架（僅 include 與 main，不含解題邏輯）",
  "starter_code_c": "給學生的 C 空骨架",
  "ref_code": "C++17 完整正解（庫題卡碼文標碼）：必須以 while (cin >> ...) 或等效方式讀到 EOF；精簡、無註解、不得使用 <chrono>/<ctime>/system",
  "teda_random_script": "TeDa 隨機測資腳本（庫題卡測資０隨輸入）：外層必須是 [30~50;enn]{...}；所有賦值運算一律加 @ 前綴（例 @A = 10;）以免被印出；使用至少兩組 P(機率){...} 涵蓋邊界案例（單一 P() 群組不被トランスパイラ支援）",
  "wording_rules": "TeDa 語量規則（庫題卡碼文限碼），每行一條 #term/#regex 指令，OR 條件一律用 #regex，行內不加自然語言說明",
  "teda_scoring_script": "選填自訂批改腳本（庫題卡測資評判輸出），不需要時填字串 \\"\\"",
  "sample_cases": [
    {"input": "範例輸入（結尾換行）", "output": "對應範例輸出（結尾換行）", "explanation": "一句話說明"}
  ],
  "sample_file_text": "庫題卡測資範例純文字：先整段【範例輸入】區塊、再整段【範例輸出】區塊，兩區塊嚴格分開、嚴禁交錯穿插"
}
硬性要求：
1. sample_cases 至少 3 筆（建議 3~5 筆），必含邊界案例；input/output 必須與 ref_code 的真實執行結果一致。
2. ref_code、teda_random_script、wording_rules、sample_cases 皆為 platform 載入檔，內容精簡、不加說明性註解。
3. 所有字串內的換行必須以 \\n 正確跳脫；JSON 必須可被 json.loads 直接解析。
"""


def _system_prompt() -> str:
    return (
        "你是 CodingPass 平台的「智慧出題 Agent」，負責把教師的自然語言需求轉成完整題庫卡。\n"
        "以下《CodingPass 智慧出題 Agent 指南》是你唯一必須遵守的出題規範"
        "（TeDa 語法、檔案命名、雙軌評測、排版規範）：\n\n"
        f"<skill>\n{skill_prompt()}\n</skill>\n\n"
        "任務進行方式：先在心智中依指南完成出題與自我檢驗，然後依照下列契約輸出。"
        "你的最終回覆不得包含任何思考過程、對話文字或 Markdown 圍欄，只能是一個 JSON 物件。\n\n"
        + _JSON_CONTRACT
    )


def _user_prompt(
    teacher_prompt: str,
    *,
    topic: Optional[str],
    difficulty: str,
    problem_code: str,
    code_assigned: bool,
    is_exam: bool,
    num_random: int,
) -> str:
    return "\n".join(
        [
            "教師出題需求：",
            teacher_prompt.strip(),
            "",
            f"指定議題 (topic)：{topic or '由教師需求自行判斷'}",
            f"難度 (difficulty)：{difficulty or '由教師需求自行判斷'}",
            (
                f"題號 (problem_code)：必須使用 {problem_code}，不得更改。"
                if code_assigned
                else f"題號未指定：請使用 {problem_code}（系統自動編號，勿另選）。"
            ),
            (
                "是否為考試題目 (is_exam)：是（措辭需正式，範例不得過度提示解法）"
                if is_exam
                else "是否為考試題目 (is_exam)：否"
            ),
            "隱藏測資：系統將以 TeDa 腳本搭配標竿正解自動產生約 "
            f"{num_random} 筆，teda_random_script 外層請使用 [30~50;enn]。",
            "",
            "請依系統規範輸出完整 JSON（僅 JSON 本體）。",
        ]
    )


# --------------------------------------------------------------------------- #
# Model answer -> normalised problem spec
# --------------------------------------------------------------------------- #


def _coerce_sample_cases(value: Any) -> List[Dict[str, str]]:
    """Normalise ``sample_cases`` (objects, pairs, or inputs/outputs dicts)."""
    cases: List[Dict[str, str]] = []
    if isinstance(value, dict):
        inputs = value.get("inputs") or value.get("input") or []
        outputs = value.get("outputs") or value.get("output") or []
        explanations = value.get("explanations") or []
        if isinstance(inputs, str):
            inputs = [inputs]
        if isinstance(outputs, str):
            outputs = [outputs]
        if isinstance(inputs, list) and isinstance(outputs, list):
            for index in range(max(len(inputs), len(outputs))):
                stdin = _coerce_text(inputs[index]) if index < len(inputs) else ""
                stdout = _coerce_text(outputs[index]) if index < len(outputs) else ""
                explanation = (
                    _coerce_text(explanations[index])
                    if isinstance(explanations, list) and index < len(explanations)
                    else ""
                )
                cases.append(
                    {"input": stdin, "output": stdout, "explanation": explanation}
                )
        return cases

    if not isinstance(value, list):
        return cases
    for item in value:
        if isinstance(item, dict):
            stdin = _coerce_text(
                item.get("input")
                if item.get("input") is not None
                else item.get("stdin", item.get("sample_input"))
            )
            stdout = _coerce_text(
                item.get("output")
                if item.get("output") is not None
                else item.get("expected_output", item.get("expected", item.get("sample_output")))
            )
            explanation = _coerce_text(item.get("explanation") or item.get("explain") or "")
            if stdin or stdout:
                cases.append({"input": stdin, "output": stdout, "explanation": explanation})
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            cases.append(
                {
                    "input": _coerce_text(item[0]),
                    "output": _coerce_text(item[1]),
                    "explanation": _coerce_text(item[2]) if len(item) > 2 else "",
                }
            )
    return cases


def _build_spec(
    raw: Dict[str, Any],
    *,
    requested_code: Optional[str],
    code_assigned: bool,
    fallback_code: str,
    requested_difficulty: str,
    warnings: List[str],
) -> Dict[str, Any]:
    """Validate and normalise one raw model dict; raises :class:`_ContractError`."""

    def text(key: str, default: str = "") -> str:
        value = raw.get(key)
        if value is None:
            return default
        if isinstance(value, (dict, list)):
            # A model that returns {"description": {"text": ...}} etc.
            if isinstance(value, dict) and len(value) == 1:
                only = next(iter(value.values()))
                if isinstance(only, str):
                    return only
            return json.dumps(value, ensure_ascii=False, indent=2)
        return _coerce_text(value).strip("\ufeff")

    title = text("title").strip() or text("problem_title").strip()
    if not title:
        raise _ContractError("JSON field 'title' is missing or empty")
    title = title.replace("\n", " ").strip()[:80]

    ref_code = text("ref_code").strip()
    if not ref_code:
        raise _ContractError("JSON field 'ref_code' (庫題卡碼文標碼) is missing or empty")
    if len(ref_code) > MAX_SOURCE_CHARS:
        raise _ContractError(
            f"ref_code is {len(ref_code)} characters, above the {MAX_SOURCE_CHARS} limit"
        )
    if not re.search(r"\bint\s+main\s*\(", ref_code):
        warnings.append("the generated ref_code has no visible 'int main' entry point")

    description = text("description").strip()
    if not description:
        raise _ContractError("JSON field 'description' is missing or empty")

    input_format = text("input_format").strip() or text("input").strip()
    output_format = text("output_format").strip() or text("output").strip()

    cases = _coerce_sample_cases(raw.get("sample_cases"))
    if not cases:
        # The sample file is the authoritative offline artefact: if the model
        # skipped the structured list, recover the pairs from its text blocks.
        file_text = text("sample_file_text")
        pairs = parse_sample_text(file_text) if file_text else []
        cases = [
            {"input": stdin, "output": stdout, "explanation": ""}
            for stdin, stdout in pairs
        ]
    cases = [
        case
        for case in cases
        if case["input"] != "" or case["output"] != ""
    ]
    if not cases:
        raise _ContractError(
            "no usable sample_cases (and sample_file_text held no "
            "【範例輸入】/【範例輸出】 pair)"
        )
    if len(cases) < 3:
        warnings.append(
            f"only {len(cases)} sample case(s); the skill asks for 3~5 covering boundaries"
        )
    for index, case in enumerate(cases, start=1):
        if len(case["input"]) > MAX_STDIN_CHARS or len(case["output"]) > MAX_EXPECTED_CHARS:
            raise _ContractError(f"sample case {index} exceeds the stdin/expected size limits")

    code_candidate = (requested_code or text("problem_code")).strip()
    match = _PROBLEM_CODE_RE.match(code_candidate)
    if code_candidate and not match:
        warnings.append(
            f"problem code {code_candidate!r} is not Pxxxx-shaped; using {fallback_code}"
        )
    if not match:
        problem_code = fallback_code
    else:
        problem_code = match.group(1).upper() + match.group(2)
        if code_assigned and problem_code != requested_code.upper():
            problem_code = requested_code.upper()

    difficulty = (requested_difficulty or text("difficulty") or Difficulty.EASY).strip().lower()
    if difficulty not in Difficulty.ALL:
        # An unknown hint from the teacher or the model can never break the DB
        # CHECK constraint on Problem.difficulty.
        difficulty = Difficulty.EASY

    time_limit = _clamp(
        _coerce_float(raw.get("time_limit_sec"), 3.0),
        MIN_TIME_LIMIT_SEC,
        MAX_TIME_LIMIT_SEC,
    )
    memory_limit = _clamp(
        float(_coerce_int(raw.get("memory_limit_mb"), 256)),
        MIN_MEMORY_LIMIT_MB,
        MAX_MEMORY_LIMIT_MB,
    )

    wording_rules = text("wording_rules").strip()
    if not wording_rules:
        wording_rules = DEFAULT_WORDING_RULES.strip()
        warnings.append("wording_rules missing; the anti-cheat baseline was applied")
    else:
        # Pure rule lines only (skill Step 5); merge the de-duped baseline so a
        # forgotten `system` ban cannot ship a student-runnable hole.
        lines = [
            line.strip()
            for line in wording_rules.replace("\r\n", "\n").split("\n")
            if line.strip() and not line.strip().startswith("//")
        ]
        merged = list(lines)
        for baseline in DEFAULT_WORDING_RULES.strip().split("\n"):
            if not _rule_covers(merged, baseline):
                merged.append(baseline)
        wording_rules = "\n".join(merged)

    return {
        "problem_code": problem_code,
        "title": title,
        "description": description[:MAX_DESCRIPTION_CHARS],
        "input_format": input_format,
        "output_format": output_format,
        "difficulty": difficulty,
        "time_limit_sec": round(float(time_limit), 2),
        "memory_limit_mb": int(memory_limit),
        "starter_code_cpp": text("starter_code_cpp").strip() or DEFAULT_STARTER_CPP,
        "starter_code_c": text("starter_code_c").strip() or DEFAULT_STARTER_C,
        "ref_code": ref_code + "\n",
        "ref_language": "cpp",
        "teda_random_script": text("teda_random_script").strip() + "\n",
        "wording_rules": wording_rules + "\n",
        "teda_scoring_script": text("teda_scoring_script").strip(),
        "sample_cases": cases,
    }


def _request_problem_spec(
    teacher_prompt: str,
    *,
    topic: Optional[str],
    difficulty: str,
    requested_code: Optional[str],
    fallback_code: str,
    is_exam: bool,
    num_random: int,
    warnings: List[str],
) -> Dict[str, Any]:
    """Ask the model (with one contract-retry) for a validated problem spec."""
    code_assigned = bool(requested_code)
    effective_code = (requested_code or fallback_code).upper()
    messages: List[Dict[str, str]] = [
        {"role": "system", "content": _system_prompt()},
        {
            "role": "user",
            "content": _user_prompt(
                teacher_prompt,
                topic=topic,
                difficulty=difficulty,
                problem_code=effective_code,
                code_assigned=code_assigned,
                is_exam=is_exam,
                num_random=num_random,
            ),
        },
    ]

    last_error: Optional[Exception] = None
    for attempt in range(1, MAX_MODEL_ATTEMPTS + 1):
        raw_text = _chat_completion(messages)
        try:
            parsed = _parse_model_json(raw_text)
            spec = _build_spec(
                parsed,
                requested_code=requested_code,
                code_assigned=code_assigned,
                fallback_code=fallback_code,
                requested_difficulty=difficulty,
                warnings=warnings,
            )
            return spec
        except _ContractError as exc:
            last_error = exc
            logger.warning(
                "AI problem generator attempt %d/%d produced unusable output: %s",
                attempt,
                MAX_MODEL_ATTEMPTS,
                exc,
            )
            # Feed the exact complaint back so attempt #2 can fix the JSON.
            messages = messages[:2] + [
                {"role": "assistant", "content": _clip(raw_text, 4_000)},
                {
                    "role": "user",
                    "content": (
                        f"你的回覆無法使用：{exc}。請重新輸出「單一完整 JSON 物件」"
                        "（僅 JSON 本體，字串內換行請用 \\n 跳脫，結尾不得有逗號多餘）。"
                    ),
                },
            ]
    raise AIProblemGenerationError(
        "the model did not return a usable problem JSON "
        f"after {MAX_MODEL_ATTEMPTS} attempt(s): {last_error}"
    )


# --------------------------------------------------------------------------- #
# Dual-run sandbox validation (compiler + TeDa via teda_engine)
# --------------------------------------------------------------------------- #


def _verify_with_reference(
    spec: Dict[str, Any],
    *,
    num_random: int,
    seed: int,
    generate_hidden: bool,
    warnings: List[str],
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Compile the reference in the sandbox and replay every case through it.

    Returns ``(verification, hidden_cases)``.

    * Samples: reference stdout must agree with the model's promised output
      (trimmed-equality, the same pass criterion the judge uses). Mismatches
      are *corrected* to the reference output — in the dual-run model of
      truth the reference speaks — and veto the ``dual_run_verified`` flag.
    * Hidden: ``generate_random_inputs`` + ``run_dual_run`` build the
      ``expected_output`` for ~30 random inputs, never trusting the LLM here.
    """
    verification: Dict[str, Any] = {
        "attempted": True,
        "ref_compiled": False,
        "samples_verified": False,
        "samples_total": len(spec["sample_cases"]),
        "samples_passed": 0,
        "samples_corrected": 0,
        "sample_results": [],
        "hidden_generated": False,
        "hidden_case_count": 0,
        "dual_run_verified": False,
    }
    hidden_cases: List[Dict[str, Any]] = []

    ref_code = spec["ref_code"]
    language = spec["ref_language"]
    time_limit = float(spec["time_limit_sec"])
    # The reference should be comfortably faster than the student budget;
    # clamp so a pathological ref cannot pin a sandbox slot for long.
    ref_time_limit = _clamp(time_limit * 2.0, 3.0, 10.0)

    samples = spec["sample_cases"]
    sample_inputs = [_ensure_trailing_newline(case["input"]) for case in samples]
    try:
        sample_outputs = (
            teda_engine.run_dual_run(
                ref_code, language, sample_inputs, time_limit_sec=ref_time_limit
            )
            if sample_inputs
            else []
        )
        verification["ref_compiled"] = True
    except teda_engine.DualRunError as exc:
        detail = _clip(getattr(exc, "details", "") or str(exc))
        warnings.append(f"reference solution could not be verified in the sandbox: {detail}")
        return verification, hidden_cases
    except ValueError as exc:  # empty ref / bad language: caller bug, still report
        warnings.append(f"reference verification was rejected: {exc}")
        return verification, hidden_cases

    passed = 0
    corrected = 0
    for index, (case, actual) in enumerate(zip(samples, sample_outputs), start=1):
        expected = _ensure_trailing_newline(case["output"])
        comparison = teda_engine.compare_outputs(actual, expected)
        matched = bool(comparison["trimmed_match"])
        record = {
            "index": index,
            "matched": matched,
            "presentation_warning": bool(comparison["presentation_warning"]),
            "expected": _clip(expected, 500),
            "actual": _clip(actual, 500),
        }
        if matched:
            passed += 1
            case["output"] = expected
        else:
            # Dual-run convention: the reference binary is the source of truth
            # for expected answers; keep the pack self-consistent.
            corrected += 1
            warnings.append(
                f"sample {index} disagreed with the reference output; "
                "the expected answer was replaced by the reference's"
            )
            case["output"] = _ensure_trailing_newline(actual)
        verification["sample_results"].append(record)

    verification["samples_passed"] = passed
    verification["samples_corrected"] = corrected
    verification["samples_verified"] = bool(samples) and passed == len(samples)
    # Verified = compiled + every sample agreed with the reference *before* any
    # auto-correction; hidden generation may still veto it below.
    verified = bool(verification["ref_compiled"] and verification["samples_verified"])
    verification["dual_run_verified"] = verified

    if not verification["samples_verified"] and not corrected:
        warnings.append("no sample case matched the reference execution")

    script = (spec["teda_random_script"] or "").strip()
    if not generate_hidden:
        return verification, hidden_cases
    if not script:
        warnings.append(
            "no teda_random_script was provided; the pack ships without hidden cases"
        )
        return verification, hidden_cases
    if len(re.findall(r"\bP\s*\(", script)) == 1:
        # teda_engine's transpiler compiles P-groups into an if/else-if chain;
        # a *single* P() leaves a dangling `else` and node rejects the JS.
        warnings.append(
            "the TeDa script has exactly one P(probability) group; the "
            "transpiler compiles P() into an if/else-if chain, so use 2+ groups "
            "(or plain statements for the remaining cases)"
        )

    try:
        random_inputs = [
            _ensure_trailing_newline(text)
            for text in teda_engine.generate_random_inputs(
                script, num_random=num_random, seed=seed
            )
        ]
        hidden_outputs = teda_engine.run_dual_run(
            ref_code, language, random_inputs, time_limit_sec=ref_time_limit
        )
    except (teda_engine.TeDaError, teda_engine.DualRunError) as exc:
        detail = _clip(getattr(exc, "details", "") or str(exc))
        warnings.append(f"hidden test case generation failed: {detail}")
        verification["dual_run_verified"] = False
        return verification, hidden_cases
    except ValueError as exc:  # pragma: no cover - defensive
        warnings.append(f"hidden test case generation was rejected: {exc}")
        verification["dual_run_verified"] = False
        return verification, hidden_cases

    empty_inputs = 0
    for stdin_text, expected_text in zip(random_inputs, hidden_outputs):
        if not stdin_text.strip():
            empty_inputs += 1
        hidden_cases.append(
            {
                "input": stdin_text,
                "expected_output": _ensure_trailing_newline(expected_text),
                "is_sample": False,
            }
        )
    if empty_inputs:
        warnings.append(
            f"{empty_inputs} random case(s) produced empty stdin; "
            "check the TeDa loop format (enn/esn)"
        )
    verification["hidden_generated"] = bool(hidden_cases)
    verification["hidden_case_count"] = len(hidden_cases)

    verification["dual_run_verified"] = bool(
        verified
        and (verification["hidden_generated"] or num_random <= 0)
    )
    return verification, hidden_cases


# --------------------------------------------------------------------------- #
# Pack artefacts: sample file, A4 HTML statement, folder, zip, PDF
# --------------------------------------------------------------------------- #


def build_sample_file_text(cases: List[Dict[str, str]]) -> str:
    """``*_庫題卡測資範例.txt`` text: one 【範例輸入】 block, then 【範例輸出】.

    The skill forbids interleaved sample layouts (指南 §1.2 / §Step 6), so all
    inputs are concatenated first and all outputs second - the exact layout
    ``problem_pack.parse_sample_text`` recovers with its single-block parser.
    """
    inputs = "".join(_ensure_trailing_newline(case["input"]) for case in cases)
    outputs = "".join(_ensure_trailing_newline(case["output"]) for case in cases)
    return f"【範例輸入】\n{inputs}\n【範例輸出】\n{outputs}"


def _html_block(text: str) -> str:
    """Markdown-ish statement text -> safe HTML paragraphs (headings demoted)."""
    escaped = _html.escape(text or "")
    chunks: List[str] = []
    for line in escaped.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        heading = re.match(r"^#{1,4}\s*(.*)$", stripped)
        if heading:
            chunks.append(
                f'<div class="section-title">{heading.group(1)}</div>'
            )
        else:
            chunks.append(f'<p class="desc">{stripped}</p>')
    return "\n".join(chunks) or "<p class=\"desc\">&nbsp;</p>"


def build_statement_html(
    spec: Dict[str, Any],
    sample_file_text: str,
    *,
    verification: Optional[Dict[str, Any]] = None,
) -> str:
    """A4 single-page statement (指南 §3): #c00000 headings, separated samples."""
    code = spec["problem_code"]
    title = _html.escape(spec["title"])
    inputs_blob, _, outputs_blob = sample_file_text.partition("【範例輸出】")
    inputs_blob = inputs_blob.replace("【範例輸入】", "").strip("\n")
    outputs_blob = outputs_blob.strip("\n")

    hidden_note = ""
    if verification is not None:
        hidden_note = (
            f"（本題另含 {verification.get('hidden_case_count', 0)} 筆由隨機測資腳本"
            "與正解標竿自動產生之隱藏測資）"
        )

    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<title>{code} {title}</title>
<style>
  @page {{ size: A4 portrait; margin: 18mm 20mm 15mm 20mm; }}
  html, body {{ margin: 0; padding: 0; }}
  body {{
    font-family: "Noto Serif CJK TC", "AR PL UMing TW", "PMingLiU", serif;
    font-size: 11pt; line-height: 1.55; text-align: justify; color: #000;
  }}
  h1 {{ text-align: center; font-size: 21pt; font-weight: bold; margin: 0 0 0.5em; }}
  p.desc {{ text-indent: 2em; margin: 0.25em 0; text-align: justify; }}
  .section-title {{
    color: #c00000; font-weight: bold; font-size: 13pt;
    margin: 0.8em 0 0.25em; page-break-after: avoid;
  }}
  pre {{
    font-family: "Noto Sans Mono CJK TC", "Courier New", monospace;
    font-size: 10.5pt; white-space: pre; margin: 0.2em 0 0.5em;
    page-break-inside: avoid;
  }}
</style>
</head>
<body>
<h1>{code} {title}</h1>
{_html_block(spec["description"])}
<div class="section-title">輸入說明</div>
{_html_block(spec["input_format"])}
<div class="section-title">輸出說明</div>
{_html_block(spec["output_format"])}
<div class="section-title">範例輸入</div>
<pre>{_html.escape(inputs_blob)}</pre>
<div class="section-title">範例輸出</div>
<pre>{_html.escape(outputs_blob)}</pre>
<p class="desc">備註：{code} 以 EOF 讀取多筆測資{hidden_note}。</p>
</body>
</html>
"""


def _try_render_pdf(html_path: Path, out_dir: Path) -> tuple[Optional[Path], Optional[str]]:
    """Best-effort HTML -> A4 PDF via headless LibreOffice (指南 Step 6.1)."""
    override = os.environ.get("LIBREOFFICE_BINARY", "").strip()
    binary = (shutil.which(override) if override else None) or shutil.which(
        "libreoffice"
    ) or shutil.which("soffice")
    if binary is None:
        return None, (
            "LibreOffice (libreoffice/soffice) was not found on the server PATH; "
            "the statement was kept as HTML only"
        )
    profile_dir = tempfile.mkdtemp(prefix="cj-lo-profile-")
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": tempfile.gettempdir(),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }
    try:
        subprocess.run(
            [
                binary,
                "--headless",
                f"-env:UserInstallation=file://{profile_dir}",
                "--convert-to",
                "pdf",
                "--outdir",
                str(out_dir),
                str(html_path),
            ],
            capture_output=True,
            timeout=PDF_TIMEOUT_SEC,
            env=env,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return None, f"LibreOffice PDF rendering failed: {exc}"
    finally:
        shutil.rmtree(profile_dir, ignore_errors=True)

    pdf_path = html_path.with_suffix(".pdf")
    if pdf_path.is_file():
        return pdf_path, None
    return None, "LibreOffice ran but produced no PDF; the statement stays HTML"


def _write_pack_files(
    spec: Dict[str, Any],
    sample_file_text: str,
    workspace: Path,
    *,
    html_text: str,
    make_pdf: bool,
) -> Dict[str, Any]:
    """Materialise ``Pxxxx_[題名]/`` with the official 庫題卡 file names."""
    code = spec["problem_code"]
    pack_dir = workspace / f"{code}_{_safe_component(spec['title'])}"
    pack_dir.mkdir(parents=True, exist_ok=True)

    paths: Dict[str, Any] = {"workspace_dir": str(workspace), "pack_dir": str(pack_dir)}

    def write(name: str, content: str) -> Path:
        path = pack_dir / name
        path.write_text(content, encoding="utf-8", newline="\n")
        return path

    paths["reference_cpp"] = str(
        write(f"{code}_庫題卡碼文標碼.cpp", spec["ref_code"])
    )
    paths["sample_file"] = str(
        write(f"{code}_庫題卡測資範例.txt", sample_file_text)
    )
    paths["wording_rules"] = str(
        write(f"{code}_庫題卡碼文限碼.txt", spec["wording_rules"])
    )
    if spec["teda_random_script"].strip():
        paths["teda_random_script"] = str(
            write(f"{code}_庫題卡測資０隨輸入.teda", spec["teda_random_script"])
        )
    else:
        paths["teda_random_script"] = None
    if spec["teda_scoring_script"].strip():
        paths["teda_scoring_script"] = str(
            write(
                f"{code}_庫題卡測資評判輸出.teda",
                spec["teda_scoring_script"] + "\n",
            )
        )
    else:
        paths["teda_scoring_script"] = None

    html_path = write(f"{code}_{_safe_component(spec['title'])}.html", html_text)
    paths["statement_html"] = str(html_path)

    paths["statement_pdf"] = None
    if make_pdf:
        pdf_path, why = _try_render_pdf(html_path, pack_dir)
        if pdf_path is not None:
            paths["statement_pdf"] = str(pdf_path)
        elif why:
            paths["pdf_warning"] = why
    return paths


def _zip_pack(pack_dir: Path) -> Path:
    """Bundle ``pack_dir`` as ``<parent>/<pack name>.zip`` (folder at the root).

    The layout is exactly what :func:`problem_pack.parse_problem_pack` expects
    from an uploaded archive (its ``_find_pack_dir`` descends into wrappers).
    """
    zip_path = pack_dir.parent / f"{pack_dir.name}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for entry in sorted(pack_dir.rglob("*")):
            if entry.is_file():
                arcname = PurePosixPath(
                    pack_dir.name, entry.relative_to(pack_dir)
                ).as_posix()
                archive.write(entry, arcname=arcname)
    return zip_path


# --------------------------------------------------------------------------- #
# Database import
# --------------------------------------------------------------------------- #


def _import_to_db(
    db: Any,
    teacher_id: int,
    spec: Dict[str, Any],
    test_cases: List[Dict[str, Any]],
    *,
    classroom_id: Optional[int],
    is_exam: bool,
    exam_lock_required: bool,
    pdf_path: Optional[str],
    warnings: List[str],
) -> int:
    """Commit the generated problem (+ samples + hidden cases) for *teacher_id*."""
    clash = (
        db.query(Problem)
        .filter(Problem.problem_code == spec["problem_code"])
        .filter(Problem.teacher_id == teacher_id)
        .first()
    )
    if clash is not None:
        raise AIProblemGenerationError(
            f"{spec['problem_code']} is already problem {clash.id} in this teacher's "
            "bank; delete it or pick another problem_code before regenerating."
        )

    statement = spec["description"].strip()
    if spec["input_format"]:
        statement += "\n\n### 輸入說明\n" + spec["input_format"]
    if spec["output_format"]:
        statement += "\n\n### 輸出說明\n" + spec["output_format"]
    statement = statement[:MAX_DESCRIPTION_CHARS]

    cases = list(test_cases)
    if len(cases) > MAX_TEST_CASES:
        kept_samples = [case for case in cases if case["is_sample"]][:MAX_TEST_CASES]
        room = max(0, MAX_TEST_CASES - len(kept_samples))
        cases = kept_samples + [c for c in cases if not c["is_sample"]][:room]
        warnings.append(
            f"the pack produced more than {MAX_TEST_CASES} cases; kept every "
            f"sample and the first {room} random ones"
        )
    if not cases:
        raise AIProblemGenerationError(
            "nothing could be imported: the problem carries no test cases"
        )

    problem = Problem(
        title=spec["title"][:200],
        description=statement,
        difficulty=spec["difficulty"],
        time_limit_sec=spec["time_limit_sec"],
        memory_limit_mb=spec["memory_limit_mb"],
        teacher_id=teacher_id,
        classroom_id=classroom_id,
        starter_code_c=spec["starter_code_c"] or DEFAULT_STARTER_C,
        starter_code_cpp=spec["starter_code_cpp"] or DEFAULT_STARTER_CPP,
        problem_code=spec["problem_code"],
        ref_code=spec["ref_code"],
        ref_language=spec["ref_language"],
        teda_random_script=spec["teda_random_script"] or None,
        wording_rules=spec["wording_rules"],
        pdf_path=pdf_path,
        is_exam=bool(is_exam),
        exam_lock_required=bool(exam_lock_required),
    )
    problem.test_cases = [
        TestCase(
            input=str(case.get("input") or ""),
            expected_output=str(case.get("expected_output") or ""),
            is_sample=bool(case.get("is_sample")),
        )
        for case in cases
    ]
    db.add(problem)
    try:
        db.commit()
        db.refresh(problem)
    except Exception:
        db.rollback()
        raise
    return int(problem.id)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def generate_problem(
    teacher_prompt: str,
    *,
    db: Any = None,
    teacher: Optional[User] = None,
    teacher_id: Optional[int] = None,
    topic: Optional[str] = None,
    difficulty: str = "",
    problem_code: Optional[str] = None,
    is_exam: bool = False,
    exam_lock_required: bool = False,
    classroom_id: Optional[int] = None,
    auto_import: bool = True,
    generate_hidden: bool = True,
    num_random: int = 30,
    seed: int = 42,
    workspace_dir: Union[Path, str, None] = None,
    make_pdf: bool = True,
    make_zip: bool = True,
) -> Dict[str, Any]:
    """Generate, sandbox-verify and deliver one complete CodingPass problem.

    Args:
        teacher_prompt: free-form natural-language request from the teacher.
        db: optional SQLAlchemy session; required only for auto-import and for
            reading existing problem codes.
        teacher / teacher_id: owner of the imported problem (either one).
        topic: optional topic hint injected into the prompt.
        difficulty: ``easy``/``medium``/``hard``; empty lets the model decide.
        problem_code: pin the ``Pxxxx`` code; ``None`` auto-assigns the highest
            used number (database + workspace folders) plus one.
        is_exam / exam_lock_required: exam-mode flags stored on the Problem.
        classroom_id: optional classroom to attach the problem to.
        auto_import: commit to the database when *db* + owner are available;
            otherwise a warning is recorded and ``problem_id`` stays ``None``.
        generate_hidden: run the TeDa generator + reference dual-run to build
            hidden cases (also stored in the DB; pack importers regenerate them).
        num_random / seed: hidden-case count and deterministic TeDa seed.
        workspace_dir: workspace root for ``Pxxxx_[題名]/`` folders; defaults
            to ``CODINGPASS_WORKSPACE_DIR`` or ``<repo>/CodingPass``.
        make_pdf: try headless-LibreOffice HTML -> A4 PDF (best effort).
        make_zip: bundle the pack folder as ``Pxxxx_[題名].zip``.

    Returns:
        Structured dict::

            {
              "problem_code", "title", "difficulty", "time_limit_sec",
              "memory_limit_mb", "description", "input_format", "output_format",
              "starter_code_c", "starter_code_cpp", "ref_code", "ref_language",
              "teda_random_script", "wording_rules", "teda_scoring_script",
              "sample_cases":   [{"input","output","explanation"}, ...],
              "sample_file_text", "sample_test_cases", "hidden_test_cases",
              "test_cases":     [{"input","expected_output","is_sample"}, ...],
              "verification":   {"dual_run_verified", "ref_compiled",
                                 "samples_verified", "sample_results",
                                 "hidden_generated", "hidden_case_count", ...},
              "paths":          {workspace_dir, pack_dir, reference_cpp,
                                 teda_random_script, wording_rules, sample_file,
                                 scoring_script, statement_html, statement_pdf, zip},
              "pack_dir", "zip_path", "pdf_path", "problem_id",
              "model", "ai_powered", "warnings": [...],
            }

    Raises:
        AIProblemGenerationError: the skill file is missing, the vLLM endpoint
            is unreachable, the model cannot produce usable JSON after its
            retry, or the DB import hits a problem-code conflict.
    """
    if not (teacher_prompt or "").strip():
        raise AIProblemGenerationError("the teacher prompt is empty")

    warnings: List[str] = []
    workspace = Path(workspace_dir) if workspace_dir else default_workspace_dir()

    requested_code: Optional[str] = None
    if problem_code:
        match = _PROBLEM_CODE_RE.match(str(problem_code).strip())
        if not match:
            raise AIProblemGenerationError(
                f"problem_code {problem_code!r} must look like P1205"
            )
        requested_code = match.group(1).upper() + match.group(2)
    fallback_code = next_problem_code(db, workspace)

    requested_difficulty = (difficulty or "").strip().lower()
    if requested_difficulty and requested_difficulty not in Difficulty.ALL:
        warnings.append(
            f"unknown difficulty {requested_difficulty!r}; falling back to easy"
        )
        requested_difficulty = ""

    spec = _request_problem_spec(
        teacher_prompt,
        topic=topic,
        difficulty=requested_difficulty,
        requested_code=requested_code,
        fallback_code=fallback_code,
        is_exam=is_exam,
        num_random=num_random,
        warnings=warnings,
    )

    verification, hidden_cases = _verify_with_reference(
        spec,
        num_random=num_random,
        seed=seed,
        generate_hidden=generate_hidden,
        warnings=warnings,
    )
    if verification["dual_run_verified"]:
        logger.info(
            "AI problem %s %s passed dual-run verification (%d samples, %d hidden)",
            spec["problem_code"],
            spec["title"],
            verification["samples_passed"],
            verification["hidden_case_count"],
        )
    else:
        logger.warning(
            "AI problem %s %s shipped UNVERIFIED (ref_compiled=%s, samples=%d/%d)",
            spec["problem_code"],
            spec["title"],
            verification["ref_compiled"],
            verification["samples_passed"],
            verification["samples_total"],
        )

    # Sample outputs may have been corrected to the reference's during verify.
    sample_file_text = build_sample_file_text(spec["sample_cases"])
    sample_test_cases = [
        {
            "input": _ensure_trailing_newline(case["input"]),
            "expected_output": _ensure_trailing_newline(case["output"]),
            "is_sample": True,
        }
        for case in spec["sample_cases"]
    ]

    html_text = build_statement_html(
        spec, sample_file_text, verification=verification
    )
    try:
        workspace.mkdir(parents=True, exist_ok=True)
        paths = _write_pack_files(
            spec, sample_file_text, workspace, html_text=html_text, make_pdf=make_pdf
        )
    except OSError as exc:
        raise AIProblemGenerationError(
            f"could not write the pack folder under {workspace}: {exc}"
        ) from exc
    if paths.get("pdf_warning"):
        warnings.append(paths.pop("pdf_warning"))

    zip_path: Optional[str] = None
    if make_zip:
        try:
            zip_path = str(_zip_pack(Path(paths["pack_dir"])))
        except OSError as exc:  # pragma: no cover - disk full etc.
            warnings.append(f"could not zip the pack folder: {exc}")
    paths["zip"] = zip_path

    test_cases = sample_test_cases + hidden_cases

    problem_id: Optional[int] = None
    owner_id = teacher_id if teacher_id is not None else getattr(teacher, "id", None)
    if auto_import:
        if db is None or owner_id is None:
            warnings.append(
                "auto-import skipped: pass db= plus teacher= (or teacher_id=) "
                "to store the problem in the database"
            )
        else:
            problem_id = _import_to_db(
                db,
                int(owner_id),
                spec,
                test_cases,
                classroom_id=classroom_id,
                is_exam=is_exam,
                exam_lock_required=exam_lock_required,
                pdf_path=paths.get("statement_pdf"),
                warnings=warnings,
            )
    else:
        warnings.append("auto-import disabled; the problem lives only on disk/zip")

    return {
        "problem_code": spec["problem_code"],
        "title": spec["title"],
        "difficulty": spec["difficulty"],
        "time_limit_sec": spec["time_limit_sec"],
        "memory_limit_mb": spec["memory_limit_mb"],
        "description": spec["description"],
        "input_format": spec["input_format"],
        "output_format": spec["output_format"],
        "starter_code_c": spec["starter_code_c"],
        "starter_code_cpp": spec["starter_code_cpp"],
        "ref_code": spec["ref_code"],
        "ref_language": spec["ref_language"],
        "teda_random_script": spec["teda_random_script"],
        "wording_rules": spec["wording_rules"],
        "teda_scoring_script": spec["teda_scoring_script"],
        "sample_cases": spec["sample_cases"],
        "sample_file_text": sample_file_text,
        "sample_test_cases": sample_test_cases,
        "hidden_test_cases": hidden_cases,
        "test_cases": test_cases,
        "verification": verification,
        "paths": paths,
        "pack_dir": paths["pack_dir"],
        "zip_path": zip_path,
        "pdf_path": paths.get("statement_pdf"),
        "problem_id": problem_id,
        "model": VLLM_MODEL,
        "ai_powered": True,
        "warnings": warnings,
    }
