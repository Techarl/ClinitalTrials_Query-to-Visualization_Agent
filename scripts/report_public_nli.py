"""Publish only a complete, protocol-matched NLI study; never read partial case files or gold."""

import argparse
import hashlib
import json
import math
import random
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZipFile

ARMS = ["hybrid", "cross_encoder", "jev", "luna", "full"]
SEED = 20261101
COMPARISONS = [(arm, baseline) for baseline in ("hybrid", "full") for arm in ARMS if arm != baseline]
COMPARISONS += [("jev", "luna"), ("jev", "cross_encoder")]


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def percentile(values, q):
    ordered = sorted(values)
    if not ordered:
        return None
    index = (len(ordered) - 1) * q
    low, high = math.floor(index), math.ceil(index)
    return ordered[low] + (ordered[high] - ordered[low]) * (index - low)


def wilson(successes, n):
    """Descriptive binomial interval; dependence across trials is not corrected here."""
    if not n:
        return {"successes": 0, "denominator": 0, "rate": None, "wilson95": None}
    if not 0 <= successes <= n:
        raise ValueError("Invalid proportion")
    z, p = 1.959963984540054, successes / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    radius = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return {
        "successes": successes,
        "denominator": n,
        "rate": p,
        "wilson95": [max(0, centre - radius), min(1, centre + radius)],
    }


def paired_cluster_difference(rows, arm, baseline, metric, *, draws=4000, seed=SEED):
    """Resample shared-trial components, retaining paired outcomes and all their cases."""
    groups = defaultdict(lambda: [0, 0])
    for row in rows:
        if metric == "answer_correct" and not row["answer_planned"]:
            continue
        cell = groups[row["shared_trial_component"]]
        cell[0] += int(row["arms"][arm][metric]) - int(row["arms"][baseline][metric])
        cell[1] += 1
    cells = [groups[key] for key in sorted(groups)]
    if not cells:
        return {"denominator": 0, "clusters": 0, "difference": None, "cluster_bootstrap95": None}
    rng, estimates = random.Random(seed), []
    for _ in range(draws):
        sample = rng.choices(cells, k=len(cells))
        estimates.append(sum(cell[0] for cell in sample) / sum(cell[1] for cell in sample))
    n = sum(cell[1] for cell in cells)
    return {
        "denominator": n,
        "clusters": len(cells),
        "difference": sum(cell[0] for cell in cells) / n,
        "cluster_bootstrap95": [percentile(estimates, 0.025), percentile(estimates, 0.975)],
        "draws": draws,
        "seed": seed,
    }


def require_unique(values, description):
    if len(values) != len(set(values)):
        raise ValueError(f"Duplicate {description}")


