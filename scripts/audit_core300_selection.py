"""Post-run dependency linkage audit; preserve frozen grades and never read partial runs."""

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from scripts.report_core300 import aggregate, validate_corpus, validate_results
from scripts.report_public_nli import digest, read

REPLACED_CODES = {
    "dependent_selection_missing",
    "invented_or_unrelated_cohorts",
    "dependent_selection_request_mismatch",
}


def dependency_failures(expected, artifacts, journal):
    """Bind the requested anchor/N to the selection actually backing the trend and each lineage."""
    if not expected.get("dependent_selection"):
        return []
    by_handle = {a["result_id"]: a for a in artifacts}
    trends = [
        a for a in artifacts if a["metadata"].get("analysis_parameters", {}).get("operation") == "time_series"
    ]
    selections = [
        e
        for e in journal
        if e["kind"] == "tool"
        and e["input"].get("name") == "select_entities"
        and (e.get("output") or {}).get("status") == "ok"
    ]
    if not selections or not trends:
        return ["dependent_selection_missing"]
    for entry in selections:
        args, output = entry["input"]["arguments"], entry["output"]
        if expected.get("anchor_sponsor") and args["anchor_sponsor"] != expected["anchor_sponsor"]:
            continue
        if expected.get("selection_top_n") and args["top_n"] != expected["selection_top_n"]:
            continue
        selected = output.get("selected", [])
        scopes = {r["scope_id"]: r for r in selected}
        if not selected or len(scopes) != len(selected):
            continue
        if len(scopes) != min(args["top_n"], output["candidate_count"]):
            continue
        if output["root_scope_id"] != args["scope_id"]:
            continue
        network = by_handle.get(args["result_id"])
        if (
            not network
            or network["metadata"].get("analysis_parameters", {}).get("relationship") != "sponsor_shared_drug"
        ):
            continue
        if network["metadata"].get("root_scopes") != [args["scope_id"]]:
            continue
        dependency = output.get("result_id") or args["result_id"]
        if dependency not in by_handle:
            continue
        if dependency != args["result_id"] and args["result_id"] not in by_handle[dependency].get(
            "dependencies", []
        ):
            continue
        for trend in trends:
            roots = trend["metadata"].get("root_scopes", [])
            lineage_scopes = trend["metadata"].get("lineage", [])
            if (
                len(roots) != len(scopes)
                or set(roots) != set(scopes)
                or dependency not in trend.get("dependencies", [])
            ):
                continue
            if len(lineage_scopes) != len(roots):
                continue
            if all(
                any(
                    step.get("selection") == "top_shared_drug_sponsors"
                    and step.get("entity_id") == scopes[scope_id]["sponsor_id"]
                    and step.get("anchor") == args["anchor_sponsor"]
                    and step.get("parent_scope") == args["scope_id"]
                    and step.get("result_id") == dependency
                    for step in scope.get("lineage", [])
                )
                for scope_id, scope in zip(roots, lineage_scopes, strict=True)
            ):
                return []
    return ["dependent_selection_unbound_to_requested_anchor_and_n"]


def revised_failures(original, expected, artifacts, journal):
    if not expected.get("dependent_selection"):
        return list(original)
    return [f for f in original if f not in REPLACED_CODES] + dependency_failures(
        expected, artifacts, journal
    )


