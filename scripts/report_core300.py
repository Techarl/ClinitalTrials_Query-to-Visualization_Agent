"""Report the complete frozen Core300 run without inspecting partial formal scores."""

import argparse
import hashlib
import json
import math
import random
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZipFile

from scripts.report_public_nli import accounting, digest, percentile, read, require_unique, wilson

SEED = 20261101


def category(family):
    if family.startswith("single_"):
        return "single"
    if family.startswith(("compound_", "dependent_")):
        return "compound_or_dependent"
    if family.startswith("dialogue_"):
        return "multi_turn"
    if family.startswith("boundary_"):
        return "boundary"
    raise ValueError("Unknown episode family")


def check_identity(envelope):
    if digest(envelope["identity"]) != envelope["sha256"]:
        raise ValueError("Frozen identity digest mismatch")
    return envelope["identity"]


def validate_corpus(corpus, study, *, expected=300):
    identity = check_identity(study)
    episodes = corpus["episodes"]
    require_unique([e["id"] for e in episodes], "episode IDs")
    if len(episodes) != expected or identity["parameters"]["episodes"] != episodes:
        raise ValueError("Frozen episode contract differs from corpus")
    group_splits = defaultdict(set)
    for episode in episodes:
        group_splits[episode["entity_group"]].add(episode["split"])
    if any(len(splits) != 1 for splits in group_splits.values()):
        raise ValueError("Disease group leaks across splits")
    if expected == 300:
        if Counter(e["split"] for e in episodes) != {"development": 60, "calibration": 40, "heldout": 200}:
            raise ValueError("Wrong split denominators")
        if len(group_splits) != 15 or any(
            n != 20 for n in Counter(e["entity_group"] for e in episodes).values()
        ):
            raise ValueError("Expected 15 disease groups of 20 episodes")
        if sum(len(e["turns"]) for e in episodes) != 420:
            raise ValueError("Wrong turn denominator")
        if Counter(category(e["family"]) for e in episodes) != {
            "single": 120,
            "compound_or_dependent": 80,
            "multi_turn": 60,
            "boundary": 40,
        }:
            raise ValueError("Wrong category denominators")
    return episodes


def source_audit(root, environment):
    """Verify frozen manifests and raw contents; publish counts, hashes, and queries only."""
    identity = check_identity(environment)
    for name, expected in identity["source_manifests_sha256"].items():
        if hashlib.sha256(Path(name).read_bytes()).hexdigest() != expected:
            raise ValueError("Frozen source manifest changed")
    populations, all_ids = [], set()
    for path in sorted((root / "snapshots").glob("*/manifest.json")):
        manifest, raw = read(path), read(path.parent / "studies.json")
        ids = [r["protocolSection"]["identificationModule"]["nctId"] for r in raw]
        require_unique(ids, "source trial IDs")
        if digest(raw) != manifest["raw_hash"] or len(raw) != manifest["retrieved_unique_count"]:
            raise ValueError("Raw frozen snapshot hash/count mismatch")
        if not manifest["complete"] or manifest["expected_count"] != len(raw):
            raise ValueError("Incomplete source population")
        if manifest["source_version_before"] != manifest["source_version_after"]:
            raise ValueError("Source version changed during acquisition")
        all_ids.update(ids)
        populations.append(
            {
                k: manifest[k]
                for k in (
                    "dataset_id",
                    "query",
                    "expected_count",
                    "retrieved_unique_count",
                    "complete",
                    "raw_hash",
                    "finished_at",
                    "source_version_after",
                )
            }
        )
    if len(populations) != 15:
        raise ValueError("Expected 15 frozen core populations")
    return {
        "populations": populations,
        "population_count": len(populations),
        "population_record_sum": sum(p["retrieved_unique_count"] for p in populations),
        "unique_trial_count": len(all_ids),
        "runtime_manifest_count": len(identity["source_manifests_sha256"]),
        "note": "The 15 primary populations overlap; sum of population records is not unique trials. "
        "The replay also exposes registered auxiliary snapshots. Frozen official query results "
        "are not an independently adjudicated ontology or universal drug-alias recall benchmark.",
    }