def validate_and_sanitize(report, protocol, strata, *, expected_cases=500, expected_answers=200):
    """Reject incomplete/mixed runs instead of silently shrinking denominators."""
    identity = protocol["identity"]
    if digest(identity) != protocol["sha256"]:
        raise ValueError("Protocol identity digest mismatch")
    if strata["formal_protocol_sha256"] != protocol["sha256"]:
        raise ValueError("Strata belong to a different formal protocol")
    if strata.get("label_access") is not False or strata.get("outcomes_used") is not False:
        raise ValueError("Strata are not registered input-only")
    params = identity["parameters"]
    ids, answer_ids = params["ids"], params["answer_ids"]
    require_unique(ids, "planned case IDs")
    require_unique(answer_ids, "answer case IDs")
    if len(ids) != expected_cases or len(answer_ids) != expected_answers or not set(answer_ids) <= set(ids):
        raise ValueError("Wrong formal case or answer denominator")
    if params["arms"] != ARMS or params["context_tokens"] != 1024 or params["candidate_limit"] != 24:
        raise ValueError("Unexpected arms or selection budget")
    stratum_ids = [r["case_id"] for r in strata["cases"]]
    require_unique(stratum_ids, "stratum case IDs")
    if set(stratum_ids) != set(ids):
        raise ValueError("Stratum case set differs from protocol")
    if set(answer_ids) != set(ids[:expected_answers]):
        raise ValueError("Answer subset differs from frozen hash-ordered selection")
    if expected_cases == 500:
        registered = strata["cases"]
        counts = (
            sum(r["full_context_fits_1024"] for r in registered),
            sum(r["source_fits_24_candidate_limit"] for r in registered),
            len({r["shared_trial_component"] for r in registered}),
            Counter(r["type"] for r in registered),
            Counter(r["section"] for r in registered),
        )
        if counts != (
            237,
            326,
            113,
            {"Single": 229, "Comparison": 271},
            {"Intervention": 142, "Eligibility": 132, "Results": 106, "Adverse Events": 120},
        ):
            raise ValueError("Registered input strata counts changed")
    indexed = {}
    for source in report["cases"]:
        key = source["id"], source["arm"]
        if key in indexed:
            raise ValueError("Duplicate case/arm result")
        if key[0] not in ids or key[1] not in ARMS:
            raise ValueError("Unexpected case/arm result")
        if source["status"] not in {"completed", "failed"}:
            raise ValueError("Unfinished case/arm result")
        completed = source["status"] == "completed"
        for field in ("recall", "precision"):
            value = source.get(field)
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError("Invalid evidence metric")
        if type(source.get("complete")) is not bool:
            raise ValueError("Missing complete-evidence metric")
        answer_planned = key[0] in answer_ids
        if completed:
            selected, ranking = source.get("selected_ids"), source.get("ranking")
            for items in (selected, ranking):
                if not isinstance(items, list) or any(not isinstance(item, str) for item in items):
                    raise ValueError("Missing or invalid selected/ranked IDs")
                require_unique(items, "selected/ranked evidence IDs")
            if not set(selected) <= set(ranking):
                raise ValueError("Selected evidence outside the ranked pool")
            if key[1] != "full" and len(ranking) > params["candidate_limit"]:
                raise ValueError("Ranking exceeds frozen candidate limit")
            if key[1] == "full" and selected != ranking:
                raise ValueError("Full-text control did not retain its full ranked source")
        if completed and answer_planned:
            if type(source.get("answer_correct")) is not bool or not isinstance(source.get("answer"), dict):
                raise ValueError("Planned answer missing from completed result")
            if type(source["answer"].get("citation_ids_valid")) is not bool:
                raise ValueError("Missing citation validation")
            citations = source["answer"].get("evidence_ids")
            if not isinstance(citations, list) or any(not isinstance(item, str) for item in citations):
                raise ValueError("Missing answer citation IDs")
            if source["answer"]["citation_ids_valid"] != (set(citations) <= set(selected)):
                raise ValueError("Citation membership flag disagrees with selected evidence")
        if not answer_planned and (source.get("answer") or source.get("answer_correct") is not None):
            raise ValueError("Unexpected answer outside frozen subset")
        # Never export statement text, selected sentences, predictions, or official gold.
        indexed[key] = {
            "completed": completed,
            "complete_evidence": completed and source["complete"],
            "evidence_recall": source["recall"] if completed else 0,
            "evidence_precision": source["precision"] if completed else 0,
            "answer_correct": bool(completed and answer_planned and source.get("answer_correct")),
            "citation_ids_valid": bool(
                completed and answer_planned and source.get("answer", {}).get("citation_ids_valid")
            ),
            "nonempty_citations": bool(completed and answer_planned and source["answer"]["evidence_ids"]),
        }
        for field in ("context_tokens", "ranking_seconds"):
            value = source.get(field)
            if completed:
                if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                    raise ValueError("Invalid execution measurement")
                if field == "context_tokens" and (type(value) is not int or (key[1] != "full" and value > params["context_tokens"])):
                    raise ValueError("Context exceeds frozen selection budget")
                indexed[key][field] = value
    if len(indexed) != expected_cases * len(ARMS):
        raise ValueError("Incomplete formal report: every planned case/arm must be terminal")
    for case_id in ids:
        pools = [set(source["ranking"]) for source in report["cases"]
                 if source["id"] == case_id and source["status"] == "completed" and source["arm"] != "full"]
        if pools and any(pool != pools[0] for pool in pools[1:]):
            raise ValueError("Selectors used different candidate pools")
    rows = []
    safe_strata = (
        "case_id",
        "type",
        "section",
        "full_context_fits_1024",
        "source_fits_24_candidate_limit",
        "shared_trial_component",
    )
    for stratum in sorted(strata["cases"], key=lambda r: r["case_id"]):
        rows.append(
            {
                **{k: stratum[k] for k in safe_strata},
                "answer_planned": stratum["case_id"] in answer_ids,
                "arms": {arm: indexed[stratum["case_id"], arm] for arm in ARMS},
            }
        )
    for arm in ARMS:
        claimed = report["summary"][arm]
        computed = summarize(rows)[arm]
        checks = {
            "planned": expected_cases,
            "answer_planned": expected_answers,
            "completed": computed["completed"],
            "complete_evidence": computed["complete_evidence"]["successes"],
            "answer_correct": computed["answer_correct"]["successes"],
            "evidence_recall": computed["mean_evidence_recall"],
            "evidence_precision": computed["mean_evidence_precision"],
        }
        if any(not math.isclose(claimed[key], value, abs_tol=1e-10) for key, value in checks.items()):
            raise ValueError("Raw cases disagree with published runner summary")
    return rows


