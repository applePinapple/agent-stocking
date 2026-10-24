"""Synthetic report-source regression tests, never market-return evidence."""
import importlib.util
import json
from pathlib import Path
import shutil
import unittest
import uuid

BASE=Path(__file__).resolve().parent
SPEC=importlib.util.spec_from_file_location('native_report_binding_under_test',BASE/'build_native_report.py')
REPORT=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REPORT)


class NativeReportBindingTests(unittest.TestCase):
    def setUp(self):
        fixture_parent=(BASE/'synthetic-native-report-fixtures').resolve()
        fixture_parent.mkdir(exist_ok=True)
        self.root=fixture_parent/uuid.uuid4().hex
        self.root.mkdir()
        self.assertTrue(self.root.resolve().is_relative_to(fixture_parent))
        def cleanup():
            if not self.root.resolve().is_relative_to(fixture_parent):
                raise ValueError('Synthetic cleanup target left intended fixture directory')
            shutil.rmtree(self.root)
        self.addCleanup(cleanup)
        self.source=self.root/'native-evaluation/per-strategy/S21-expert.txt'
        self.source.parent.mkdir(parents=True)
        self.source.write_text('ENTERLONG:SID=21 AND C>REF(C,1);\nEXITLONG:SID=21 AND C<REF(C,1);\n',encoding='ascii')
        self.summary=self.root/'synthetic-summary.xls';self.summary.write_bytes(b'SYNTHETIC SUMMARY NOT MARKET DATA')
        self.ledger=self.root/'synthetic-ledger.xls';self.ledger.write_bytes(b'SYNTHETIC LEDGER NOT TRADES')

    def record(self,**changes):
        row={'strategy_id':21,'runtime_sid':21,'source_path':self.source.relative_to(self.root).as_posix(),
             'source_sha256':REPORT.sha(self.source),'native_summary_sha256':REPORT.sha(self.summary),
             'native_trades_all_sha256':REPORT.sha(self.ledger)}
        row.update(changes)
        return row

    def write_log(self,*rows):
        (self.root/'native-run-source-log.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf-8')
        return REPORT.read_run_source_log(self.root)

    def test_root_new_alias_schema_binds_actual_hashes(self):
        records=self.write_log(self.record())
        selected=REPORT.select_actual_run_source(self.root,21,self.summary,self.ledger,records)
        self.assertTrue(selected['source_file_hash_verified'])
        self.assertEqual(selected['summary_sha256'],REPORT.sha(self.summary))
        self.assertEqual(selected['trades_sha256'],REPORT.sha(self.ledger))

    def test_conflicting_aliases_are_errors_not_silent_preference(self):
        with self.assertRaisesRegex(ValueError,'冲突'):
            self.write_log(self.record(summary_sha256='0'*64))

    def test_current_source_or_export_tamper_cannot_rebind(self):
        records=self.write_log(self.record())
        self.source.write_text('ENTERLONG:0;EXITLONG:0;',encoding='ascii')
        with self.assertRaisesRegex(ValueError,'哈希'):
            REPORT.select_actual_run_source(self.root,21,self.summary,self.ledger,records)
        self.ledger.write_bytes(b'DIFFERENT SYNTHETIC LEDGER')
        with self.assertRaisesRegex(ValueError,'匹配'):
            REPORT.select_actual_run_source(self.root,21,self.summary,self.ledger,records)

    def test_new_adapter_admitted_but_disabled_universal_not_promoted(self):
        records=self.write_log(self.record())
        plan={'supported_sid':[3]}
        self.assertEqual(REPORT.current_native_candidates(self.root,plan,records),{3,21})
        self.source.write_text('ENTERLONG:SID=3 AND C>0;EXITLONG:SID=3 AND C<0;',encoding='ascii')
        records=self.write_log(self.record())
        self.assertEqual(REPORT.current_native_candidates(self.root,plan,records),{3})

    def test_formula_file_alone_does_not_complete_six_or_seven(self):
        plan={'supported_sid':[3],'strategies':[]}
        file=self.source.with_name('S06-expert.txt')
        file.write_text('ENTERLONG:SID=6 AND C>0;EXITLONG:SID=6 AND C<0;',encoding='ascii')
        self.assertEqual(REPORT.current_native_candidates(self.root,plan,[]),{3})
        entry=REPORT.read_strategy(self.root,6,set(),{3},plan,None,[])
        self.assertEqual(entry['status'],'in_progress')
        self.assertIsNone(entry['returns'])
        self.assertFalse(entry['permanent_platform_incapability_claim'])

    def test_compile_log_without_exports_stays_in_progress(self):
        (self.root/'authorization.json').write_text('{}',encoding='utf-8')
        records=self.write_log(self.record(native_summary_sha256=None,native_trades_all_sha256=None))
        plan={'supported_sid':[3],'strategies':[]}
        candidate=REPORT.current_native_candidates(self.root,plan,records)
        entry=REPORT.read_strategy(self.root,21,set(),candidate,plan,None,records)
        self.assertEqual(entry['status'],'in_progress')
        self.assertIsNone(entry['native_summary'])
        self.assertFalse(entry['native_export_presence']['summary_file_saved'])

    def test_runtime_sid_conflict_and_source_path_escape_rejected(self):
        with self.assertRaisesRegex(ValueError,'runtime_sid'):
            self.write_log(self.record(runtime_sid=8))
        records=self.write_log(self.record(source_path='../outside-synthetic.txt'))
        with self.assertRaisesRegex(ValueError,'越界'):
            REPORT.select_actual_run_source(self.root,21,self.summary,self.ledger,records)


if __name__=='__main__':
    unittest.main(verbosity=2)
