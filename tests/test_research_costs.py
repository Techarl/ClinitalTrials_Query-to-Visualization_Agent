import copy
import hashlib
import sys
from decimal import Decimal

import pytest

from scripts import report_research_costs as report


def row():
    return dict(attempts=1, known_cost_usd=0.1, unknown_usage_attempts=0,
                unknown_reserved_usd=0, estimated_or_reserved_usd=0.1)


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def fixture():
    inputs = {}
    for key, stages in [('core', ['pilot', 'formal']), ('nli', ['pilot', 'aborted_v1', 'formal_v2'])]:
        studies = {name: {**row(), 'ledger_sha256': sha(key + name)} for name in stages}
        inputs[key] = {'studies': studies, 'totals': report.total([report.normalize(r) for r in studies.values()])}
    ledger = {'attempts': 1, 'estimated_or_reserved_usd': 0.1,
              'events': [{'status': 'completed', 'estimated_cost_usd': 0.1}]}
    inputs['trial_formal'] = {'budget': copy.deepcopy(ledger)}
    inputs['trial_pilot'] = {'budget': copy.deepcopy(ledger)}
    inputs['trial_zero'] = {'budget': {'attempts': 0, 'estimated_or_reserved_usd': 0, 'events': []}}
    inputs['trial_audit'] = {'accounting': row()}
    inputs['jev'] = {'attempts': 1, 'estimated_or_reserved_usd': 0.1, 'unknown_attempts': 0}
    inputs['jev_control'] = {'accounting': dict(inputs['jev'])}
    receipts = [dict(label=f'prior{i}', category=category, sha256=sha(str(i)), attempts=1,
                     known_usd=0.1, unknown_reserved_usd=0, unknown_attempts=0, estimated_or_reserved_usd=0.1)
                for i, category in enumerate(['prior_main', 'prior_main', 'pilot', 'smoke'])]
    inputs['harness'] = dict(version='harness-v3-formal-closed-report-v1',
                            all_v3_costs=dict(receipts=receipts, all_v3_actual_or_reserved_usd=0.5, all_v3_attempts=5),
                            formal_costs=row(), ledger_sha256=sha('formalharness'),
                            prior_main_study_usd=0.2, combined_main_study_usd=0.3)
    return inputs, {key: sha(key) for key in report.FILES}


def test_totals_count_nonoverlapping_stages_and_exclude_unused_reserve():
    inputs, hashes = fixture()
    result = report.reconcile(inputs, hashes)
    assert result['totals']['attempts'] == 13
    assert result['totals']['estimated_or_reserved_usd'] == Decimal('1.3')
    assert result['closed_base_excluding_formal_harness_usd'] == Decimal('1.2')
    assert result['unused_demo_reserve_usd'] == Decimal('0.50')
    assert result['reserve_included_in_costs'] is False
    assert len(result['source_files']) == len(report.FILES)
    assert 'historical V1/V2' in report.markdown(result)


@pytest.mark.parametrize('key', list(report.FILES))
def test_missing_study_blocks_publication(key):
    inputs, hashes = fixture()
    del inputs[key]
    with pytest.raises(ValueError, match='omitted'):
        report.reconcile(inputs, hashes)


@pytest.mark.parametrize('mutate', [
    lambda x: x['core']['studies'].pop('pilot'),
    lambda x: x['nli']['totals'].update(estimated_or_reserved_usd=0.8),
    lambda x: x['trial_formal']['budget'].update(attempts=2),
    lambda x: x['trial_audit']['accounting'].update(attempts=2),
    lambda x: x['trial_zero']['budget'].update(estimated_or_reserved_usd=0.1),
    lambda x: x['jev'].update(unknown_attempts=1),
    lambda x: x['jev_control']['accounting'].update(estimated_or_reserved_usd=0.2),
    lambda x: x['harness']['all_v3_costs']['receipts'].pop(),
    lambda x: x['harness']['all_v3_costs'].update(all_v3_attempts=6),
    lambda x: x['harness'].update(prior_main_study_usd=0.3),
    lambda x: x['harness'].update(version='running'),
])
def test_cost_attempt_stage_and_closure_mutations_rejected(mutate):
    inputs, hashes = fixture()
    mutate(inputs)
    with pytest.raises(ValueError):
        report.reconcile(inputs, hashes)


def test_duplicate_and_cumulative_receipts_rejected():
    inputs, hashes = fixture()
    inputs['nli']['studies']['pilot']['ledger_sha256'] = inputs['core']['studies']['pilot']['ledger_sha256']
    with pytest.raises(ValueError, match='Duplicate ledger'):
        report.reconcile(inputs, hashes)
    inputs, hashes = fixture()
    priors = inputs['harness']['all_v3_costs']['receipts']
    priors[1]['sha256'] = priors[0]['sha256']
    with pytest.raises(ValueError, match='Duplicate ledger'):
        report.reconcile(inputs, hashes)


@pytest.mark.parametrize('value', [True, -1, float('nan'), float('inf'), '0.1', None])
def test_invalid_cost_scalar_rejected(value):
    inputs, hashes = fixture()
    inputs['trial_formal']['budget']['events'][0]['estimated_cost_usd'] = value
    with pytest.raises(ValueError, match='scalar'):
        report.reconcile(inputs, hashes)


@pytest.mark.parametrize('status', ['attempted_usage_unknown', 'completed_usage_unknown'])
def test_unknown_attempt_retains_reservation_without_becoming_known_spend(status):
    value = {'attempts': 2, 'estimated_or_reserved_usd': 0.3,
             'events': [{'status': 'completed', 'estimated_cost_usd': 0.1},
                        {'status': status, 'reserved_upper_estimate_usd': 0.2}]}
    result = report.ledger_summary(value)
    assert result['unknown_usage_attempts'] == 1
    assert result['known_cost_usd'] == Decimal('0.1')
    assert result['unknown_reserved_usd'] == Decimal('0.2')


def test_absent_closed_harness_reads_no_partial_data_and_writes_nothing(tmp_path, monkeypatch):
    (tmp_path / 'ledger.json').write_text('LIVE AND UNREADABLE')
    monkeypatch.setattr(sys, 'argv', ['report_research_costs', '--directory', str(tmp_path)])
    with pytest.raises(SystemExit) as error:
        report.main()
    assert error.value.code == 2
    assert not (tmp_path / 'research-costs.json').exists()
    assert not (tmp_path.parent / 'RESEARCH_COSTS.md').exists()
