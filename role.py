import json
import re
from typing import Any


def _extract_job_ids(text: str) -> list:
    return re.findall(r"DF-\d+", text)


def _blocked(reason: str) -> dict:
    return {"decision": "BLOCKED", "answer": "", "evidence_sources": [], "steps": [], "block_reason": reason}


def _pass_out(answer: str, ev: list, steps: list) -> dict:
    return {"decision": "PASS", "answer": answer, "evidence_sources": ev, "steps": steps}


_NEGATION_PATTERNS = [
    r"(?:不|禁止|不得|不要|无需|不代表|不能|不需要|不可|勿|别)[^，,。；;！!\n]{0,8}(?:执行|更改|合并|发布|修改|上线|改|设为|改为|设置为|部署|删除)",
    r"(?:do\s*not|don't|doesn't|without|never|no)\s+(?:change|modify|update|set|merge|deploy|publish|release|delete|remove|create|ignore|disregard|override)\b",
]

# Noun suffixes that turn a verb into a compound noun (not an action command)
_NOUN_SUFFIX_AFTER_VERB = r"(?:进度|时间|记录|日志|状态|计划|列表|说明|报告|详情|信息|文档)"

_WRITE_PATTERNS = [
    r"(?:跳过核验|跳过检查).{0,10}(?:直接|然后|再)?\s*(?:修改|执行|合并|发布|上线)",
    r"(?:请|直接|自动)\s*(?:执行|修改|合并|发布|上线)",
    r"执行\s*devflow\.(?:job\.update|pr\.merge|deploy)",
    r"(?:合并|发布|上线)\s*(?:关联的|到|至|将)",
    r"(?:然后|接着|之后|并)\s*(?:执行|修改|合并|发布|上线)",
    r"(?:把|将|对)\s*(?:状态|status)\s*(?:修改|更改|改|设为|设置为|改为)",
    r"(?:修改|更改|设置|改)\s*(?:状态|status)\s*(?:为|成|到)",
    r"(?:update|change|modify|set)\s+(?:(?:the|its|this|that|your|job|task|ticket|record|item)\s+)?(?:status|state)",
    r"(?:update|change|modify|set|merge|deploy|publish|release)\s+(?:the|this|that|its|their)\s+(?:record|item|entry|task|job)",
    r"(?:update|change|modify|set|merge|deploy|publish|release)\s+(?:to|into)\s+(?:production|prod|staging)",
    r"(?:deploy|merge|publish|release)\s+(?:to|into|the|it|this|task|job)",
    r"(?:把|将).{0,20}(?:合并|发布|上线|部署|删除|创建)",
    # Require directional target or explicit object after verb; NOT when followed by noun suffix
    r"(?:部署|删除|创建)(?:到|至|进)\s*(?:生产|线上|正式|测试环境)",
    r"(?:部署|删除|创建)\s*(?:任务|这条|记录)(?!\s*(?:时间|进度))",
    r"(?:delete|remove|create)\s+(?:the\s+)?(?:task|job|ticket|record|item|entry|PR|pr)\b",
    r"^(?:合并|发布|上线|部署|删除|修改)(?!\s*(?:进度|时间|记录|日志|状态|计划|列表|说明|报告|详情|信息|文档))",
]



def _split_into_action_segments(text: str) -> list:
    """Split text into segments, further splitting on '并' and 'and' to isolate actions."""
    # First split by major delimiters
    primary_segments = re.split(
        r"[，,、；;。！？\n]|(?:然后|接着|之后|并且|而且|同时|但|但是|不过|however|but|then)",
        text, flags=re.IGNORECASE,
    )
    result = []
    for seg in primary_segments:
        seg = seg.strip()
        if not seg:
            continue
        # Further split on standalone '并' and 'and' (but not inside '合并')
        sub = re.split(r"(?<!合)并(?!非)|\band\b(?!\s+\w+\s+\w+\s+and)", seg, flags=re.IGNORECASE)
        for s in sub:
            s = s.strip()
            if s:
                result.append(s)
    return result


def _has_unnegated_write_request(text: str) -> bool:
    segments = _split_into_action_segments(text)
    for seg in segments:
        has_write = any(re.search(p, seg, re.IGNORECASE) for p in _WRITE_PATTERNS)
        if not has_write:
            continue
        # Check negation patterns for this segment
        is_neg = any(re.search(p, seg, re.IGNORECASE) for p in _NEGATION_PATTERNS)
        # Also check broader negation: 不代表...合并/发布 pattern (negation scope covers multiple actions)
        if is_neg:
            continue
        # Check if this segment starts with a scope-limiting negation like "不代表"
        # which negates ALL actions in the same segment
        if re.search(r"(?:不代表|不代表自动|不代表会|不代表要)", seg):
            continue
        return True
    return False


