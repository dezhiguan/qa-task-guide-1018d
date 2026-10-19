import json
import re
from typing import Any


def _extract_job_ids(text: str) -> list:
    return [m.upper() for m in re.findall(r"(?<![a-zA-Z])DF-\d+(?![a-zA-Z0-9_]|\-\d)", text, re.IGNORECASE)]


def _blocked(reason: str) -> dict:
    return {"decision": "BLOCKED", "answer": "", "evidence_sources": [], "steps": [], "block_reason": reason}


def _pass_out(answer: str, ev: list, steps: list) -> dict:
    return {"decision": "PASS", "answer": answer, "evidence_sources": ev, "steps": steps}


_NEGATION_PREFIXES = re.compile(
    r"(?:不|禁止|不得|不要|无需|不代表|不能|不需要|不可|勿|别|不允许|not|never|without|don\'t)",
    re.IGNORECASE,
)

_WRITE_PATTERNS = [
    r"(?:update|change|modify|set|merge|deploy|publish|release|cancel|delete|remove|close|approve|abort|terminate)\s+DF-\d+",
    r"(?:跳过核验|跳过检查).{0,10}(?:直接|然后|再)?\s*(?:修改|执行|合并|发布|上线)",
    r"(?:请|直接|自动)\s*(?:执行|修改|合并|发布|上线|更新|取消)",
    r"执行\s*devflow\.(?:job\.update|pr\.merge|deploy|job\.cancel)",
    r"(?<![是否已未了过])(?:合并|发布|上线)\s*(?:到|至|将)\s*(?:(?!的\s*(?:时间|进度|计划|安排|情况|详情|信息|日志|报告|列表|状态|结果|记录|方式|方案|步骤|流程)|了吗|没有|多久|何时|是否|怎样|如何)[^\s，,、；;。！？\n])*(?!\s*(?:的\s*(?:时间|进度|计划|安排|情况|详情|信息|日志|报告|列表|状态|结果|记录|方式|方案|步骤|流程)|了吗|没有|多久|何时|是否|怎样|如何|是什么时候|是多少|了吗|中|过|完毕))",
    r"(?<![是否已未了过])(?:合并|发布|上线)\s*(?:到|至|将)\s*(?:(?!\s*\S{0,4}\s*的\s*(?:时间|进度|计划|安排|情况|详情|信息|日志|报告|列表|状态|结果|记录|方式|方案|步骤|流程))[^\s，,、；;。！？\n])*(?!\s*(?:\S{0,4}\s*)?的\s*(?:时间|进度|计划|安排|情况|详情|信息|日志|报告|列表|状态|结果|记录|方式|方案|步骤|流程)|\S{0,4}\s*(?:了吗|没有|多久|何时|是否|怎样|如何|中|过|完毕))",
    r"(?:然后|接着|之后|并)\s*(?:执行|修改|合并|发布|上线|更新|取消)",
    r"(?:把|将|对)\s*(?:状态|status|task|job)\s*(?:修改|更改|改|设为|设置为|改为|更新|取消)",
    r"(?:修改|更改|设置|改|更新|取消)\s*(?:状态|status|任务|task|job|record)\s*(?:为|成|到)?",
    r"(?:update|change|modify|set|cancel)\s+(?:(?:the|its|this|that|your|job|task|ticket|record|item)\s+)?(?:status|state)",
    r"(?:update|change|modify|set|merge|deploy|publish|release|cancel)\s+(?:the|this|that|its|their)\s+(?:record|item|entry|task|job)",
    r"(?:update|change|modify|set|merge|deploy|publish|release|cancel)\s+(?:to|into)\s+(?:production|prod|staging)",
    r"(?:deploy|merge|publish|release|cancel)\s+(?:to|into|the|it|this|task|job)\b",
    r"(?:把|将).{0,20}(?:合并|发布|上线|部署|删除|创建|更新|取消)",
    r"(?:部署|删除|创建|取消)(?:到|至|进)\s*(?:生产|线上|正式|测试环境)",
    r"(?:部署|删除|创建|取消)\s*(?:任务|这条|记录)(?!\s*(?:时间|进度))",
    r"(?:delete|remove|create|cancel)\s+(?:the\s+)?(?:task|job|ticket|record|item|entry|PR|pr)\b",
    r"^(?:合并|发布|上线|部署|删除|修改|更新|取消)(?!\s*(?:进度|时间|记录|日志|状态|计划|列表|说明|报告|详情|信息|文档))",
    r"(?:approve|close)\s+(?:the\s+|this\s+|that\s+|its\s+|their\s+)?(?:PR|pr|pull.?request|task|job|ticket|record)",
    r"(?:批准|关闭)\s*(?:任务|PR|pr|记录|job|ticket)",
    r"(?:把|将)\s*(?:任务|PR|pr|记录|job|ticket)\s*(?:关闭|批准|approve|close)",
]

