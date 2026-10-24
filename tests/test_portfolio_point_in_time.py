from datetime import datetime, timezone
from decimal import Decimal as D
import unittest
from agent_stocking.point_in_time import HistoricalCoverage, HistoricalFact, PointInTimeTable, load_verified_fact_bundle
from pathlib import Path
import hashlib,json,uuid
from contextlib import contextmanager

@contextmanager
def workspace_fixture_dir():
    parent=Path(__file__).resolve().parent
    root=parent/("synthetic-pit-"+uuid.uuid4().hex)
    root.mkdir(mode=0o777)
    assert root.resolve().is_relative_to(parent.resolve())
    try:
        yield root
    finally:
        for child in root.iterdir():
            assert child.resolve().is_relative_to(root.resolve()) and child.is_file()
            child.unlink()
        root.rmdir()

def moment(y,m,d):return datetime(y,m,d,15,tzinfo=timezone.utc)
def fact(value,effective,published):return HistoricalFact("A","total_shares",D(value),effective,published,"synthetic official-like fixture","shares")

class PointInTimeTests(unittest.TestCase):
    def test_publication_gate_prevents_future_restatement_backfill(self):
        old=fact("100",moment(2020,1,1),moment(2020,1,1))
        future=fact("200",moment(2020,1,1),moment(2022,1,1))
        table=PointInTimeTable([old,future])
        self.assertEqual(table.as_of("A","total_shares",moment(2021,1,1),unit="shares").value,D(100))
        self.assertEqual(table.as_of("A","total_shares",moment(2022,1,1),unit="shares").value,D(200))

    def test_effective_date_gate_and_current_snapshot_not_historical(self):
        future=fact("200",moment(2022,1,1),moment(2020,1,1))
        self.assertIsNone(PointInTimeTable([future]).as_of("A","total_shares",moment(2021,1,1),unit="shares"))

    def test_marketcap_requires_coverage_and_uses_raw_price(self):
        item=fact("100",moment(2020,1,1),moment(2020,1,1))
        self.assertIsNone(PointInTimeTable([item]).market_cap("A",moment(2021,1,1),D(20)))
        coverage=HistoricalCoverage("A","total_shares",moment(2020,1,1),moment(2021,12,31),"synthetic complete event-history audit")
        self.assertEqual(PointInTimeTable([item],[coverage]).market_cap("A",moment(2021,1,1),D(20))[0],D(2000))

    def test_latest_public_snapshot_does_not_require_proof_of_unreported_real_changes(self):
        old=fact("100",moment(2020,12,31),moment(2021,1,5))
        change=fact("200",moment(2021,1,6),moment(2021,2,5))
        public=HistoricalCoverage("A","total_shares_publications",moment(2021,1,5),moment(2021,3,1),"synthetic complete public-notice audit")
        table=PointInTimeTable([old,change],[public])
        at=moment(2021,1,20)
        self.assertIsNone(table.market_cap("A",at,D(20)))
        value,used=table.market_cap("A",at,D(20),share_fact_semantics="latest_published_known")
        self.assertEqual((value,used.effective_at),(D(2000),old.effective_at))
        self.assertEqual(table.market_cap("A",moment(2021,2,6),D(20),share_fact_semantics="latest_published_known")[0],D(4000))

    def test_later_correction_applies_only_from_publication_and_keeps_asof_date(self):
        erroneous=fact("140",moment(2020,12,31),moment(2021,1,5))
        correction=fact("100",moment(2020,12,31),moment(2021,2,5))
        table=PointInTimeTable([erroneous,correction])
        self.assertEqual(table.latest_published("A","total_shares",moment(2021,1,20),unit="shares").value,D(140))
        self.assertEqual(table.latest_published("A","total_shares",moment(2021,2,6),unit="shares").value,D(100))
        self.assertEqual(table.at_decision(moment(2021,1,20)).latest_published("A","total_shares",moment(2021,1,20),unit="shares").value,D(140))

    def test_old_annual_published_later_does_not_roll_back_newer_known_asof(self):
        later_asof=fact("200",moment(2021,1,10),moment(2021,1,11))
        newest_snapshot=fact("100",moment(2021,1,9),moment(2021,1,12))
        table=PointInTimeTable([later_asof,newest_snapshot])
        self.assertEqual(table.latest_published("A","total_shares",moment(2021,1,20),unit="shares").value,D(200))
        future_asof=fact("300",moment(2021,2,28),moment(2021,2,26))
        feb=PointInTimeTable([later_asof,newest_snapshot,future_asof])
        self.assertEqual(feb.latest_published("A","total_shares",moment(2021,2,26),unit="shares").value,D(200))
        self.assertEqual(feb.latest_published("A","total_shares",moment(2021,3,1),unit="shares").value,D(300))

    def test_same_asof_same_publication_sources_conflict_before_selection(self):
        old=fact("100",moment(2021,1,9),moment(2021,1,12))
        conflict=HistoricalFact("A","total_shares",D(300),old.effective_at,old.published_at,"synthetic second conflicting original","shares")
        with self.assertRaises(ValueError):PointInTimeTable([old,conflict])

    def test_units_timezone_and_conflicts_fail_closed(self):
        item=fact("100",moment(2020,1,1),moment(2020,1,1))
        with self.assertRaises(ValueError):PointInTimeTable([item]).as_of("A","total_shares",moment(2021,1,1),unit="ten_thousand_shares")
        with self.assertRaises(ValueError):PointInTimeTable([item,fact("200",item.effective_at,item.published_at)])
        with self.assertRaises(ValueError):fact("100",datetime(2020,1,1),moment(2020,1,1))

    def test_hashed_source_bundle_and_conservative_date_publication(self):
        project=Path(__file__).resolve().parents[1]
        with workspace_fixture_dir() as folder:
            root=Path(folder).resolve()
            self.assertTrue(root.is_relative_to(project))
            source=root/"synthetic-notice.txt"
            source.write_text("Synthetic disclosure evidence, not a market fact.",encoding="utf-8")
            published="2020-01-02T23:59:59+00:00"
            bundle={"schema_version":1,"status":"verified_point_in_time","sources":[{"id":"test","path":source.name,"sha256":hashlib.sha256(source.read_bytes()).hexdigest(),"published_at":published,"timestamp_precision":"date"}],"facts":[{"symbol":"A","field":"total_shares","value":"100","unit":"shares","effective_at":"2020-01-01T00:00:00+00:00","published_at":published,"source_id":"test"}],"coverage":[{"symbol":"A","field":"total_shares","start_at":"2020-01-01T00:00:00+00:00","end_at":"2021-12-31T23:59:59+00:00","source_id":"test","event_history_complete":True}]}
            path=root/"bundle.json";path.write_text(json.dumps(bundle),encoding="utf-8")
            table=load_verified_fact_bundle(path)
            self.assertIsNone(table.as_of("A","total_shares",moment(2020,1,2),unit="shares"))
            self.assertEqual(table.market_cap("A",moment(2020,1,3),D(20))[0],D(2000))
            for invalid in (True, 0.1, 100):
                bundle["facts"][0]["value"]=invalid
                path.write_text(json.dumps(bundle),encoding="utf-8")
                with self.assertRaises(ValueError):load_verified_fact_bundle(path)
            bundle["facts"][0]["value"]="100"
            path.write_text(json.dumps(bundle),encoding="utf-8")
            source.write_text("Altered synthetic evidence",encoding="utf-8")
            with self.assertRaises(ValueError):load_verified_fact_bundle(path)

    def test_unverified_source_bundle_does_not_become_empty_profitable_universe(self):
        project=Path(__file__).resolve().parents[1]
        with workspace_fixture_dir() as folder:
            root=Path(folder).resolve();self.assertTrue(root.is_relative_to(project))
            path=root/"bundle.json";path.write_text('{"schema_version":1,"status":"pending"}',encoding="utf-8")
            with self.assertRaises(ValueError):load_verified_fact_bundle(path)

if __name__=="__main__":unittest.main()