def _prohibits_required_tools(text: str) -> bool:
    _REQUIRED_TOOLS = r"(?:devflow\.job\.read|devflow\.catalog\.search)"
    _GENERIC_READ = r"(?:工具|这些工具|上述工具|读取工具|全部工具|所有工具|the\s+tools?|all\s+tools?|these\s+tools?|reading\s+tools?)"
    patterns = [
        rf"(?:不要|禁止|不得|无需|不能|不许|别|勿|不可)\s*(?:调用|使用|执行|运行|访问|读取)\s*(?:{_REQUIRED_TOOLS}|{_GENERIC_READ})",
        rf"(?:跳过|不调用|不使用|不执行|不访问)\s*(?:{_REQUIRED_TOOLS}|{_GENERIC_READ}|工具|读取|所有|全部)",
        rf"(?:do\s*not|don\'t|without|never)\s+(?:calls?|calling|invokes?|invoking|uses?|using|executes?|executing|runs?|running|access(?:es|ing)?|reads?|reading)\s+(?:{_REQUIRED_TOOLS}|{_GENERIC_READ})",
        rf"skip\s+(?:{_REQUIRED_TOOLS}|all\s+(?:read|tools)|the\s+(?:read|tool))",
    ]
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


_INJECTION_BASE_PATTERNS = [
    r"(?:新的系统指令|作为.*指令|以下.*规则)",
    r"(?:忽略|覆盖)(?:所有|全部|一切)?.*规则",
    r"(?:编造|伪造|虚构)(?:一?个|一份|虚假的?)?(?:成功的?)?(?:记录|结果|数据|状态)",
    r"(?:fabricat(?:e|ing|ed)|make\s+up|invent)\s+(?:a|an|the|their|this|that|their)?\s*(?:successful\s+)?(?:task\s+)?(?:record|status|result)\b",
    r"(?:必须.*(?:输出|返回).*(?:PASS|通过))",
    r"(?:跳过.*(?:工具|检查|核验|验证)|skip.*(?:tool|check|read))",
    r"(?:强制|必须|立即|立刻|马上)\s*(?:输出|返回|显示|打印|给出)\s*(?:PASS|通过)",
    r"(?:force|must)\s*(?:output|return|print)\s*(?:PASS|pass)\b",
]

_INJECTION_BROAD_PATTERNS = [r"(?:执行任何|可以.*所有|所有写操作|任何写操作)"]

_NEG_SEGMENT = r"(?:不|禁止|不得|不要|无需|不代表|不能|不需要|不可|勿|别|never|without|don\'t|no\s+)"

_INJECTION_NEG_PREFIX = r"(?:do\s*not|don\'t|never|without|cannot|不|禁止|不得|不要|无需|不能|不需要|不可|勿|别)\s*(?:[，,、；;]\s*)?$"


def _is_injection(text: str) -> bool:
    # English "ignore/disregard/override previous/prior instructions" only counts if NOT negated
    if _has_unnegated_injection_base(text):
        return True
    # Check broad patterns in segments
    segments = re.split(
        r"[，,、；;。！？\n]|(?:然后|接着|之后|并且|而且|同时|但|但是|不过|however|but|then)",
        text, flags=re.IGNORECASE,
    )
    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        has_broad = any(re.search(p, seg, re.IGNORECASE) for p in _INJECTION_BROAD_PATTERNS)
        has_neg = re.search(_NEG_SEGMENT, seg, re.IGNORECASE)
        if has_broad and not has_neg:
            return True
    # Check English ignore-previous in its own segment context
    return _has_unnegated_injection_english(text)


def _has_unnegated_injection_base(text: str) -> bool:
    """Return True if ANY injection base pattern match is NOT negated."""
    segments = re.split(r"[，,、；;。！？\n]", text)
    for seg in segments:
        for pat in _INJECTION_BASE_PATTERNS:
            for m in re.finditer(pat, seg, re.IGNORECASE):
                before = seg[:m.start()]
                if re.search(_INJECTION_NEG_PREFIX, before, re.IGNORECASE):
                    continue
                return True
    return False


def _has_unnegated_injection_english(text: str) -> bool:
    """Check for English 'ignore/disregard/override previous instructions' not preceded by negation."""
    pattern = r"(ignore|disregard|override)\s+(?:all\s+)?(?:previous|prior|above|earlier|the)?\s*(?:instructions?|rules?|prompts?|context|system)"
    segments = re.split(r"[；;.\n]|[,，](?=\s*(?:do\s*not|don\'t|never|please|just|only))", text, flags=re.IGNORECASE)
    for seg in segments:
        for m in re.finditer(pattern, seg, re.IGNORECASE):
            before = seg[:m.start()]
            if re.search(r"(?:do\s*not|don\'t|never|without|cannot)\s+", before, re.IGNORECASE):
                continue
            return True
    return False


def _validate_job_identifier(rec: dict, job_id: str) -> str:
    ids_found = []
    if "jobId" in rec:
        jid = rec["jobId"]
        if not isinstance(jid, str) or not jid.strip():
            return "记录中jobId字段无效。"
        ids_found.append(jid.strip())
    if "id" in rec:
        iid = rec["id"]
        if not isinstance(iid, str) or not iid.strip():
            return "记录中id字段无效。"
        ids_found.append(iid.strip())
    if not ids_found:
        return "记录缺少jobId或id标识字段。"
    for found_id in ids_found:
        if found_id != job_id:
            return f"记录标识'{found_id}'与请求的'{job_id}'不匹配。"
    return ""


