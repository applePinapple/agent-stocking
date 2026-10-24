"""Focused fixtures for data conversion, validation, and malformed inputs."""

import struct
import shutil
import unittest
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from agent_stocking.data import load_csv, load_tdx_day, validate_bars, write_csv
from agent_stocking.models import Bar


D = Decimal
HEADER = "date,open,high,low,close,volume"
ROW = "2024-01-02,10,12,9,11,1000"


class DataTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent / ("data-test-" + uuid4().hex)
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root)

    def csv_file(self, text, encoding="utf-8-sig"):
        path = self.root / "daily.csv"
        path.write_text(text, encoding=encoding)
        return path

    def bar(self, **changes):
        base = Bar(date(2024, 1, 2), D("10"), D("12"), D("9"), D("11"), 1000)
        return replace(base, **changes)

    def binary_file(self, records):
        path = self.root / "sz000001.day"
        path.write_bytes(b"".join(struct.pack("<IIIIIfII", *record) for record in records))
        return path

    def test_english_csv_and_optional_fields(self):
        path = self.csv_file(HEADER + ",amount,buy_allowed,sell_allowed,MA5\n" + ROW + ",11000,true,0,10.5\n")
        self.assertEqual(load_csv(path), [self.bar(amount=D("11000"), buy_allowed=True, sell_allowed=False)])

    def test_gb18030_tdx_title_tabs_and_footer(self):
        path = self.csv_file(
            "000001 平安银行 日线 不复权\n\n"
            "日期\t开盘\t最高\t最低\t收盘\t成交量\t成交额\n"
            "2024/01/02\t10\t12\t9\t11\t10\t11000\n"
            "数据来源:通达信\n", encoding="gb18030",
        )
        self.assertEqual(load_csv(path, volume_unit="lots"), [self.bar(amount=D("11000"))])

    def test_whitespace_export(self):
        path = self.csv_file("日期 开盘 最高 最低 收盘 成交量\n20240102 10 12 9 11 1000\n")
        self.assertEqual(load_csv(path), [self.bar()])

    def test_tab_export_preserves_trailing_blank_fields(self):
        path = self.csv_file(HEADER.replace(",", "\t") + "\tamount\tbuy_allowed\tsell_allowed\n"
                             + ROW.replace(",", "\t") + "\t\t\t\n")
        self.assertEqual(load_csv(path), [self.bar()])

    def test_volume_unit_is_explicit(self):
        path = self.csv_file(HEADER + "\n" + ROW + "\n")
        self.assertEqual(load_csv(path)[0].volume, 1000)
        self.assertEqual(load_csv(path, volume_unit="lots")[0].volume, 100000)
        with self.assertRaisesRegex(ValueError, "volume_unit"):
            load_csv(path, volume_unit="auto")

    def test_round_trip_preserves_decimal_and_permissions(self):
        bars = [self.bar(amount=D("11000.123"), buy_allowed=False, sell_allowed=True),
                self.bar(date=date(2024, 1, 3), open=D("10.125"), volume=0)]
        path = self.root / "roundtrip.csv"
        write_csv(iter(bars), path)
        self.assertEqual(load_csv(path), bars)
        self.assertTrue(path.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_empty_and_header_only_csv_rejected(self):
        for text in ("", "\n  \n", HEADER + "\n", HEADER + "\n数据来源:通达信\n"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                load_csv(self.csv_file(text))

    def test_duplicate_mapped_columns_rejected(self):
        path = self.csv_file(HEADER + ",收盘\n" + ROW + ",11\n")
        with self.assertRaisesRegex(ValueError, "line 1.*duplicate column: close"):
            load_csv(path)

    def test_incomplete_header_rejected(self):
        path = self.csv_file("date,open,high,low,close\n2024-01-02,10,12,9,11\n")
        with self.assertRaisesRegex(ValueError, "line 1.*volume"):
            load_csv(path)

    def test_only_one_title_allowed(self):
        path = self.csv_file("first title\nsecond title\n" + HEADER + "\n" + ROW)
        with self.assertRaisesRegex(ValueError, "line 2.*missing required"):
            load_csv(path)

    def test_malformed_rows_are_not_skipped(self):
        for row in ("garbage", ROW + ",extra", "2024-01-03,10,12,9,11", '"2024-01-03,10,12,9,11,1000'):
            with self.subTest(row=row), self.assertRaisesRegex(ValueError, "line 3"):
                load_csv(self.csv_file(HEADER + "\n" + ROW + "\n" + row))

    def test_content_after_metadata_rejected(self):
        path = self.csv_file(HEADER + "\n" + ROW + "\n数据来源:通达信\n2024-01-03,10,12,9,11,1000")
        with self.assertRaisesRegex(ValueError, "line 4.*after"):
            load_csv(path)

    def test_duplicate_and_unsorted_csv_dates_rejected(self):
        for second_date, message in (("2024-01-02", "duplicate date"), ("2024-01-01", "increasing")):
            with self.subTest(date=second_date), self.assertRaisesRegex(ValueError, "line 3.*" + message):
                load_csv(self.csv_file(HEADER + "\n" + ROW + "\n" + ROW.replace("2024-01-02", second_date)))

    def test_invalid_numeric_data_rejected(self):
        for column, value in (("open", "NaN"), ("high", "Infinity"), ("low", "0"),
                              ("close", "13"), ("volume", "-1"), ("volume", "1.5"),
                              ("volume", "nan"), ("amount", "-1"), ("amount", "inf")):
            fields = dict(zip(HEADER.split(","), ROW.split(",")))
            fields["amount"] = "11000"
            fields[column] = value
            with self.subTest(column=column, value=value), self.assertRaisesRegex(ValueError, "line 2"):
                load_csv(self.csv_file(",".join(fields) + "\n" + ",".join(fields.values())))

    def test_invalid_dates_rejected(self):
        for invalid in ("2024-02-30", "2024-1-02", "2024/01-02", "2024012", "not-a-date"):
            with self.subTest(date=invalid), self.assertRaisesRegex(ValueError, "line 2.*date"):
                load_csv(self.csv_file(HEADER + "\n" + ROW.replace("2024-01-02", invalid)))

    def test_boolean_values_and_blank_optionals(self):
        for raw, expected in (("true", True), ("TRUE", True), ("1", True), ("false", False), ("0", False), ("", None)):
            with self.subTest(raw=raw):
                bar = load_csv(self.csv_file(HEADER + ",amount,buy_allowed,sell_allowed\n" + ROW + ",," + raw + ","))[0]
                self.assertIs(bar.buy_allowed, expected)
                self.assertIsNone(bar.sell_allowed)
                self.assertIsNone(bar.amount)
        with self.assertRaisesRegex(ValueError, "buy_allowed"):
            load_csv(self.csv_file(HEADER + ",buy_allowed\n" + ROW + ",yes"))

    def test_real_binary_layout_and_price_units(self):
        path = self.binary_file([(20240102, 1010, 1200, 900, 1105, 11050.5, 1000, 0),
                                 (20240103, 1105, 1300, 1050, 1250, 20000.0, 2000, 123)])
        bars = load_tdx_day(path)
        self.assertEqual(bars[0], self.bar(open=D("10.10"), close=D("11.05"), amount=D("11050.5")))
        self.assertEqual(bars[1].date, date(2024, 1, 3))
        self.assertEqual(bars[1].volume, 2000)
        self.assertIsNone(bars[1].buy_allowed)
        self.assertIsNone(bars[1].sell_allowed)

    def test_empty_and_truncated_day_file_rejected(self):
        path = self.root / "bad.day"
        for contents in (b"", b"\0" * 31, b"\0" * 33):
            with self.subTest(size=len(contents)), self.assertRaises(ValueError):
                path.write_bytes(contents)
                load_tdx_day(path)

    def test_malformed_day_records_rejected(self):
        good = (20240102, 1000, 1200, 900, 1100, 11000.0, 1000, 0)
        for index, value in ((0, 20240230), (1, 0), (4, 1300), (5, float("nan")), (5, -1.0)):
            record = list(good)
            record[index] = value
            with self.subTest(index=index, value=value), self.assertRaisesRegex(ValueError, "record 1"):
                load_tdx_day(self.binary_file([record]))

    def test_duplicate_and_unsorted_day_records_rejected(self):
        first = (20240102, 1000, 1200, 900, 1100, 11000.0, 1000, 0)
        for day in (20240102, 20240101):
            with self.subTest(date=day), self.assertRaisesRegex(ValueError, "record 2"):
                load_tdx_day(self.binary_file([first, (day, *first[1:])]))

    def test_validate_bars_rejects_invalid_model_fields(self):
        invalid_fields = ({"open": 10.0}, {"volume": True}, {"volume": -1},
                          {"amount": D("NaN")}, {"sell_allowed": 1},
                          {"date": datetime(2024, 1, 2)}, {"low": D("11")})
        for changes in invalid_fields:
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "bar 1"):
                validate_bars([self.bar(**changes)])
        with self.assertRaisesRegex(ValueError, "empty"):
            validate_bars([])

    def test_write_validates_before_overwriting(self):
        path = self.root / "existing.csv"
        path.write_text("keep me", encoding="utf-8")
        with self.assertRaises(ValueError):
            write_csv([self.bar(volume=-1)], path)
        self.assertEqual(path.read_text(encoding="utf-8"), "keep me")


if __name__ == "__main__":
    unittest.main()