def summarize(rows):
    output = {}
    n, answer_n = len(rows), sum(r["answer_planned"] for r in rows)
    for arm in ARMS:
        values = [r["arms"][arm] for r in rows]
        answers = [r["arms"][arm] for r in rows if r["answer_planned"]]
        latency = [r["ranking_seconds"] for r in values if "ranking_seconds" in r]
        context_lengths = [r["context_tokens"] for r in values if "context_tokens" in r]
        output[arm] = {
            "planned": n,
            "completed": sum(r["completed"] for r in values),
            "failures": sum(not r["completed"] for r in values),
            "answer_failures": sum(not r["completed"] for r in answers),
            "complete_evidence": wilson(sum(r["complete_evidence"] for r in values), n),
            "answer_correct": wilson(sum(r["answer_correct"] for r in answers), answer_n),
            "valid_citation_ids": wilson(sum(r["citation_ids_valid"] for r in answers), answer_n),
            "nonempty_citations": wilson(sum(r["nonempty_citations"] for r in answers), answer_n),
            "answer_correct_with_nonempty_valid_ids": wilson(
                sum(r["answer_correct"] and r["citation_ids_valid"] and r["nonempty_citations"] for r in answers), answer_n
            ),
            "mean_evidence_recall": sum(r["evidence_recall"] for r in values) / n if n else None,
            "mean_evidence_precision": sum(r["evidence_precision"] for r in values) / n if n else None,
            "implementation_ranking_latency": {
                "successful_samples": len(latency),
                "p50_seconds": percentile(latency, 0.5),
                "p95_seconds": percentile(latency, 0.95),
            },
            "serialized_context_tokens": {
                "successful_samples": len(context_lengths),
                "mean": sum(context_lengths) / len(context_lengths) if context_lengths else None,
                "p50": percentile(context_lengths, 0.5), "p95": percentile(context_lengths, 0.95),
                "scope": "cl100k_base count of selected passage JSON only; excludes question, instructions and model output",
            },
        }
    return output


def aggregate(rows, *, draws=4000):
    subsets = {"all": rows}
    for field in ("full_context_fits_1024", "source_fits_24_candidate_limit", "type", "section"):
        for value in sorted({r[field] for r in rows}, key=str):
            subsets[f"{field}={value}"] = [r for r in rows if r[field] == value]
    output = {}
    for name, subset in subsets.items():
        output[name] = {
            "arms": summarize(subset),
            "paired_differences": {
                f"{arm}-minus-{baseline}": {
                    metric: paired_cluster_difference(subset, arm, baseline, metric, draws=draws)
                    for metric in ("complete_evidence", "answer_correct")
                }
                for arm, baseline in COMPARISONS
            },
        }
    return output