def validate_results(raw, episodes):
    by_id = {e["id"]: e for e in episodes}
    require_unique([r["episode_id"] for r in raw["episodes"]], "result episode IDs")
    if {r["episode_id"] for r in raw["episodes"]} != set(by_id):
        raise ValueError("Final report must contain every planned episode exactly once")
    rows = []
    for source in raw["episodes"]:
        episode = by_id[source["episode_id"]]
        if (
            source["split"] != episode["split"]
            or source.get("family", episode["family"]) != episode["family"]
        ):
            raise ValueError("Result split/family differs from frozen episode")
        if type(source["correct"]) is not bool:
            raise ValueError("Missing boolean episode outcome")
        status = source.get("status", "graded")
        if status not in {"graded", "runner_failed", "not_run"}:
            raise ValueError("Nonterminal episode status")
        failures = source.get("failures", [])
        if status == "graded":
            if source["turns"] != len(episode["turns"]) or len(failures) != source["turns"]:
                raise ValueError("Incomplete dialogue result")
            if source["correct"] != (not any(failures)):
                raise ValueError("Correct flag contradicts turn failures")
        elif source["correct"]:
            raise ValueError("Runner failure cannot be successful")
        codes = Counter(str(code).split(":", 1)[0] for turn in failures for code in turn)
        if status != "graded":
            codes[status] += 1
        row = {
            "episode_id": episode["id"],
            "entity_group": episode["entity_group"],
            "split": episode["split"],
            "family": episode["family"],
            "category": category(episode["family"]),
            "planned_turns": len(episode["turns"]),
            "correct": source["correct"],
            "status": status,
            "failure_codes": dict(codes),
            "reported_model_calls": source.get("model_calls"),
            "reported_unknown_attempts": source.get("unknown_attempts"),
            "elapsed_seconds": source.get("elapsed_seconds"),
        }
        for key in ("reported_model_calls", "reported_unknown_attempts", "elapsed_seconds"):
            value = row[key]
            if value is not None and (
                type(value) not in (int, float) or not math.isfinite(value) or value < 0
            ):
                raise ValueError("Invalid execution measurement")
        rows.append(row)
    for split, claimed in raw["summary"].items():
        selected = [r for r in rows if r["split"] == split]
        if claimed != {"episodes": len(selected), "correct": sum(r["correct"] for r in selected)}:
            raise ValueError("Runner summary differs from raw outcomes")
    return sorted(rows, key=lambda r: r["episode_id"])


def clustered_rate(rows, *, draws=4000):
    groups = defaultdict(lambda: [0, 0])
    for row in rows:
        groups[row["entity_group"]][0] += row["correct"]
        groups[row["entity_group"]][1] += 1
    cells = [groups[key] for key in sorted(groups)]
    if not cells:
        return {"clusters": 0, "cluster_bootstrap95": None}
    rng, samples = random.Random(SEED), []
    for _ in range(draws):
        selected = rng.choices(cells, k=len(cells))
        samples.append(sum(r[0] for r in selected) / sum(r[1] for r in selected))
    return {
        "clusters": len(cells),
        "cluster_bootstrap95": [percentile(samples, 0.025), percentile(samples, 0.975)],
        "draws": draws,
        "seed": SEED,
        "few_cluster_warning": len(cells) < 30,
    }


def summarize(rows, *, draws=4000):
    codes = Counter()
    for row in rows:
        codes.update(row["failure_codes"])
    return {
        **wilson(sum(r["correct"] for r in rows), len(rows)),
        **clustered_rate(rows, draws=draws),
        "planned_turns": sum(r["planned_turns"] for r in rows),
        "status_counts": dict(Counter(r["status"] for r in rows)),
        "incorrect_or_failed": sum(not r["correct"] for r in rows),
        "failure_code_occurrences": dict(codes),
    }