_PROGRESS_QUERY_RE = re.compile(
    r"(?:查询|查看|了解|看看|告诉我|说明|解释|read|check|status|progress|time|when|what)"
    r".*(?:的\s*(?:时间|进度|状态|情况|计划|安排|详情|信息|日志|报告|结果|记录|方式|方案|步骤|流程)|什么时候|何时|多久|是否|有没有|了吗)",
    re.IGNORECASE,
)


def _is_progress_query(text: str) -> bool:
    return bool(_PROGRESS_QUERY_RE.search(text))


_VERB_TOKENS = r"(?:跳过核验|跳过检查|执行|修改|更改|设置|改|设为|改为|更新|取消|合并|发布|上线|部署|删除|创建|update|change|modify|set|merge|deploy|publish|release|cancel|delete|remove|create)"


def _split_into_action_segments(text: str) -> list:
    primary_segments = re.split(
        r"[，,、；;。！？\n]|(?:然后|接着|之后|并且|而且|同时|但|但是|不过|however|but)",
        text, flags=re.IGNORECASE,
    )
    result = []
    for seg in primary_segments:
        seg = seg.strip()
        if not seg:
            continue
        sub = re.split(r"(?<!合)并(?!非)|(?<=\s)and(?=\s)", seg, flags=re.IGNORECASE)
        for s in sub:
            s = s.strip()
            if s:
                result.append(s)
    return result


_LOCAL_NEG_RE = re.compile(
    r"(?:不必|不用|不要|不需要|不可|不|禁止|不得|无需|不代表|不能|勿|别|不允许|not|never|without|don\'t)"
    r"[\s]*"
    r"(?:实际|自动|执行|主动|擅自|自行|随意|任意|直接|立马|马上|立刻|immediately|directly|ever)?"
    r"[\s]*$",
    re.IGNORECASE,
)
_LOCAL_NEG_BLOCKERS = re.compile(
    r"(?:请|然后|接着|之后|并且|而且|同时|but|and)",
    re.IGNORECASE,
)
_VERB_IN_BLOCKER_RE = re.compile(_VERB_TOKENS, re.IGNORECASE)


def _is_locally_negated(seg: str, verb_start: int) -> bool:
    before = seg[:verb_start]
    if not before:
        return False
    m = _LOCAL_NEG_RE.search(before)
    if not m:
        return False
    gap = before[m.start():]
    if _LOCAL_NEG_BLOCKERS.search(gap):
        return False
    return True


def _has_unnegated_write_request(text: str) -> bool:
    if _is_progress_query(text):
        return False
    segments = _split_into_action_segments(text)
    for seg in segments:
        for pat in _WRITE_PATTERNS:
            for m in re.finditer(pat, seg, re.IGNORECASE):
                verb_in_match = re.search(_VERB_TOKENS, m.group(), re.IGNORECASE)
                if verb_in_match:
                    verb_pos = m.start() + verb_in_match.start()
                    if _is_locally_negated(seg, verb_pos):
                        continue
                else:
                    if _is_locally_negated(seg, m.start()):
                        continue
                return True
    return False


