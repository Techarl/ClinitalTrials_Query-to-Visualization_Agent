"""Reconcile published, closed new-phase studies; never inspect a live study ledger."""

import argparse
import hashlib
import json
from decimal import Decimal
from pathlib import Path

FIELDS = ('attempts', 'known_cost_usd', 'unknown_usage_attempts',
          'unknown_reserved_usd', 'estimated_or_reserved_usd')
FILES = {
    'core': 'core300-accounting.json', 'nli': 'nli4ct-accounting.json',
    'trial_formal': 'trialqa-formal-v1.json', 'trial_pilot': 'trialqa-pilot-v2.json',
    'trial_zero': 'trialqa-pilot.json', 'trial_audit': 'trialqa-formal-statistics.json',
    'jev': 'jev-latency-accounting.json', 'jev_control': 'jev-latency-control.json',
    'harness': 'harness-v3-formal-results.json',
}
TOLERANCE = Decimal('0.00000001')


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError('Invalid cost scalar')
    value = Decimal(str(value))
    if not value.is_finite() or value < 0:
        raise ValueError('Invalid cost scalar')
    return value


def integer(value):
    if type(value) is not int or value < 0:
        raise ValueError('Invalid attempt count')
    return value


def close(left, right):
    if abs(number(left) - number(right)) > TOLERANCE:
        raise ValueError('Cost mismatch')


def normalize(row):
    out = {k: integer(row[k]) if k in ('attempts', 'unknown_usage_attempts')
           else number(row[k]) for k in FIELDS}
    if out['unknown_usage_attempts'] > out['attempts']:
        raise ValueError('Unknown count exceeds attempts')
    if out['unknown_usage_attempts'] == 0 and out['unknown_reserved_usd'] != 0:
        raise ValueError('Reservation without unknown attempt')
    close(out['known_cost_usd'] + out['unknown_reserved_usd'], out['estimated_or_reserved_usd'])
    return out


def total(rows):
    return {key: sum(row[key] for row in rows) for key in FIELDS}


def match(claimed, calculated):
    claimed = normalize(claimed)
    for key in FIELDS:
        if key in ('attempts', 'unknown_usage_attempts'):
            if claimed[key] != calculated[key]:
                raise ValueError('Attempt count mismatch')
        else:
            close(claimed[key], calculated[key])


def ledger_summary(ledger):
    events = ledger['events']
    if integer(ledger['attempts']) != len(events):
        raise ValueError('Ledger attempt count mismatch')
    known, reserved, unknown = Decimal(0), Decimal(0), 0
    for event in events:
        if event['status'] == 'completed':
            known += number(event['estimated_cost_usd'])
        elif event['status'] in ('attempted_usage_unknown', 'completed_usage_unknown'):
            reserved += number(event['reserved_upper_estimate_usd'])
            unknown += 1
        else:
            raise ValueError('Unrecognized ledger status')
    close(ledger['estimated_or_reserved_usd'], known + reserved)
    return normalize(dict(attempts=len(events), known_cost_usd=known,
                          unknown_usage_attempts=unknown, unknown_reserved_usd=reserved,
                          estimated_or_reserved_usd=known + reserved))