def markdown(result):
    lines = [
        "# Core300 数据依赖关联审计",
        "",
        "这是一项运行结束后的限定评分审计：验证用户要求的 anchor 与 top-N，是否连接到实际选择结果、"
        "趋势所用 scope、每条 sponsor lineage 及图表依赖。原始模型输出、原始成绩和费用账本均保留。"
        "这不是模型改进实验，也不是对全部任务重新进行语义评分。",
        "",
        f"原始报告内容 SHA-256：`{result['original_report_sha256']}`。",
        f"执行协议 SHA-256：`{result['study_sha256']}`。",
        "",
        "| 范围 | 原始成功 | 关联审计后成功 | 分母 |",
        "|---|---:|---:|---:|",
    ]
    for name in ("all", "development", "calibration", "heldout"):
        before = result["original"]["all"] if name == "all" else result["original"]["split"][name]
        after = (
            result["linkage_rescored"]["all"] if name == "all" else result["linkage_rescored"]["split"][name]
        )
        lines.append(f"| {name} | {before['successes']} | {after['successes']} | {before['denominator']} |")
    turns = result["audited_dependent_turns"]
    linked = sum(not row["linkage_failures"] for row in turns)
    changed = result["changed_episodes"]
    lines += [
        "",
        f"共检查 **{len(turns)} 个依赖分析轮次**；其中 **{linked} 个**具备所要求的关联链，"
        f"**{len(turns) - linked} 个**未形成通过检查的完整链。共有 **{len(changed)} 个 episode** 的正确标记发生变化。",
        "",
        "三项旧的 dependency-linkage 检查由上述更严格的关联检查替换，其他冻结失败项逐项保留。"
        "失败任务继续进入分母；没有选择性重跑，没有借助审计结果修改执行输出。"
        "未改变总分不能证明所有其他评分规则均完备。",
        "",
        "[完整逐轮审计与原始/审计后分层统计](public-evaluation/core300-selection-audit.json) · "
        "[原始正式结果](CORE300_RESULTS.md)",
        "",
        "独立审查者可下载 [原始420轮执行记录](public-evaluation/core300-execution.zip)，"
        "以 [哈希与扫描收据](public-evaluation/core300-execution-archive.json) 及 ZIP 内部manifest核对内容。"
        "完整模型工具轨迹已保留；SQLite状态、重复运行指针和重复快照未包含，原始公开人口数据另存于core300-sources.zip。",
        "",
    ]
    if changed:
        lines += ["变动 episode：", ""]
        lines += [
            f"- `{row['episode_id']}`：{row['original_correct']} → {row['audit_correct']}。"
            for row in changed
        ]
        lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, default=Path("data/core300-formal-v1"))
    parser.add_argument("--cases", type=Path, default=Path("data/core300-frozen/cases.json"))
    args = parser.parse_args()
    report_path = args.study / "report.json"
    if not report_path.exists():
        parser.exit(2, "Final Core300 report absent; no partial scores inspected.\n")
    original, corpus, protocol = read(report_path), read(args.cases), read(args.study / "study.json")
    episodes = validate_corpus(corpus, protocol)
    rows = validate_results(original, episodes)
    episodes_by_id = {e["id"]: e for e in episodes}
    audited, changed, turn_details = [], [], []
    for row in rows:
        revised = dict(row)
        failures, correct = Counter(), []
        for number, spec in enumerate(episodes_by_id[row["episode_id"]]["turns"], 1):
            path = args.study / row["episode_id"] / f"turn-{number}.json"
            if not path.exists():
                continue  # Runner failures remain failures; never promote incomplete episodes.
            turn = read(path)
            revised_codes = revised_failures(
                turn["grade"]["failures"], spec["expected"], turn["response"]["artifacts"], turn["journal"]
            )
            correct.append(not revised_codes)
            failures.update(f.split(":", 1)[0] for f in revised_codes)
            if spec["expected"].get("dependent_selection"):
                turn_details.append(
                    {
                        "episode_id": row["episode_id"],
                        "turn": number,
                        "original_correct": turn["grade"]["correct"],
                        "audit_correct": not revised_codes,
                        "linkage_failures": dependency_failures(
                            spec["expected"], turn["response"]["artifacts"], turn["journal"]
                        ),
                    }
                )
        if row["status"] == "graded":
            if len(correct) != row["planned_turns"]:
                raise ValueError("Saved turn missing from complete graded episode")
            revised["correct"], revised["failure_codes"] = all(correct), dict(failures)
        if revised["correct"] != row["correct"]:
            changed.append(
                {
                    "episode_id": row["episode_id"],
                    "original_correct": row["correct"],
                    "audit_correct": revised["correct"],
                    "failure_codes": revised["failure_codes"],
                }
            )
        audited.append(revised)
    result = {
        "version": "core300-selection-linkage-audit-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "original_report_sha256": digest(original),
        "study_sha256": protocol["sha256"],
        "original": aggregate(rows),
        "linkage_rescored": aggregate(audited),
        "changed_episodes": changed,
        "audited_dependent_turns": turn_details,
        "scope": "Only the three original dependency-linkage checks are replaced. All other frozen grades retained. "
        "No model rerun, no output editing, no claim of full independent semantic regrading.",
    }
    target = Path("docs/public-evaluation/core300-selection-audit.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    Path("docs/CORE300_SELECTION_AUDIT.md").write_text(markdown(result), encoding="utf-8")
    print(json.dumps({"changed_episodes": len(changed), "audited_dependent_turns": len(turn_details)}))


if __name__ == "__main__":
    main()
