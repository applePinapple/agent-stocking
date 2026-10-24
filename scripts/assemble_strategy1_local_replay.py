"""Guarded local screenshot assembly: draft collection or explicit finalization; no ROI."""
from __future__ import annotations
import argparse
import csv
from contextlib import contextmanager
from dataclasses import asdict
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'src'))
from agent_stocking.strategy1 import DailyObservation, MissingProfitObservationError, Strategy1RuleError, replay_strategy1
from scripts.run_strategy1_replay import load_cash_dividends


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def serial(value):
    if isinstance(value, (Decimal, date)):
        return str(value)
    raise TypeError(type(value).__name__)


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=serial) + '\n', encoding='utf-8')


@contextmanager
def staging_directory(base):
    stage = (base / ('.strategy1-assembly-' + uuid4().hex)).resolve()
    if not stage.is_relative_to(base):
        raise ValueError('Staging directory escaped local-run')
    stage.mkdir()
    try:
        yield stage
    finally:
        if not stage.resolve().is_relative_to(base):
            raise ValueError('Refusing cleanup outside local-run')
        shutil.rmtree(stage)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'Duplicate JSON key: {key}')
        result[key] = value
    return result


def iso(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError(f'Invalid ISO date: {value!r}')
    return value


def number(value):
    if isinstance(value, bool):
        raise ValueError('Boolean is not a financial number')
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError('Invalid financial number') from exc
    if not result.is_finite():
        raise ValueError('Finite financial number required')
    return result


def csv_rows(text):
    reader = csv.DictReader(io.StringIO(text))
    header = reader.fieldnames
    if not header or any(not key for key in header) or len(header) != len(set(header)):
        raise ValueError('CSV columns must be nonempty and unique')
    rows = list(reader)
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ValueError('CSV row has a missing or extra value')
    return rows


def keyed(rows, label, calendar):
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(f'Invalid {label} row')
        selected = iso(row.get('date'))
        if selected not in calendar or selected in result:
            raise ValueError(f'Duplicate or outside-calendar {label} date: {selected}')
        result[selected] = row
    return result


class MissingExecutionObservationError(Strategy1RuleError):
    def __init__(self, selected_date, field):
        self.date, self.field = selected_date, field
        super().__init__(f'{selected_date}: missing native execution evidence for {field}')


class GuardedObservation(DailyObservation):
    """Missing execution fields never return their sentinel to decision logic."""
    __slots__ = ('_guard',)

    def __getattribute__(self, name):
        if name in ('close_limit_up', 'open_buy_allowed'):
            caller = sys._getframe(1)
            if (caller.f_globals.get('__name__') == 'agent_stocking.strategy1'
                    and caller.f_code.co_name != '_validate_observations'):
                confirmed, reads = object.__getattribute__(self, '_guard')
                selected = object.__getattribute__(self, 'date')
                reads.add((str(selected), name))
                if (str(selected), name) not in confirmed:
                    raise MissingExecutionObservationError(selected, name)
        return object.__getattribute__(self, name)


def safe_trace(observations, confirmed, cash_dividends=()):
    reads, guarded = set(), []
    for bar in observations:
        item = GuardedObservation(**asdict(bar))
        object.__setattr__(item, '_guard', (confirmed, reads))
        guarded.append(item)
    missing_profit = execution_gap = rule_gap = result = None
    try:
        result = replay_strategy1(guarded, allow_sparse_profit=True, cash_dividends=cash_dividends)
    except (MissingProfitObservationError, MissingExecutionObservationError) as exc:
        if isinstance(exc, MissingProfitObservationError):
            missing_profit = {'date': str(exc.date), 'message': str(exc)}
        else:
            execution_gap = {'date': str(exc.date), 'field': exc.field, 'message': str(exc)}
        prefix = [bar for bar in guarded if bar.date < exc.date]
        if prefix:
            result = replay_strategy1(prefix, allow_sparse_profit=True, cash_dividends=cash_dividends)
    except Strategy1RuleError as exc:
        match = re.match(r'(\d{4}-\d{2}-\d{2}):', str(exc))
        rule_gap = {'message': str(exc), 'date': match.group(1) if match else None}
        if match:
            prefix = [bar for bar in guarded if str(bar.date) < match.group(1)]
            if prefix:
                result = replay_strategy1(prefix, allow_sparse_profit=True, cash_dividends=cash_dividends)
    return result, reads, missing_profit, execution_gap, rule_gap


def assemble(base: Path, *, finalize=False, cash_dividends_path=None,
             corporate_actions_coverage_path=None, write=True):
    base = base.resolve()
    files, text_cache = {}, {}

    def verify(source, expected=None, role='evidence'):
        if not isinstance(source, str) or not source.strip() or Path(source).anchor:
            raise ValueError(f'Evidence must use a relative path: {source!r}')
        path = (base / source).resolve()
        if not path.is_relative_to(base) or not path.is_file():
            raise ValueError(f'Invalid evidence source: {source}')
        if expected is not None and (not isinstance(expected, str) or not re.fullmatch(r'[0-9a-fA-F]{64}', expected)):
            raise ValueError(f'Invalid evidence hash: {source}')
        if source not in files:
            files[source] = {'source': source, 'path': str(path), 'sha256': sha(path), 'roles': []}
        record = files[source]
        if expected is not None and record['sha256'] != expected.lower():
            raise ValueError(f'Evidence hash mismatch: {source}')
        if role not in record['roles']:
            record['roles'].append(role)
        return path

    def read(source):
        path = verify(source, role='input_snapshot')
        if source not in text_cache:
            content = path.read_bytes()
            if hashlib.sha256(content).hexdigest() != files[source]['sha256']:
                raise ValueError(f'Input changed while reading: {source}')
            text_cache[source] = content.decode('utf-8-sig')
        return text_cache[source]

    def jread(source):
        return json.loads(read(source), object_pairs_hook=unique_object)

    def jlread(source):
        return [json.loads(line, object_pairs_hook=unique_object) for line in read(source).splitlines() if line.strip()]

    def metadata(row, selected, image_role):
        verify(row['source'], row['sha256'], image_role)
        verify(row['metadata_source'], row['metadata_sha256'], image_role + '_metadata')
        raw = jread(row['metadata_source'])
        details = raw.get('details', {})
        value = dict(raw, **details) if isinstance(details, dict) else raw
        if value.get('date', value.get('selected_date')) != selected or value.get('source') != row['source']:
            raise ValueError(f'Native source date/image mismatch: {selected}')
        if any(key in value and value[key] != selected for key in ('date', 'selected_date', 'chip_cost_date')):
            raise ValueError(f'Native chip date mismatch: {selected}')
        return value

    price_rel = 'derived/price-union-20261024/prices.csv'
    prices = csv_rows(read(price_rel))
    if not prices:
        raise ValueError('Empty price calendar')
    calendar = [iso(row['date']) for row in prices]
    if calendar != sorted(set(calendar)):
        raise ValueError('Price calendar must be increasing and unique')
    rocket_payload = jread('derived/rocket-daily-confirmed.json')
    if (rocket_payload.get('status') != 'confirmed' or rocket_payload.get('symbol') != '688017'
            or rocket_payload.get('row_count') != len(prices)
            or rocket_payload.get('price_calendar', {}).get('sha256') != files[price_rel]['sha256']):
        raise ValueError('Confirmed rocket series is not bound to this price calendar')
    approval_ref = rocket_payload['root_approval']
    verify(approval_ref['path'], approval_ref['sha256'], 'root_rocket_approval')
    rockets = keyed(rocket_payload['daily'], 'rocket', calendar)
    if sorted(rockets) != calendar:
        raise ValueError('Price and confirmed rocket calendars differ')
    scope = jread(approval_ref['path']).get('approved_scope', {})
    if (scope.get('symbol') != '688017' or scope.get('observed_dates') != len(calendar)
            or scope.get('first_observed_date') != calendar[0] or scope.get('last_observed_date') != calendar[-1]
            or rocket_payload.get('true_count') != sum(r.get('rocket') is True for r in rockets.values())
            or rocket_payload.get('false_count') != sum(r.get('rocket') is False for r in rockets.values())):
        raise ValueError('Rocket approval scope/counts do not match the observed calendar')
    epoch = rocket_payload.get('source_epoch', {})
    if epoch.get('local_day_num') != 1489 or epoch.get('used_data_num') != 1489 or epoch.get('price_adjustment_flag') != 0:
        raise ValueError('Expected explicitly confirmed full local-history unadjusted signal epoch')
    profits = keyed(jlread('derived/native-daily-confirmed.jsonl'), 'profit', calendar)
    limits = keyed(jlread('derived/native-limit-confirmed.jsonl'), 'limit', calendar)
    open_rel = 'derived/native-open-confirmed.jsonl'
    opening = keyed(jlread(open_rel), 'open', calendar) if (base / open_rel).exists() else {}
    accepted_profit, quarantined_profit, confirmed_execution = {}, [], set()
    observations, role_payloads = [], []
    for price in prices:
        selected, rocket = price['date'], rockets[price['date']]
        values = [number(price[k]) for k in ('open', 'high', 'low', 'close')]
        opn, high, low, close = values
        if not 0 < low <= min(opn, close) <= max(opn, close) <= high:
            raise ValueError(f'Invalid price OHLC: {selected}')
        source_rows = json.loads(price['sources_json'], object_pairs_hook=unique_object)
        if not source_rows:
            raise ValueError(f'Missing price evidence: {selected}')
        for source in source_rows:
            verify(source['source'], source['source_sha256'], 'price_original')
            verify(source['input_csv'], source['input_csv_sha256'], 'price_transcription')
            transcription = csv_rows(read(source['input_csv']))
            line = source['input_csv_line']
            if type(line) is not int or line < 2 or line - 2 >= len(transcription):
                raise ValueError(f'Invalid price row locator: {selected}')
            original = transcription[line - 2]
            if (original['date'] != selected or original['source'] != source['source']
                    or original['row_locator'] != source['row_locator']
                    or any(number(original[k]) != number(price[k]) for k in ('open', 'high', 'low', 'close'))):
                raise ValueError(f'Price transcription/date mismatch: {selected}')
        if rocket.get('status') != 'confirmed' or type(rocket.get('rocket')) is not bool:
            raise ValueError(f'Unconfirmed rocket: {selected}')
        catalog = rocket_payload['source_catalog'][rocket['primary_source_id']]
        if catalog['image'] != rocket['primary_source'] or catalog['image_sha256'] != rocket['primary_source_sha256']:
            raise ValueError(f'Rocket primary source/catalog mismatch: {selected}')
        verify(rocket['primary_source'], rocket['primary_source_sha256'], 'rocket_original')
        for source in rocket['sources']:
            verify(source['source'], source['sha256'], 'rocket_support')
        mappings = rocket['overview_mapping']
        if not rocket['rocket'] and rocket['verification_method'] == 'root_reviewed_overview_absence':
            if not any(m.get('overview_group') in ('C', 'A', 'D')
                       and m.get('eligible_for_final_absence_basis') is True and m.get('edge_or_partial') is False
                       and 1 < number(m['predicted_bar_center_x']) < 1416
                       and rocket['primary_source_id'] in m['source_ids'] for m in mappings):
                raise ValueError(f'Rocket absence lacks an interior source: {selected}')
        else:
            primary = next(s for s in rocket['sources'] if s['source'] == rocket['primary_source'])
            raw = metadata({'source': primary['source'], 'sha256': primary['sha256'],
                            'metadata_source': primary['metadata'], 'metadata_sha256': primary['metadata_sha256']}, selected, 'rocket_native')
            if raw.get('rocket') is not rocket['rocket']:
                raise ValueError(f'Native rocket value mismatch: {selected}')
        for mapping in mappings:
            verify(mapping['overview_review'], mapping['overview_review_sha256'], 'rocket_overview_review')
        profit, profit_ref = profits.get(selected), None
        if profit:
            raw = metadata(profit, selected, 'profit_original')
            if (profit.get('native_confirmation') is not True or raw.get('native_confirmation') is not True
                    or type(profit.get('rocket')) is not bool or profit['rocket'] != rocket['rocket']
                    or raw.get('rocket') is not rocket['rocket']
                    or number(profit['close']) != close or number(raw['close']) != close
                    or number(profit['profit_pct']) != number(raw['profit_pct'])
                    or not 0 <= number(profit['profit_pct']) <= 100):
                raise ValueError(f'Native P/date/close confirmation mismatch: {selected}')
            if (profit.get('independent_visual_sample_verified') is not True
                    or profit.get('independent_visual_review_status') not in ('confirmed', 'accepted_with_stale_bottom_date')
                    or not {'date', 'profit_pct', 'close'}.issubset(profit.get('independently_verified_fields', []))):
                quarantined_profit.append({'date': selected, 'source': profit['source'], 'reason': 'individual_visual_review_not_confirmed'})
                profit = None
            else:
                matched = [r for r in jlread(profit['visual_review_source'])
                           if r.get('date') == selected and r.get('source') == profit['source']]
                if not matched or any(r.get('sha256') != profit['sha256'] or r.get('popup_and_chip_cost_dates_match') is not True
                                      or r.get('review_status', 'confirmed') not in ('confirmed', 'accepted_with_stale_bottom_date')
                                      or (r.get('review_status') == 'accepted_with_stale_bottom_date' and r.get('accepted_by') != 'root')
                                      or number(r.get('profit_pct')) != number(profit['profit_pct'])
                                      or number(r.get('close')) != close
                                      or ('rocket' in r and r['rocket'] is not rocket['rocket']) for r in matched):
                    raise ValueError(f'Individual P review mismatch: {selected}')
                accepted_profit[selected] = profit
                profit_ref = {'source': profit['source'], 'sha256': profit['sha256'], 'date': selected,
                              'locator': 'individually reviewed native chip panel; synchronized popup/cost date'}
        for records, field in ((limits, 'close_limit_up'), (opening, 'open_buy_allowed')):
            record = records.get(selected)
            if not record:
                continue
            if record.get('status') != 'confirmed' or type(record.get(field)) is not bool:
                raise ValueError(f'Unconfirmed execution field {field}: {selected}')
            raw = metadata(record, selected, field)
            if 'close' in raw and number(raw['close']) != close:
                raise ValueError(f'Execution native close mismatch: {selected}')
            for key, expected in (('native_popup_close', close), ('native_popup_high', high), ('native_popup_open', opn)):
                if key in record and number(record[key]) != expected:
                    raise ValueError(f'Execution quoted price mismatch: {selected}/{key}')
            confirmed_execution.add((selected, field))
        first = source_rows[0]
        role_payloads.append({'schema_version': 1, 'kind': 'tdx_daily_evidence_draft', 'date': selected,
                              'price': {'source': first['source'], 'sha256': first['source_sha256'], 'date': selected, 'locator': first['row_locator']},
                              'rocket': {'source': rocket['primary_source'], 'sha256': rocket['primary_source_sha256'], 'date': selected,
                                         'locator': rocket['verification_method'] + '; ' + ', '.join(f"{m['overview_group']} x={m['predicted_bar_center_x']}" for m in mappings)},
                              'profit': profit_ref})
        observations.append(DailyObservation(date.fromisoformat(selected), *values,
                            number(profit['profit_pct']) if profit else None, rocket['rocket'],
                            limits[selected]['close_limit_up'] if selected in limits else False,
                            opening[selected]['open_buy_allowed'] if selected in opening else False, True, ''))
    coverage_blockers, dividends, corporate_binding = [], [], None
    if finalize:
        if cash_dividends_path is None or corporate_actions_coverage_path is None:
            coverage_blockers.append('finalization_requires_cash_dividends_and_confirmed_corporate_action_coverage')
        else:
            cash_path, coverage_path = Path(cash_dividends_path).resolve(), Path(corporate_actions_coverage_path).resolve()
            if not cash_path.is_relative_to(base) or not coverage_path.is_relative_to(base):
                raise ValueError('Finalization configuration files must be inside local-run')
            cash_rel, coverage_rel = cash_path.relative_to(base).as_posix(), coverage_path.relative_to(base).as_posix()
            verify(cash_rel, role='cash_dividend_events')
            coverage = jread(coverage_rel)
            required = {'schema_version', 'kind', 'status', 'symbol', 'start_date', 'end_date', 'cash_dividends_sha256',
                        'cash_dividend_events_complete', 'cash_per_share_and_event_dates_verified', 'other_actions_status', 'sources', 'review_note'}
            if (not isinstance(coverage, dict) or set(coverage) != required or type(coverage['schema_version']) is not int or coverage['schema_version'] != 1
                    or coverage['kind'] != 'corporate_action_coverage' or coverage['status'] != 'confirmed'
                    or coverage['symbol'] != '688017' or iso(coverage['start_date']) > calendar[0] or iso(coverage['end_date']) < calendar[-1]
                    or coverage['cash_dividend_events_complete'] is not True or coverage['cash_per_share_and_event_dates_verified'] is not True
                    or coverage['other_actions_status'] != 'none_confirmed' or not isinstance(coverage['review_note'], str) or not coverage['review_note'].strip()
                    or not isinstance(coverage['sources'], list) or not coverage['sources']):
                raise ValueError('Explicit confirmed full corporate-action coverage declaration required')
            if coverage['cash_dividends_sha256'] != files[cash_rel]['sha256']:
                raise ValueError('Corporate-action coverage does not bind this cash dividend file')
            for source in coverage['sources']:
                if not isinstance(source, dict) or set(source) != {'source', 'sha256'}:
                    raise ValueError('Coverage sources require source/sha256 original images')
                path = verify(source['source'], source['sha256'], 'corporate_action_coverage_original')
                if path.suffix.lower() not in ('.jpg', '.jpeg', '.png', '.bmp', '.tif', '.tiff'):
                    raise ValueError('Corporate-action coverage must reference original images')
            dividends, dividend_sources = load_cash_dividends(cash_path, base / 'assembled-evidence-manifest.json')
            for source in dividend_sources:
                verify(source['source'], source['sha256'], 'cash_dividend_original')
            corporate_binding = {'cash_dividends': {'source': cash_rel, 'sha256': files[cash_rel]['sha256']},
                                 'coverage': {'source': coverage_rel, 'sha256': files[coverage_rel]['sha256']}}
    else:
        coverage_blockers.append('draft_mode_has_not_verified_corporate_action_coverage')
    result, reads, missing_profit, execution_gap, rule_gap = safe_trace(observations, confirmed_execution, dividends)
    market_complete = (result is not None and len(result.equity) == len(observations)
                       and missing_profit is None and execution_gap is None and rule_gap is None)
    ready = bool(finalize and market_complete and not coverage_blockers)
    field_proofs = []
    for bar in observations:
        fields = {}
        for field, records in (('close_limit_up', limits), ('open_buy_allowed', opening)):
            queried, record = (str(bar.date), field) in reads, records.get(str(bar.date))
            fields[field] = {'decision_read': queried, 'factual_native_confirmation': record is not None,
                             'source': record['source'] if record else None, 'sha256': record['sha256'] if record else None,
                             'status': 'confirmed' if record else ('inactive_sentinel_proven_not_read' if market_complete and not queried else 'unverified_sentinel'),
                             'sentinel_is_market_fact': False if record is None else None}
        field_proofs.append({'date': str(bar.date), 'fields': fields})
    status = {'artifact_type': 'collection_status_not_a_return_report', 'symbol': '688017', 'name': '\u7eff\u7684\u8c10\u6ce2',
              'requested_start': '2023-01-01', 'actual_first_bar': calendar[0], 'actual_last_bar': calendar[-1],
              'price_rows': len(prices), 'confirmed_rocket_rows': len(rockets), 'confirmed_rocket_count': sum(r['rocket'] for r in rockets.values()),
              'confirmed_native_profit_rows': len(accepted_profit), 'quarantined_native_profit_rows': quarantined_profit,
              'next_required_profit': missing_profit, 'execution_evidence_gaps': [execution_gap] if execution_gap else [], 'rule_gap': rule_gap,
              'queried_limit_dates': sorted(d for d, f in reads if f == 'close_limit_up'), 'queried_open_dates': sorted(d for d, f in reads if f == 'open_buy_allowed'),
              'execution_field_proofs': field_proofs, 'safe_trace_completed_all_bars': market_complete,
              'safe_trace_method': 'actual_engine_decision_attribute_reads_guarded_before_value_return; type_validation_reads_excluded',
              'completed_close_only_prefix_through': str(result.equity[-1].date) if result else None,
              'draft_fills_so_far': [{k: v for k, v in asdict(fill).items() if k != 'cash_after'} for fill in result.fills] if result else [],
              'draft_closed_batches_so_far': [{k: v for k, v in asdict(batch).items() if k in ('buy_sequence', 'sell_sequence', 'entry_date', 'exit_date', 'quantity')} for batch in result.closed_batches] if result else [],
              'formal_returns_produced': False, 'ready_for_formal_replay': ready, 'finalization_requested': finalize,
              'corporate_action_blockers': coverage_blockers,
              'cash_dividend_events_applied_to_trace': len(dividends),
              'note': 'Collection aid, not cash or return reporting. Draft trace omits dividends; finalization uses verified cash events. Inactive execution sentinels are not market facts.'}
    suffix = '' if ready else '-draft'
    folder = 'derived/daily-evidence' + suffix
    csv_name, manifest_name = 'strategy1-observations' + suffix + '.csv', 'assembled-evidence-manifest' + suffix + '.json'
    for payload in role_payloads:
        payload['kind'] = 'tdx_daily_evidence' if ready else 'tdx_daily_evidence_draft'
        payload['note'] = ('Confirmed input after complete safe trace; execution proof in assembly manifest. ' if ready else 'DRAFT_NOT_FOR_FORMAL_RUNNER. ') + 'Unqueried execution sentinels are not market facts; sell permission follows user ideal-close model.'
    if write:
        with staging_directory(base) as stage:
            daily_files = []
            for bar, payload in zip(observations, role_payloads):
                relative = f'{folder}/{bar.date}.json'
                dump(stage / relative, payload)
                daily_files.append({'source': relative, 'sha256': sha(stage / relative)})
            with (stage / csv_name).open('w', encoding='utf-8-sig', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=list(asdict(observations[0])))
                writer.writeheader()
                for bar in observations:
                    row = asdict(bar)
                    row['source'] = f'{folder}/{bar.date}.json'
                    writer.writerow({key: '' if value is None else str(value).lower() if type(value) is bool else value for key, value in row.items()})
            dump(stage / manifest_name, {'kind': 'local_tdx_screenshot_input_assembly', 'status': 'confirmed' if ready else 'collection_incomplete',
                 'ready_for_formal_replay': ready, 'input_csv': csv_name, 'input_csv_sha256': sha(stage / csv_name),
                 'safe_trace_completed_all_bars': market_complete,
                 'trace_calendar': {'first_date': calendar[0], 'last_date': calendar[-1], 'rows': len(calendar)},
                 'corporate_action_binding': corporate_binding,
                 'verified_inputs_and_sources': [dict(item, status='confirmed', confirmation_kind='sha256_integrity') for item in files.values()], 'execution_field_proofs': field_proofs,
                 'corporate_action_coverage_verified': bool(finalize and not coverage_blockers), 'cash_dividends_required_by_formal_runner': True,
                 'safe_trace_engine_sha256': sha(ROOT / 'src/agent_stocking/strategy1.py'), 'assembler_sha256': sha(Path(__file__)),
                 'daily_mappings': daily_files, 'collection_status': 'derived/collection-status.json'})
            dump(stage / 'derived/collection-status.json', status)
            for record in files.values():
                if sha(Path(record['path'])) != record['sha256']:
                    raise ValueError(f"Input/source changed before assembly output: {record['source']}")
            for path in stage.rglob('*'):
                if path.is_file():
                    target = base / path.relative_to(stage)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    path.replace(target)
    else:
        for record in files.values():
            if sha(Path(record['path'])) != record['sha256']:
                raise ValueError(f"Input/source changed during validation: {record['source']}")
    return status


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--local-run', type=Path, required=True)
    parser.add_argument('--finalize', action='store_true')
    parser.add_argument('--cash-dividends', type=Path)
    parser.add_argument('--corporate-actions-coverage', type=Path)
    args = parser.parse_args(argv)
    status = assemble(args.local_run, finalize=args.finalize, cash_dividends_path=args.cash_dividends,
                      corporate_actions_coverage_path=args.corporate_actions_coverage)
    print(json.dumps({key: status[key] for key in ('confirmed_native_profit_rows', 'next_required_profit', 'execution_evidence_gaps', 'completed_close_only_prefix_through', 'ready_for_formal_replay')}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    main()