def ledger_summary(ledger):
    events = ledger["events"]
    if ledger["attempts"] != len(events):
        raise ValueError("Ledger attempt count mismatch")
    known = unknown = embeddings = 0.0
    unknown_count = embedding_count = 0
    models, stages = Counter(), defaultdict(lambda: {"attempts": 0, "estimated_or_reserved_usd": 0.0})
    arm_costs = defaultdict(lambda: {"attempts": 0, "known_cost_usd": 0.0,
                                   "unknown_usage_attempts": 0, "unknown_reserved_usd": 0.0,
                                   "estimated_or_reserved_usd": 0.0})
    for event in events:
        metadata = event.get("metadata") or {}
        model = metadata.get("reported_model") or metadata.get("requested_model") or "unknown"
        models[model] += 1
        if event["status"] == "completed":
            cost = event["estimated_cost_usd"]
        else:
            cost = event["reserved_upper_estimate_usd"]
        if type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0:
            raise ValueError("Invalid ledger cost")
        if event["status"] == "completed":
            known += cost
        else:
            unknown += cost
            unknown_count += 1
        arm = event.get("arm") if event.get("arm") in ARMS else "shared_or_unattributed"
        arm_costs[arm]["attempts"] += 1
        arm_costs[arm]["estimated_or_reserved_usd"] += cost
        if event["status"] == "completed":
            arm_costs[arm]["known_cost_usd"] += cost
        else:
            arm_costs[arm]["unknown_usage_attempts"] += 1
            arm_costs[arm]["unknown_reserved_usd"] += cost
        embedding = "embedding" in model
        if embedding:
            embeddings += cost
            embedding_count += 1
        stage = "embeddings" if embedding else event.get("stage", "ranking_or_untagged")
        stages[stage]["attempts"] += 1
        stages[stage]["estimated_or_reserved_usd"] += cost
    if not math.isclose(known + unknown, ledger["estimated_or_reserved_usd"], abs_tol=1e-8):
        raise ValueError("Ledger event costs do not reconcile")
    return {
        "attempts": len(events),
        "known_cost_usd": known,
        "unknown_usage_attempts": unknown_count,
        "unknown_reserved_usd": unknown,
        "estimated_or_reserved_usd": known + unknown,
        "embedding_attempts": embedding_count,
        "embedding_cost_usd": embeddings,
        "model_attempts": dict(models),
        "stages": dict(stages),
        "nli_arm_costs": dict(arm_costs),
        "attribution_note": "Only explicit NLI arm tags are assigned. Embedding and untagged/unknown-model attempts stay shared or unattributed; do not interpret identified embedding counts as complete when usage is missing.",
    }


def accounting(paths):
    """Deduplicate the same physical file, not independent equal-content ledgers."""
    studies, seen_paths, seen_content, skipped, identical = {}, [], {}, [], []
    for name, path in paths.items():
        path = Path(path).resolve(strict=True)
        if any(path.samefile(previous) for previous in seen_paths):
            skipped.append(name)
            continue
        seen_paths.append(path)
        ledger = read(path)
        fingerprint = digest(ledger)
        if fingerprint in seen_content:
            identical.append({"ledger": name, "same_content_as": seen_content[fingerprint]})
        seen_content[fingerprint] = name
        studies[name] = {"ledger_sha256": fingerprint, **ledger_summary(ledger)}
    totals = {
        k: sum(study[k] for study in studies.values())
        for k in (
            "attempts",
            "known_cost_usd",
            "unknown_usage_attempts",
            "unknown_reserved_usd",
            "estimated_or_reserved_usd",
            "embedding_attempts",
            "embedding_cost_usd",
        )
    }
    return {
        "studies": studies,
        "totals": totals,
        "duplicate_ledger_inputs_skipped": skipped,
        "identical_content_ledgers_retained": identical,
        "note": "Provider-reported cost or versioned estimates, not invoices. Unknown usage retains reservations. "
        "Embedding costs are included in totals, not added again. All attempts include failures. "
        "Only the same physical input file is deduplicated. Equal-content independent files remain counted and flagged; "
        "their identity cannot be resolved from content alone. No per-response-ID deduplication: repeated attempts may be separately billable.",
    }