def _prohibits_required_tools(text: str) -> bool:
    _REQUIRED_TOOLS = r"(?:devflow\.job\.read|devflow\.catalog\.search)"
    _GENERIC_READ = r"(?:工具|任何工具|这些工具|上述工具|读取工具|全部工具|所有工具|the\s+tools?|all\s+tools?|any\s+tools?|these\s+tools?|reading\s+tools?)"
    _EN_DET = r"(?:(?:the|either|any|both|all|neither|these|those|such|one|each|every)\s+(?:of\s+(?:the\s+)?)?)?"
    patterns = [
        rf"(?:不必|不用|不要|不需要|禁止|不得|无需|不能|不许|别|勿|不可|不允许|避免)\s*(?:调用|使用|执行|运行|访问|读取)\s*(?:{_REQUIRED_TOOLS}|{_GENERIC_READ})",
        rf"(?:跳过|不调用|不使用|不执行|不访问|避免)\s*(?:{_REQUIRED_TOOLS}|{_GENERIC_READ}|工具|读取|所有|全部)",
        rf"(?:do\s*not|don\'t|without|never|not|avoid(?:s|ing)?)\s+(?:calls?|calling|invokes?|invoking|uses?|using|executes?|executing|runs?|running|access(?:es|ing)?|reads?|reading)\s+{_EN_DET}(?:{_REQUIRED_TOOLS}|{_GENERIC_READ})",
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
    r"(?:output|return|print)\s*(?:PASS|pass)\b",
    r"(?:输出|返回|打印|显示|给出)\s*(?:PASS|pass|通过)\b",
]

_INJECTION_NEG_PREFIX = r"(?:do\s*not|don\'t|never|without|cannot|不必|不用|不要|不需要|不可|不|禁止|不得|无需|不能|勿|别|不允许)(?:\s+(?:实际|自动|执行|主动|擅自|自行|随意|任意|直接|立马|马上|立刻|immediately|directly|ever))*\s*(?:[，,、；;]\s*)?$"


def _is_injection(text: str) -> bool:
    if _has_unnegated_injection_base(text):
        return True
    if _has_unnegated_broad(text):
        return True
    return _has_unnegated_injection_english(text)


_BROAD_PATTERNS = [r"(?:执行任何|可以.*所有|所有写操作|任何写操作)"]
_BROAD_NEG_PREFIX = r"(?:不必|不用|不要|不需要|禁止|不得|不允许|不可|不能|无需|别|勿|不)(?:[，,、；;\s]*)?(?:执行)?(?:[，,、；;\s]*)?$"


def _has_unnegated_broad(text: str) -> bool:
    for pat in _BROAD_PATTERNS:
        for m in re.finditer(pat, text, re.IGNORECASE):
            before = text[max(0, m.start() - 8):m.start()]
            if re.search(_BROAD_NEG_PREFIX, before, re.IGNORECASE):
                continue
            return True
    return False


def _has_unnegated_injection_base(text: str) -> bool:
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
    pattern = r"(ignore|disregard|override)\s+(?:all\s+)?(?:previous|prior|above|earlier|the)?\s*(?:instructions?|rules?|prompts?|context|system)"
    segments = re.split(r"[；;\n]", text, flags=re.IGNORECASE)
    for seg in segments:
        for m in re.finditer(pattern, seg, re.IGNORECASE):
            before = seg[:m.start()]
            if re.search(r"(?:do\s*not|don\'t|never|without|cannot)(?:\s+(?:ever|actually|really|simply|just|merely))*\s*(?:[，,、；;]\s*)?$", before, re.IGNORECASE):
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
    if not isinstance(text, str):
        return _blocked("输入无效：text必须是字符串。")
    if not text.strip():
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

    _VALID_ACCESS = {"READ", "WRITE", "EXEC"}
    _VALID_RISK = {"LOW", "MID", "HIGH"}
    for item in catalog_result:
        if not isinstance(item, dict):
            return _blocked("目录条目无效。")
        _name = item.get("name")
        _access = item.get("access")
        _risk = item.get("risk")
        if not isinstance(_name, str) or not _name.strip():
            return _blocked("目录条目name无效。")
        if not isinstance(_access, str) or _access.strip().upper() not in _VALID_ACCESS:
            return _blocked("目录条目access无效。")
        if not isinstance(_risk, str) or _risk.strip().upper() not in _VALID_RISK:
            return _blocked("目录条目risk无效。")

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