def _filter_readonly_tools(catalog: list) -> list:
    result = []
    for item in catalog:
        if not isinstance(item, dict):
            continue
        access = str(item.get("access", "")).strip().upper()
        risk = str(item.get("risk", "")).strip().upper()
        if access == "READ" and risk == "LOW":
            result.append(item)
    return result


def _build_answer(job_records: list, readonly_tools: list) -> str:
    lines = []
    lines.append("[任务状态]")
    for rec in job_records:
        if isinstance(rec, dict):
            jid = rec.get("jobId", rec.get("id", "?"))
            status = rec.get("status", "?")
            title = rec.get("title", rec.get("name", ""))
            lines.append(f"  - {jid}: {status} {title}")
    lines.append("[可用只读工具]")
    if readonly_tools:
        for t in readonly_tools:
            lines.append(f"  - {t.get('name','?')}: {t.get('description','')}")
    else:
        lines.append("  (无)")
    return "\n".join(lines)


async def execute(data: dict, ctx) -> dict:
    text = data.get("text", "")
    if not isinstance(text, str) or not text.strip():
        return _blocked("输入无效或为空白。")

    job_ids = _extract_job_ids(text)
    unique_ids = list(dict.fromkeys(job_ids))

    if len(unique_ids) == 0:
        return _blocked("未检测到DF-数字任务编号。")
    if len(unique_ids) > 1:
        return _blocked(f"多个任务编号({', '.join(unique_ids)})，仅支持单任务。")

    job_id = unique_ids[0]

    if _has_unnegated_write_request(text):
        return _blocked("检测到肯定性写操作请求。")
    if _is_injection(text):
        return _blocked("检测到提示注入或权限覆盖尝试。")
    if _prohibits_required_tools(text):
        return _blocked("检测到禁止调用必需读取工具。")

    try:
        job_result = await ctx.tools.call("devflow.job.read", jobId=job_id)
    except Exception:
        return _blocked("调用devflow.job.read失败。")

    if not isinstance(job_result, list) or len(job_result) == 0:
        return _blocked(f"任务{job_id}无记录。")

    _VALID_STATUSES = {"RUN", "WAIT", "HUMAN", "QUEUED", "DONE", "FAIL", "CANCEL"}
    for rec in job_result:
        if not isinstance(rec, dict) or "status" not in rec:
            return _blocked("记录格式无效。")
        status = rec["status"]
        if not isinstance(status, str) or not status.strip():
            return _blocked("status字段无效。")
        if status.strip().upper() not in _VALID_STATUSES:
            return _blocked(f"status'{status}'不合法。")
        err = _validate_job_identifier(rec, job_id)
        if err:
            return _blocked(err)

    try:
        catalog_result = await ctx.tools.call("devflow.catalog.search", query="")
    except Exception:
        return _blocked("调用devflow.catalog.search失败。")

    if not isinstance(catalog_result, list):
        return _blocked("工具目录返回格式无效。")

    for item in catalog_result:
        if not isinstance(item, dict):
            return _blocked("目录条目无效。")
        if not item.get("name") or not item.get("access") or not item.get("risk"):
            return _blocked("目录条目缺少字段。")

    readonly_tools = _filter_readonly_tools(catalog_result)

    evidence = {
        "job_records": job_result,
        "catalog_raw": catalog_result,
        "readonly_tools": readonly_tools,
    }
    steps = [
        f"提取: {job_id}",
        f"devflow.job.read({job_id}) -> {len(job_result)}条",
        "devflow.catalog.search('') -> 目录",
        f"过滤READ+LOW: {len(readonly_tools)}个",
    ]
    evidence_sources = ["devflow.job.read", "devflow.catalog.search"]

    try:
        prompt_obj = await ctx.prompt("role")
        system_msg = prompt_obj.compile()
    except Exception:
        return _blocked("提示词加载或编译失败。")

    user_msg = json.dumps({"input": text, "evidence": evidence}, ensure_ascii=False)

    try:
        model_output = await ctx.llm.chat(
            [{"role": "system", "content": system_msg}, {"role": "user", "content": user_msg}],
            prompt=prompt_obj,
        )
    except Exception:
        return _blocked("模型推理服务不可用。")

    try:
        parsed = json.loads(model_output)
    except (json.JSONDecodeError, TypeError):
        return _blocked("模型输出无法解析为JSON。")

    if not isinstance(parsed, dict):
        return _blocked("模型输出非对象。")

    decision = parsed.get("decision")
    if decision is None:
        return _blocked("缺少decision字段。")
    if not isinstance(decision, str) or decision.upper() not in ("PASS", "BLOCKED"):
        return _blocked("decision字段无效。")

    if decision.upper() == "BLOCKED":
        return _blocked("模型BLOCKED: " + str(parsed.get("answer", parsed.get("block_reason", ""))))

    answer = _build_answer(job_result, readonly_tools)
    return _pass_out(answer, evidence_sources, steps)
