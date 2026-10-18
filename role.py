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
    r"(?:不|禁止|不得|不要|无需|不代表|不能|不需要)\s*(?:执行|更改|合并|发布|修改|上线)",
    r"(?:仅|只)\s*(?:查询|解释|说明|提供|读取|了解|需要)",
    r"(?:仅提供信息|Advice only|only read|read-only)",
]

_WRITE_PATTERNS = [
    r"(?:跳过核验|跳过检查).{0,10}(?:直接|然后|再)?\s*(?:修改|执行|合并|发布|上线)",
    r"(?:请|直接|自动)\s*(?:执行|修改|合并|发布|上线)",
    r"执行\s*devflow\.(?:job\.update|pr\.merge|deploy)",
    r"(?:合并|发布|上线)\s*(?:关联的|到|至|将)",
    r"(?:然后|接着|之后)\s*(?:执行|修改|合并|发布)",
]


def _has_write_request(text: str) -> bool:
    for p in _WRITE_PATTERNS:
        if re.search(p, text):
            return True
    return False


def _is_negated(text: str) -> bool:
    for p in _NEGATION_PATTERNS:
        if re.search(p, text):
            return True
    return False


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
    lines.append("【任务状态】")
    for rec in job_records:
        if isinstance(rec, dict):
            jid = rec.get("id", rec.get("jobId", "未知"))
            status = rec.get("status", "未知")
            title = rec.get("title", rec.get("name", ""))
            lines.append(f"  - {jid}: 状态={status}, 标题={title}")
    lines.append("\n【可用只读工具】(access=READ, risk=LOW)")
    if readonly_tools:
        for t in readonly_tools:
            lines.append(f"  - {t.get('name','?')}: {t.get('description','')}")
    else:
        lines.append("  (无符合条件的只读低风险工具)")
    return "\n".join(lines)


async def execute(data: dict, ctx) -> dict:
    text = data.get("text", "")
    if not text or not text.strip():
        return _blocked("输入为空白，无法提取任务编号。")

    job_ids = _extract_job_ids(text)
    unique_ids = list(dict.fromkeys(job_ids))

    if len(unique_ids) == 0:
        return _blocked("未检测到明确的DF-数字任务编号。")
    if len(unique_ids) > 1:
        return _blocked(f"检测到多个不同任务编号({', '.join(unique_ids)})，仅支持单任务查询。")

    job_id = unique_ids[0]

    if _has_write_request(text) and not _is_negated(text):
        return _blocked("检测到肯定性写操作/发布/合并请求，本岗位仅提供信息查询。")

    # 调用 devflow.job.read
    try:
        job_result = await ctx.tools.call("devflow.job.read", jobId=job_id)
    except Exception:
        return _blocked("调用devflow.job.read失败。")

    if not isinstance(job_result, list) or len(job_result) == 0:
        return _blocked(f"任务{job_id}无记录或查询返回空列表。")

    # 校验每条记录是dict且有status
    for rec in job_result:
        if not isinstance(rec, dict) or "status" not in rec:
            return _blocked(f"任务{job_id}返回的记录格式无效。")

    # 调用 devflow.catalog.search
    try:
        catalog_result = await ctx.tools.call("devflow.catalog.search", query="")
    except Exception:
        return _blocked("调用devflow.catalog.search失败。")

    if not isinstance(catalog_result, list):
        return _blocked("工具目录返回格式无效。")

    for item in catalog_result:
        if not isinstance(item, dict):
            return _blocked("工具目录包含无效条目（非字典类型）。")
        if not item.get("name") or not item.get("access") or not item.get("risk"):
            return _blocked("工具目录条目缺少name/access/risk字段。")

    readonly_tools = _filter_readonly_tools(catalog_result)

    # 组装证据
    evidence = {
        "job_records": job_result,
        "catalog_raw": catalog_result,
        "readonly_tools": readonly_tools,
    }
    steps = [
        f"提取任务编号: {job_id}",
        f"调用devflow.job.read(jobId={job_id})获取{len(job_result)}条记录",
        "调用devflow.catalog.search(query='')获取目录",
        f"过滤只读低风险工具{len(readonly_tools)}个",
    ]
    evidence_sources = ["devflow.job.read", "devflow.catalog.search"]

    # 模型推理
    prompt_obj = await ctx.prompt("role")
    system_msg = prompt_obj.compile()
    user_msg = json.dumps({"input": text, "evidence": evidence}, ensure_ascii=False)

    model_output = await ctx.llm.chat(
        [{"role": "system", "content": system_msg}, {"role": "user", "content": user_msg}],
        prompt=prompt_obj,
    )

    # 严格解析模型输出
    try:
        parsed = json.loads(model_output)
    except (json.JSONDecodeError, TypeError):
        return _blocked("模型输出无法解析为JSON。")

    if not isinstance(parsed, dict):
        return _blocked("模型输出非JSON对象。")

    decision = parsed.get("decision")
    if decision is None:
        return _blocked("模型输出缺少decision字段。")
    if not isinstance(decision, str) or decision.upper() not in ("PASS", "BLOCKED"):
        return _blocked("模型decision字段无效。")

    if decision.upper() == "BLOCKED":
        return _blocked("模型判断BLOCKED: " + str(parsed.get("answer", parsed.get("block_reason", ""))))

    # PASS: 由代码组装answer，不依赖模型answer
    answer = _build_answer(job_result, readonly_tools)
    return _pass_out(answer, evidence_sources, steps)