def reconcile(inputs, hashes):
    if set(inputs) != set(FILES) or set(hashes) != set(FILES):
        raise ValueError('Required study inputs omitted or unexpected inputs added')
    harness = inputs['harness']
    if harness.get('version') != 'harness-v3-formal-closed-report-v1' or 'all_v3_costs' not in harness:
        raise ValueError('Harness final closed publication is required')
    seen, leaves, groups = set(), [], {}

    def add(group, name, row, identity):
        if not isinstance(identity, str) or len(identity) != 64 or any(c not in '0123456789abcdef' for c in identity):
            raise ValueError('Invalid source identity')
        if identity in seen:
            raise ValueError('Duplicate ledger identity; possible cumulative double counting')
        seen.add(identity)
        value = normalize(row)
        leaves.append(dict(study=group, stage=name, source_sha256=identity, **value))
        groups.setdefault(group, []).append(value)

    for key, label, stages in [('core', 'Core300', {'pilot', 'formal'}),
                               ('nli', 'NLI4CT', {'pilot', 'aborted_v1', 'formal_v2'})]:
        study = inputs[key]
        if set(study['studies']) != stages or study.get('duplicate_ledger_inputs_skipped'):
            raise ValueError('Stage coverage omitted or duplicated')
        for name, row in study['studies'].items():
            add(label, name, row, row['ledger_sha256'])
        match(study['totals'], total(groups[label]))
    for key, stage in [('trial_formal', 'formal'), ('trial_pilot', 'pilot_v2'), ('trial_zero', 'pilot_before_paid_calls')]:
        row = ledger_summary(inputs[key]['budget'])
        if key == 'trial_zero' and (row['attempts'] or row['estimated_or_reserved_usd']):
            raise ValueError('Previously zero-cost pilot changed')
        if key == 'trial_formal':
            match(inputs['trial_audit']['accounting'], row)
        add('TrialQA', stage, row, hashes[key])
    jev, control = inputs['jev'], inputs['jev_control']['accounting']
    if integer(jev['unknown_attempts']) != 0 or integer(control['unknown_attempts']) != 0:
        raise ValueError('Jev aggregate lacks unknown reservation amounts; full ledger required')
    if integer(jev['attempts']) != integer(control['attempts']):
        raise ValueError('Jev attempt count mismatch')
    close(jev['estimated_or_reserved_usd'], control['estimated_or_reserved_usd'])
    add('Jev latency control', 'measured_and_warmup', dict(
        attempts=jev['attempts'], known_cost_usd=jev['estimated_or_reserved_usd'],
        unknown_usage_attempts=0, unknown_reserved_usd=0,
        estimated_or_reserved_usd=jev['estimated_or_reserved_usd']), hashes['jev'])
    prior = harness['all_v3_costs']
    receipts = prior['receipts']
    if len(receipts) != 4 or sorted(r['category'] for r in receipts) != ['pilot', 'prior_main', 'prior_main', 'smoke']:
        raise ValueError('Four nonoverlapping Harness prior ledgers required')
    for row in receipts:
        add('Harness v3', row['label'], dict(
            attempts=row['attempts'], known_cost_usd=row['known_usd'],
            unknown_usage_attempts=row['unknown_attempts'], unknown_reserved_usd=row['unknown_reserved_usd'],
            estimated_or_reserved_usd=row['estimated_or_reserved_usd']), row['sha256'])
    prior_main = sum(number(r['estimated_or_reserved_usd']) for r in receipts if r['category'] == 'prior_main')
    close(prior_main, harness['prior_main_study_usd'])
    add('Harness v3', 'formal', harness['formal_costs'], harness['ledger_sha256'])
    harness_sum = total(groups['Harness v3'])
    close(harness_sum['estimated_or_reserved_usd'], prior['all_v3_actual_or_reserved_usd'])
    if integer(prior['all_v3_attempts']) != harness_sum['attempts']:
        raise ValueError('Harness all-v3 attempt count mismatch')
    close(prior_main + number(harness['formal_costs']['estimated_or_reserved_usd']), harness['combined_main_study_usd'])
    totals = total([normalize(row) for row in leaves])
    return {
        'version': 'closed-new-phase-research-costs-v1',
        'source_files': {FILES[k]: {'sha256': hashes[k]} for k in FILES},
        'studies': {name: total(rows) for name, rows in groups.items()}, 'stages': leaves,
        'totals': totals,
        'closed_base_excluding_formal_harness_usd': totals['estimated_or_reserved_usd'] - number(harness['formal_costs']['estimated_or_reserved_usd']),
        'unused_demo_reserve_usd': Decimal('0.50'),
        'reserve_included_in_costs': False,
        'limits': [
            'Tracked new-phase studies only; historical V1/V2 experiments and untracked application usage excluded.',
            'Provider-reported amounts or versioned estimates plus unknown-usage reservations; not invoices.',
            'Snapshots, cumulative development-only receipts and per-arm shared-H0 costs are not added again.',
            'Core300/NLI validate published ledger summaries; TrialQA recomputes public event ledgers; Jev cross-checks two closed aggregates; Harness validates four prior receipts plus formal summary.',
            'Local compute and electricity are not priced. Unused demo reserve is allocation, not spending.',
        ],
    }


def markdown(result):
    lines = ['# Tracked research costs', '',
             'Closed new-phase studies only. Amounts are provider-reported or versioned estimates plus retained unknown-usage reservations, not invoices.', '',
             '| Study | Attempts | Known USD | Unknown attempts | Reserved USD | Total USD |',
             '|---|---:|---:|---:|---:|---:|']
    for name, row in [*result['studies'].items(), ('Total', result['totals'])]:
        lines.append(f"| {name} | {row['attempts']} | {row['known_cost_usd']:.6f} | {row['unknown_usage_attempts']} | {row['unknown_reserved_usd']:.6f} | {row['estimated_or_reserved_usd']:.6f} |")
    lines += ['', 'The unused **$0.50 demo reserve is not spending** and is excluded from this total. The original $100 allocation and Harness limits are not expanded by this reconciliation.', '',
              *[f'- {limitation}' for limitation in result['limits']], '',
              'Reproduce from the published closed reports:', '',
              '```sh', 'python -m scripts.report_research_costs', '```', '',
              'The command refuses publication until the final closed Harness report exists. It checks exact required study/stage coverage, source identities, attempt counts and cost arithmetic. [Machine-readable totals and source hashes](public-evaluation/research-costs.json) retain the nonoverlapping stage breakdown.', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=Path('docs/public-evaluation'))
    args = parser.parse_args()
    final = args.directory / FILES['harness']
    if not final.is_file():
        parser.exit(2, 'Final closed Harness report absent; no costs published.\n')
    inputs, hashes = {}, {}
    for key, name in FILES.items():
        data = (args.directory / name).read_bytes()
        inputs[key] = json.loads(data)
        hashes[key] = hashlib.sha256(data).hexdigest()
    result = reconcile(inputs, hashes)
    (args.directory / 'research-costs.json').write_text(
        json.dumps(result, indent=2, default=lambda value: float(value)), encoding='utf-8')
    (args.directory.parent / 'RESEARCH_COSTS.md').write_text(markdown(result), encoding='utf-8')
    print(json.dumps(result['totals'], default=float))


if __name__ == '__main__':
    main()