def aggregate(rows, *, draws=4000):
    output = {"all": summarize(rows, draws=draws)}
    for field in ("split", "family", "category", "entity_group"):
        output[field] = {
            value: summarize([r for r in rows if r[field] == value], draws=draws)
            for value in sorted({r[field] for r in rows})
        }
    return output


def tagged_costs(ledger, episodes):
    ids = {e["id"] for e in episodes}
    groups = defaultdict(lambda: {"attempts": 0, "estimated_or_reserved_usd": 0.0})
    for event in ledger["events"]:
        identity = event.get("episode_id")
        key = identity if identity in ids else "unattributed"
        groups[key]["attempts"] += 1
        groups[key]["estimated_or_reserved_usd"] += (
            event["estimated_cost_usd"]
            if event["status"] == "completed"
            else event["reserved_upper_estimate_usd"]
        )
    return dict(groups)


def audit_turns(study, rows):
    """Only called after final completeness validation; export aggregate check identities."""
    counts, statuses, seen, diagnostics = Counter(), Counter(), 0, []
    for row in rows:
        correctness = []
        for number in range(1, row["planned_turns"] + 1):
            path = study / row["episode_id"] / f"turn-{number}.json"
            if not path.exists():
                if row["status"] == "graded":
                    raise ValueError("Missing graded turn artifact")
                continue
            turn = read(path)
            seen += 1
            correctness.append(turn["grade"]["correct"])
            statuses[turn["response"]["status"]] += 1
            if not turn["grade"]["correct"]:
                model_errors, tool_errors = [], []
                for entry in turn.get("journal", []):
                    output = entry.get("output") or {}
                    if entry["kind"] == "model" and (output.get("metadata") or {}).get("error_type"):
                        model_errors.append(output["metadata"]["error_type"])
                    if entry["kind"] == "tool" and output.get("status") == "error":
                        tool_errors.append(
                            {"tool": entry["input"]["name"], "error_type": output.get("error_type")}
                        )
                diagnostics.append(
                    {
                        "episode_id": row["episode_id"],
                        "turn": number,
                        "response_status": turn["response"]["status"],
                        "model_error_types": model_errors,
                        "tool_errors": tool_errors,
                    }
                )
            for check in turn["grade"]["independent_checks"]:
                counts[check.get("oracle", "runtime_receipt_or_field_verifier")] += 1
        if row["status"] == "graded" and all(correctness) != row["correct"]:
            raise ValueError("Saved turn grades disagree with episode result")
    return {
        "saved_turns": seen,
        "response_status_counts": dict(statuses),
        "check_occurrences": dict(counts),
        "failed_turn_diagnostics": diagnostics,
        "note": "Check occurrences are not independent test cases; a chart may receive multiple checks.",
    }