def markdown(summary, costs):
    lines = [
        "# NLI4CT 官方测试集：证据选择与固定模型回答",
        "",
        "主分析保留全部 **500 题**；五组回答使用事先冻结的相同 **200 题**。失败计入分母并计为不成功。",
        "证据完整覆盖衡量选入上下文是否包含全部标注证据；回答正确衡量官方二分类结论。两者不是同一指标。",
        "",
        "| 方法 | 完整证据 / 500 | 回答正确 / 200 | 失败 / 回答子集失败 |",
        "|---|---:|---:|---:|",
    ]
    for arm, row in summary["strata"]["all"]["arms"].items():
        lines.append(
            f"| {arm} | {row['complete_evidence']['successes']} | {row['answer_correct']['successes']} | "
            f"{row['failures']} / {row['answer_failures']} |"
        )
    lines += [
        "",
        "配对差异（百分点，95% 共享试验连通分量 bootstrap 区间）：",
        "",
        "| 对照 | 完整证据差异 | 回答正确差异 |",
        "|---|---:|---:|",
    ]
    for comparison, values in summary["strata"]["all"]["paired_differences"].items():
        cells = []
        for metric in ("complete_evidence", "answer_correct"):
            result = values[metric]
            lo, hi = result["cluster_bootstrap95"]
            cells.append(f"{100 * result['difference']:+.1f} [{100 * lo:+.1f}, {100 * hi:+.1f}]")
        lines.append(f"| {comparison} | {' | '.join(cells)} |")
    lines += [
        "",
        "输入分层包括全文适合 1024 token 的 237 题及其补集、候选不超过 24 句的 326 题及其补集、"
        "Single/Comparison、四类章节。各层结果及配对区间均完整保留在 JSON 中，不能挑选有利子集替代主结果。",
        "分层于运行开始后、审查者读取结果前登记；不声称在首次执行前预注册。",
        "",
        "区间使用固定种子 20261101、4000 次共享试验连通分量重采样，主集合有 113 个连通分量。"
        "同一抽样同时应用于两方法，按题数计算差异。回答子集只重采样含回答题的分量。"
        "绝对比例另列 Wilson 区间，仅为描述性结果：独立样本假设不符合共享试验结构，可能过窄。"
        "分层与多重比较属于探索性分析，区间未进行多重比较校正，不据此宣称确定胜出。",
        "",
        "全文组不受 1024 token 选择上限限制；其余四组共享最多 24 句混合召回候选。"
        "该实验检验给定试验材料内的证据选择，不衡量全库召回或图表正确性。"
        "引用 ID 有效仅说明引用来自选中材料，不能证明引用支持结论。"
        "当回答调用失败导致整组结果未保存时，证据指标也按失败计零；这是正式执行器的端到端计分口径。",
        "1024 token 指 cl100k_base 对候选段落 JSON 的计数，包含段落标识与元数据，"
        "不包含问题、指令、精排请求或输出，不等于各组完整链路输入相同。全文组是额外信息对照。"
        "各组仍采用同一顺序贪心装配器：跳过放不下的候选，继续尝试后续段落，不做截断或相关性阈值筛选。",
        "回答模型与 reasoning 来自冻结协议，五组保持一致。原始声明及完整候选元数据都提供给精排器；"
        "Jev/Luna 另使用相同相关性 rubric，MiniLM 使用固定的查询—候选编码。"
        "这里只比较预定系统配置，不声称各模型经过同等充分调优。",
        "Jev−Luna 与 Jev−cross_encoder 的直接配对区间在未读取正式成绩时补充登记。"
        "引用 ID 的集合合法性允许空列表，因此 JSON 另列非空引用率与“标签正确且有非空合法 ID”的联合指标；"
        "该联合指标仍不证明引用的语义支持或必要证据完整覆盖。",
        "",
        "正式运行中的 Jev 每段新建 HTTP 客户端，Luna 复用客户端，时延包含不同客户端实现开销，"
        "不能当作纯模型速度排序。[独立公平时延控制](JEV_LATENCY_CONTROL.md) 已使用相同持久连接与并发验证这一影响；"
        "其少量开发集结果与本测试质量结果分开报告。原八题研究仍保留在 [原报告](PASSAGE_SELECTION_RESULTS.md)。",
        "排名计时不包括共享 embedding 预热和初召回，也可能包含中断恢复后的评分缓存读取；"
        "不能用它代表端到端耗时或公平的模型服务延迟。",
        "",
        "费用（包括失败、预留与共享 embedding；以下为估算或提供商返回值，并非账单）：",
        "",
        "| 阶段 | 请求 | 已知费用 USD | 未知预留 USD |",
        "|---|---:|---:|---:|",
    ]
    for name, row in costs["studies"].items():
        lines.append(
            f"| {name} | {row['attempts']} | {row['known_cost_usd']:.6f} | {row['unknown_reserved_usd']:.6f} |"
        )
    formal = costs["studies"].get("formal_v2", {})
    if formal.get("nli_arm_costs"):
        lines += ["", "正式运行按显式组别标签归属的费用（每组包括精排和回答尝试）：", "",
                  "| 组别 | 请求 | 已知费用 USD | 未知预留 USD |", "|---|---:|---:|---:|"]
        for name, row in formal["nli_arm_costs"].items():
            lines.append(f"| {name} | {row['attempts']} | {row['known_cost_usd']:.6f} | {row['unknown_reserved_usd']:.6f} |")
        lines += ["", "共享 embedding 与无法归属的请求单列，不平均分摊或重复加到每组。"
                  "本地 MiniLM 的 CPU 时间没有换算成 USD；API 费用不等于完整部署成本。"
                  "无元数据的未知请求不能可靠归类为 embedding，仍保留在总费用中。"]
    total = costs["totals"]
    lines += [
        "",
        f"总计 **{total['attempts']} 次尝试，${total['estimated_or_reserved_usd']:.6f}**；"
        f"其中 {total['unknown_usage_attempts']} 次用量未知，保留 ${total['unknown_reserved_usd']:.6f} 额度。"
        f"Embedding ${total['embedding_cost_usd']:.6f} 已包含在总额中。",
        "",
        "聚合数据：[summary](public-evaluation/nli4ct-summary.json) · "
        "[脱敏逐题指标](public-evaluation/nli4ct-cases.json) · "
        "[费用核对](public-evaluation/nli4ct-accounting.json)。不发布问题正文、原始材料或标准答案。",
        "",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, default=Path("data/nli4ct-official-test-v2"))
    parser.add_argument(
        "--inputs", type=Path, default=Path("data/external-benchmarks/nli4ct-test-inputs.json")
    )
    parser.add_argument("--strata", type=Path, default=Path("docs/public-evaluation/nli-input-strata.json"))
    parser.add_argument("--pilot", type=Path, default=Path("data/public-pilot-nli4ct-v1/ledger.json"))
    parser.add_argument("--aborted", type=Path, default=Path("data/nli4ct-official-test-v1/ledger.json"))
    args = parser.parse_args()
    report_path = args.study / "report.json"
    if not report_path.exists():
        parser.exit(2, "Formal report is not complete; no partial scores read and no outputs written.\n")
    # The runner creates report.json only after all planned executions and final scoring.
    report, protocol, strata = read(report_path), read(args.study / "protocol.json"), read(args.strata)
    rows = validate_and_sanitize(report, protocol, strata)
    if hashlib.sha256(args.inputs.read_bytes()).hexdigest() != strata["input_sha256"]:
        raise ValueError("Registered input file changed")
    tasks = read(args.inputs)
    tasks.sort(key=lambda t: digest(["nli4ct-evidence-v2", t["id"]]))
    if digest(tasks) != protocol["identity"]["parameters"]["dataset"]:
        raise ValueError("Input dataset does not match frozen execution")
    if [t["id"] for t in tasks] != protocol["identity"]["parameters"]["ids"]:
        raise ValueError("Frozen case order differs from input hash ordering")
    archive_path = args.study / "protocol.source.zip"
    with ZipFile(archive_path) as archive:
        for name, expected in protocol["identity"]["code"].items():
            if hashlib.sha256(archive.read(name)).hexdigest() != expected:
                raise ValueError("Frozen source archive mismatch")
    if digest(report["budget"]) != digest(read(args.study / "ledger.json")):
        raise ValueError("Formal ledger changed since final report")
    costs = accounting(
        {"pilot": args.pilot, "aborted_v1": args.aborted, "formal_v2": args.study / "ledger.json"}
    )
    summary = {
        "version": "nli4ct-final-v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "protocol_sha256": protocol["sha256"],
        "source_zip_sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
        "strata_sha256": digest(strata),
        "report_sha256": digest(report),
        "main_denominator": 500,
        "answer_denominator": 200,
        "execution_contract": {
            "answer_model_requested": protocol["identity"]["settings"]["openai_model"],
            "answer_reasoning": protocol["identity"]["settings"]["openai_reasoning_effort"],
            "decision_model_requested": protocol["identity"]["settings"]["openrouter_decision_model"],
            **{key: protocol["identity"]["parameters"][key] for key in (
                "generic_model", "generic_reasoning", "cross_encoder", "context_tokens", "candidate_limit",
                "tokenizer", "case_workers", "rank_workers",
            )},
        },
        "supplementary_comparisons_registered_before_outcome_access": ["jev-minus-luna", "jev-minus-cross_encoder"],
        "strata": aggregate(rows),
        "limitations": [
            "Shared-trial cluster bootstrap; exploratory comparisons without multiplicity correction",
            "Wilson intervals assume independent cases and may understate uncertainty",
            "Full context is exempt from selection budget",
            "Ranking latency includes asymmetric client implementations",
            "Evidence completeness, answer correctness, and citation entailment are distinct",
        ],
    }
    # Publish only after every validation and aggregation succeeds.
    destination = Path("docs/public-evaluation")
    destination.mkdir(parents=True, exist_ok=True)
    for name, value in (("summary", summary), ("cases", {"cases": rows}), ("accounting", costs)):
        target = destination / f"nli4ct-{name}.json"
        temporary = target.with_suffix(".pending")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(target)
    Path("docs/NLI4CT_RESULTS.md").write_text(markdown(summary, costs), encoding="utf-8")
    print(json.dumps({"cases": 500, "answers": 200, "accounting": costs["totals"]}))


if __name__ == "__main__":
    main()