def protocol_text(corpus, sources, study):
    settings = study["identity"]["settings"]
    lines = [
        "# Core300 冻结执行评测协议",
        "",
        "这份说明在正式运行期间、读取正式得分前整理，描述已经冻结的协议，不修改运行。",
        f"执行身份 SHA-256：`{study['sha256']}`。",
        f"执行模型 `{settings['openai_model']}`，reasoning `{settings['openai_reasoning_effort']}`；"
        f"每 run 最多 {settings['v2_max_model_calls']} 次模型调用、{settings['v2_max_tool_calls']} 次工具调用，"
        f"{settings['v2_max_run_seconds']} 秒、估算预算 ${settings['v2_max_estimated_usd']}。"
        f"上下文装配上限 {settings['v2_context_chars']} 字符。",
        "",
        "300 个 episode 来自 15 个疾病–药物群组，每组 20 题；60 个三轮对话使总交互轮次为 420。"
        "开发 60 题 / 3 组，校准 40 题 / 2 组，留出 200 题 / 10 组，群组不跨划分。"
        "模板跨群组重复，因此不能称为 300 个独立手工编写问题。原数据的 `origin=authored` 是兼容字段，"
        "不改变模板派生这一事实。",
        "",
        "| 类别 | episode 数 |",
        "|---|---:|",
        "| 单步查询与图表 | 120 |",
        "| 组合或数据依赖分析 | 80 |",
        "| 三轮对话 | 60 |",
        "| 边界任务 | 40 |",
        "",
        "单步含阶段、状态、国家、研究类型、三种年度日期和七桶直方图。组合任务要求保留两张图；"
        "依赖任务由真实 sponsor 选择结果驱动趋势。对话保留疾病和药物并切换日期字段与粒度。"
        "边界含空日期区间、超出注册信息能力的因果判断，以及仅留出组具有的精确日期散点任务。"
        "开发/校准组另有研究类型加 primary-completion 的组合模板，模板分布并非完全相同。"
        "崩溃恢复与变异测试另计，不算入这 300 题。",
        "",
        "冻结官方查询人口：",
        "",
        "| 查询疾病 | 查询药物 | 记录数 |",
        "|---|---|---:|",
    ]
    for row in sources["populations"]:
        lines.append(
            f"| {row['query']['query.cond']} | {row['query']['query.intr']} | {row['retrieved_unique_count']} |"
        )
    lines += [
        "",
        f"15 份人口快照记录数合计 **{sources['population_record_sum']}**，跨快照去重后 "
        f"**{sources['unique_trial_count']}** 个 NCT ID。完整性、原始记录哈希与获取前后版本均须一致。"
        f"执行环境还登记辅助快照，共 {sources['runtime_manifest_count']} 份 manifest。"
        "任务测量冻结官方查询范围内的执行，不能解释为所有疾病、药物别名的全库召回率。",
        "",
        "评分要求整个 episode 所有轮次通过：状态、人口范围、日期和全部请求图表、依赖选择身份与证据校验。"
        "失败、运行异常和未运行题都保留在预定分母。错误类型按出现次数与 episode 成功率分别报告。",
        "",
        "独立核验覆盖必须准确区分：",
        "",
        "- SQLite 从原始 JSON 独立计算分类计数及 sponsor 共享药物边权、排序和完整边集。",
        "- 独立人口检查从完整父快照重建筛选和 sponsor 分支成员，覆盖所有临床试验图表。",
        "- 时间序列与散点数字由已有原始日期/坐标核验器检查；直方图由已有原始区间核验器检查。"
        "这些不是新增 SQL 数值核验，不能把所有图型称为 SQL oracle。",
        "- 证据及贡献记录另经执行器验证。自动检查不等同于独立人工临床审查。",
        "",
        "主报告分别给出全 300 题及留出 200 题。使用固定种子 20261101、4000 次按疾病群组重采样的 "
        "95% bootstrap 区间，并保留全部任务族、分组与划分结果。总体仅 15 簇、留出仅 10 簇；"
        "小簇数和重复模板限制推断能力。全对/全错时 bootstrap 区间可能退化，并不表示真实误差为零。"
        "少量试验跨疾病群组重叠，按疾病分组也不能完全消除残余相关性。"
        "Wilson 区间作描述性对照，其独立性假设不成立；不据此宣称总体泛化率。",
        "",
        "最终报告只在 `report.json` 包含完整 300 个最终记录后生成；核对 study/environment、输入文件、"
        "源码归档与 source manifest。费用分别列 pilot 和 formal，保留所有失败及未知用量预留。"
        "模型调用 ledger 是费用主账；每题响应中保存的调用数作为辅助，不覆盖异常期间主账尝试。",
        "",
        "运行报告生成器：`python -m scripts.report_core300`。结果与逐题脱敏指标仅在完整运行后发布。",
        "",
    ]
    return "\n".join(lines)


def result_text(summary, sources, costs):
    lines = [
        "# Core300 冻结执行评测结果",
        "",
        "300 个模板派生 episode、15 个疾病–药物群组；留出集为 200 题、10 个群组。"
        "失败保留在分母。详见 [冻结协议](CORE300_PROTOCOL.md)。",
        "",
        "| 集合 | 正确 / 预定题数 | 群组数 | 群组 bootstrap 95% |",
        "|---|---:|---:|---|",
    ]
    for name, row in [("全部", summary["all"]), *summary["split"].items()]:
        lo, hi = row["cluster_bootstrap95"]
        lines.append(
            f"| {name} | {row['successes']} / {row['denominator']} | {row['clusters']} | {lo:.1%}–{hi:.1%} |"
        )
    lines += [
        "",
        "只有 15 / 10 个群组，区间为探索性描述；重复模板和小群组数限制统计推断。"
        "即使区间退化到 100%，也不能保证新的任务全部正确。",
        "",
        "| 任务类别 | 正确 / 预定题数 | 不正确或失败 |",
        "|---|---:|---:|",
    ]
    for name, row in summary["category"].items():
        lines.append(f"| {name} | {row['successes']} / {row['denominator']} | {row['incorrect_or_failed']} |")
    lines += [
        "",
        "全部失败代码（次数，不是互斥失败题数）：",
        "",
        "```json",
        json.dumps(summary["all"]["failure_code_occurrences"], ensure_ascii=False, indent=2),
        "```",
        "",
        f"原始人口合计 {sources['population_record_sum']} 条记录、{sources['unique_trial_count']} 个去重 NCT ID。"
        "新增独立 SQL 数值核验覆盖分类与共享药物网络；时间/散点/直方图仍由已有原始字段核验器负责，"
        "同时所有范围由独立父人口成员检查覆盖。成功指标依赖这些自动评分规则，不等于人工临床验证。",
        "",
        "| 费用阶段 | 调用尝试 | 已知费用 USD | 未知预留 USD |",
        "|---|---:|---:|---:|",
    ]
    for name, row in costs["studies"].items():
        lines.append(
            f"| {name} | {row['attempts']} | {row['known_cost_usd']:.6f} | {row['unknown_reserved_usd']:.6f} |"
        )
    lines += [
        "",
        f"总估算或预留 **${costs['totals']['estimated_or_reserved_usd']:.6f}**，包括失败调用。"
        "费用不是提供商最终账单。正式结果不合并 pilot 得分。",
        "",
        "[汇总与全部分层](public-evaluation/core300-summary.json) · "
        "[逐题指标](public-evaluation/core300-cases.json) · "
        "[来源人口](public-evaluation/core300-sources.json) · "
        "[费用账本汇总](public-evaluation/core300-accounting.json)",
        "",
        f"执行状态与成功指标须分开理解：正式保存 {summary['turn_audit']['saved_turns']} 轮，其中包括按题目要求拒绝不支持因果判断的轮次。"
        "不能把全部正确 episode 都描述成成功生成了图表。下列诊断只引用已保存的错误类型，不推断未记录的根因：",
        "",
        "| Episode | 轮次状态 | 模型调用错误 | 工具错误 |",
        "|---|---|---|---|",
    ]
    for row in summary["turn_audit"]["failed_turn_diagnostics"]:
        models = ", ".join(row["model_error_types"]) or "未记录"
        tools = (
            ", ".join(f"{e['tool']}: {e['error_type'] or '未记录'}" for e in row["tool_errors"]) or "未记录"
        )
        lines.append(f"| `{row['episode_id']}` | {row['response_status']} | {models} | {tools} |")
    lines += [
        "",
        "这些失败均保留在原始分母，未选择性重跑。图工具的 ClientError 未保存具体 Neo4j code 或完整 traceback，"
        "不能据此确定是超时、竞争或其他数据库原因。后续代码修复不改变本次冻结结果。",
        "",
        "另见 [数据依赖关联审计](CORE300_SELECTION_AUDIT.md)：对全部要求依赖选择的轮次核对 anchor、top-N、"
        "scope、lineage 和 artifact dependencies；原始分数与审计分数并列，其他评分规则保留。",
        "[冻结协议文件](public-evaluation/frozen-protocols/core300-protocol.json) 与 "
        "[执行源码归档](public-evaluation/frozen-protocols/core300-source.zip) 标识本次运行版本。",
        "",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, default=Path("data/core300-formal-v1"))
    parser.add_argument("--root", type=Path, default=Path("data/core300-frozen"))
    parser.add_argument("--pilot", type=Path, default=Path("data/core300-pilot-v1/ledger.json"))
    parser.add_argument("--protocol-only", action="store_true")
    args = parser.parse_args()
    report_path = args.study / "report.json"
    if not args.protocol_only and not report_path.exists():
        parser.exit(2, "Core300 is not complete; no partial formal scores read or results written.\n")
    corpus, study, environment = (
        read(args.root / "cases.json"),
        read(args.study / "study.json"),
        read(args.study / "environment.json"),
    )
    episodes = validate_corpus(corpus, study)
    env = check_identity(environment)
    for name, expected in env["source_code_sha256"].items():
        if study["identity"]["code"].get(name) != expected:
            raise ValueError("Study and environment disagree on frozen source code")
    if study["identity"]["code"].get("requirements.lock") != env["dependency_sha256"]:
        raise ValueError("Study and environment dependency hashes differ")
    if hashlib.sha256((args.root / "cases.json").read_bytes()).hexdigest() != env["case_sha256"]:
        raise ValueError("Case file changed since freeze")
    with ZipFile(args.study / "study.source.zip") as archive:
        for name, expected in study["identity"]["code"].items():
            if hashlib.sha256(archive.read(name)).hexdigest() != expected:
                raise ValueError("Frozen source archive mismatch")
    sources = source_audit(args.root, environment)
    protocol = protocol_text(corpus, sources, study)
    if args.protocol_only:
        Path("docs/CORE300_PROTOCOL.md").write_text(protocol, encoding="utf-8")
        print("Protocol documentation written from frozen inputs only; no formal scores read.")
        return
    raw, ledger = read(report_path), read(args.study / "ledger.json")
    if raw["corpus"] != corpus["split_audit"]:
        raise ValueError("Report corpus audit differs from frozen cases")
    rows = validate_results(raw, episodes)
    if digest(raw["budget"]) != digest(ledger):
        raise ValueError("Ledger changed since final report")
    costs = accounting({"pilot": args.pilot, "formal": args.study / "ledger.json"})
    costs["formal_episode_tags"] = tagged_costs(ledger, episodes)
    summary = {
        "version": "core300-final-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "study_sha256": study["sha256"],
        "environment_sha256": environment["sha256"],
        "report_sha256": digest(raw),
        "turn_audit": audit_turns(args.study, rows),
        **aggregate(rows),
        "limits": [
            "Template-derived episodes, not 300 independent authored questions",
            "Only 15 total / 10 heldout disease groups; uncertainty estimates are exploratory",
            "SQL numeric oracle covers categories and shared-drug network, not every chart type",
        ],
    }
    destination = Path("docs/public-evaluation")
    destination.mkdir(parents=True, exist_ok=True)
    for name, value in (
        ("summary", summary),
        ("cases", {"episodes": rows}),
        ("sources", sources),
        ("accounting", costs),
    ):
        target = destination / f"core300-{name}.json"
        temporary = target.with_suffix(".pending")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(target)
    Path("docs/CORE300_PROTOCOL.md").write_text(protocol, encoding="utf-8")
    Path("docs/CORE300_RESULTS.md").write_text(result_text(summary, sources, costs), encoding="utf-8")
    print(json.dumps({"all": summary["all"], "heldout": summary["split"]["heldout"]}))


if __name__ == "__main__":
    main()
