"""Build Chinese native-export reports. Does not backtest or touch native/quote files.

python -X utf8 build_native_report.py --refresh-audit
python -X utf8 build_native_report.py --refresh-audit --final
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import importlib.util
import json
from pathlib import Path
import platform
import re
import subprocess
import sys

BASE = Path(__file__).resolve().parent
ZERO = Decimal("0")
UNSUPPORTED_REASONS = {
    6: "Supertrend完整递推与128层包络已在七股真实本机输入完成独立数学对照；split-v2辅助/诊断分别编译通过，但应用诊断后客户端退出。等价独立128层v3压缩源已通过七股全状态对照，待用户手动登录后本机编译、诊断与正式评测，不是平台不支持或策略N/A。",
    7: "七股本机SARTURN/SAR序列及预测交易核验已保存；正式原生评测汇总和流水待手动登录后运行，不能把预测信号当作已保存原生成交。",
    8: "收盘确认后次开本机版已获准；本批通道边界、持仓与重复信号及原生运行核验未完成，不能再仅以原版STOP缺口否决。",
    11: "两档冻结预算状态机、真实七股native-v2预检和显式许可补充回放执行器已实现；分批/本地补充问题待已有答复，许可数据未提供，本机仓位及数量行为探针待手动登录后核验。当前0委托/0fill，收益未生成，不是平台不支持。",
    13: "10/20/30/40%同账户状态机、上一档执行价和MA暂停恢复已实现，并接七股真实输入/显式许可执行器；既有分批/补充问题、许可及未观测本机细节尚待核实，不能以信号收盘代替上一档成交，也未生成收益。",
    14: "共享现金引擎、PIT、真实输入装配与单SID运行CLI已实现，七股10443根及明确交易日历已核齐；150个实际技术候选已有所取得公开来源范围研究，其中47个另有实际生效区间覆盖，两种语义不互换。共享初始100万元及组合/补充模型参数待现有问题答复，显式许可未提供；真实预检0委托/0成交，正式收益为null。",
    20: "共享5仓周度横截面引擎、真实输入装配与单SID运行CLI已实现，七股10443根、CSI官方1648日数学基准及已预告周末已核齐。共享100万元、正式权重和补充模型参数仍待现有答复，本机基准数值对照与执行细节待核；真实预检0订单/0成交，正式收益为null。",
    21: "本机算法已获准；本批选定KC、挤压与动能的本机实现及原生输出核验未完成，不要求跨平台逐值一致作为运行前提。",
}

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))

def decimal_default(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    raise TypeError(type(value).__name__)

def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=decimal_default) + "\n", encoding="utf-8")

def load_auditor(root):
    path = root / "audit_native_exports.py"
    spec = importlib.util.spec_from_file_location("native_export_reader_for_report", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def file_record(root, path):
    return {"path": path.relative_to(root).as_posix(), "bytes": path.stat().st_size, "sha256": sha(path)}

def read_run_source_log(root):
    path = root / "native-run-source-log.jsonl"
    if not path.exists():
        return []
    records = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        record = json.loads(line)
        def alias(names):
            values = [record[name] for name in names if record.get(name) is not None]
            if len({str(value) for value in values}) > 1:
                raise ValueError(f"运行源日志第{line_number}行别名字段冲突: {names}")
            return values[0] if values else None
        record["sid"] = int(alias(("sid", "strategy_id")))
        if record.get("runtime_sid") is not None and int(record["runtime_sid"]) != record["sid"]:
            raise ValueError(f"运行源日志第{line_number}行runtime_sid不一致")
        record["source_path"] = alias(("source_path", "formula_path", "source"))
        record["source_sha256"] = alias(("source_sha256", "formula_sha256"))
        record["summary_sha256"] = alias(("summary_sha256", "native_summary_sha256"))
        record["trades_sha256"] = alias(("trades_sha256", "ledger_sha256", "native_trades_all_sha256", "native_ledger_sha256"))
        record["source_log_line"] = line_number
        record["source_log_path"] = path.name
        records.append(record)
    return records


def current_native_candidates(root, plan, records):
    """The old preparation SID list is a snapshot, not a permanent exclusion.

    Additional adapters require an actual source-log record and hash-correct
    dedicated source. Their files still need the full independent audit below.
    The disabled universal selector cannot upgrade a formerly excluded SID.
    """
    supported = set(plan["supported_sid"])
    for record in records:
        sid = record["sid"]
        if sid in supported or sid not in range(3, 22) or not record.get("source_path") or not record.get("source_sha256"):
            continue
        source = (root / record["source_path"]).resolve()
        if not source.is_relative_to(root) or not source.is_file() or sha(source) != record["source_sha256"]:
            continue
        try:
            source_text = source.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            source_text = source.read_text(encoding="gb18030")
        text = re.sub(r"\{.*?\}", "", source_text, flags=re.S)
        outputs = re.findall(r"(?:ENTERLONG|EXITLONG)\s*:(.*?);", text, flags=re.S)
        if len(outputs) == 2 and all(re.search(rf"\bSID\s*=\s*{sid}\b", expression) for expression in outputs):
            supported.add(sid)
    return supported

def select_actual_run_source(root, sid, summary_path, ledger_path, records):
    summary_hash, ledger_hash = sha(summary_path), sha(ledger_path)
    matches = [r for r in records if r["sid"] == sid and r.get("summary_sha256") == summary_hash
               and r.get("trades_sha256") == ledger_hash]
    if not matches:
        raise ValueError("尚无同时匹配当前汇总及逐笔哈希的实际运行源日志；不按同名公式推定来源。")
    record = dict(matches[-1])
    if not record.get("source_path") or not record.get("source_sha256"):
        raise ValueError("实际运行源日志缺少source_path或source_sha256。")
    source = (root / record["source_path"]).resolve()
    if not source.is_relative_to(root) or not source.is_file() or sha(source) != record["source_sha256"]:
        raise ValueError("实际运行源文件不存在、越界或哈希不匹配；旧来源归属保持原记录。")
    record["source_path"] = source.relative_to(root).as_posix()
    record["current_exports_match_recorded_run_hashes"] = True
    record["source_file_hash_verified"] = True
    return record

def verify_ledger_provenance(root, ledger_path, summary_path, actual_source, expected_codes):
    path = ledger_path.with_name("native-trades-all.derivation.json")
    if not path.exists():
        return {"origin":"direct_native_all_security_export","is_direct_combined_window_native_export":True}
    meta = read_json(path)
    if actual_source.get("derivation_manifest_sha256") and sha(path) != actual_source["derivation_manifest_sha256"]:
        raise ValueError("逐股合并说明哈希与实际运行日志不匹配。")
    if meta["output"]["sha256"] != sha(ledger_path) or meta["summary"]["sha256"] != sha(summary_path):
        raise ValueError("逐股合并文件或汇总哈希不匹配。")
    parts = meta["sources"]
    if len(parts) != 7 or {part["security_code"] for part in parts} != expected_codes:
        raise ValueError("逐股合并源必须恰为指定7只。")
    header = None
    bodies = []
    records = []
    for part in parts:
        original = (root / part["path"]).resolve()
        if not original.is_relative_to(root):
            raise ValueError("逐股原始导出路径越界。")
        content = original.read_bytes()
        if hashlib.sha256(content).hexdigest() != part["sha256"]:
            raise ValueError("逐股原始导出哈希不匹配。")
        boundary = content.index(b"\n") + 1
        current_header, body = content[:boundary], content[boundary:]
        if header is None:
            header = current_header
        elif header != current_header:
            raise ValueError("逐股原始导出表头字节不一致。")
        bodies.append(body)
        records.append(file_record(root, original))
        if sha(original) != part["sha256"]:
            raise ValueError("逐股原始导出在读取期间变化。")
    if header + b"".join(bodies) != ledger_path.read_bytes():
        raise ValueError("全股流水不是7份原生body原字节拼接。")
    if meta.get("returns_fees_or_trade_fields_recomputed") or meta.get("new_trades_generated"):
        raise ValueError("逐股合并说明包含重新生成交易或重算字段，不能当原生行拼接。")
    return {"origin":"derived_concatenation_of_7_native_per_security_exports",
            "is_direct_combined_window_native_export":False,"native_source_count":7,
            "derivation_manifest_path":path.relative_to(root).as_posix(),"derivation_manifest_sha256":sha(path),
            "exact_header_and_native_body_bytes_verified":True,"financial_fields_recomputed":False,
            "new_trades_generated":False,"source_files":records}

def configuration():
    return {
        "client": "通达信金融终端V7.71", "system": "程序交易评测系统",
        "basis": "主执行代理本次原生界面核对及保存的配置证据；不是引擎默认值。",
        "formula": "B321NAT", "selector": "SID", "period": "日线",
        "start": "2021-01-01", "end": "2026-10-23", "price_adjustment": "前复权",
        "independent_initial_capital_per_security_cny": "1000000",
        "independent_account_count": 7, "total_initial_base_per_strategy_cny": "7000000",
        "shared_portfolio": False, "position": "每股独立账户使用全部可用资金；不叠加开仓",
        "formula_output_direction": "仅ENTERLONG/EXITLONG",
        "client_trade_type_label": "多头及空头评测；本批公式仅发多头指令",
        "entry_price": "次周期开盘价", "exit_price": "次周期开盘价",
        "buy_fee_percent": "0.0500", "sell_fee_percent": "0.1500",
        "buy_fee_rate": "0.0005", "sell_fee_rate": "0.0015",
        "fee_scope": "本次用户已接受的软件配置费率，不称市场统一现行费率",
        "slippage_minimum_ticks": 0, "one_price_limit_bar_action": "按本次原生选项延迟",
        "force_end_close_included": True, "extra_stop_conditions": "未设置",
        "requested_extra_history_bars": 2000, "actual_extra_history_may_be_less": True,
        "relative_return_reference": "沪深300", "negative_adjusted_price_bars_excluded_by_client": True,
        "native_adjusted_execution_price_identity_verified": False,
    }


PRECISION_V2_SOURCE_SHA = "f46b14646455e7b64ecd1cc283d8205d4d7819f8e66b93e3535c6344d849f17e"


def verified_reference_file(root, relative, expected_sha):
    path=(root/relative).resolve()
    if not path.is_relative_to(root) or not path.is_file() or sha(path)!=expected_sha:
        raise ValueError("额外真实核验引用文件缺失、越界或哈希过期: "+str(relative))
    return file_record(root,path)


def s21_full_precision_validation(root, expected_codes, actual_source, ledger_path, expected_ledger_rows):
    """Admit only seven fresh native audits; numerical differences stay visible."""
    index_path=root/"native-evaluation/S21-precision-v2/index.json"
    index=read_json(index_path)
    if index.get("status")!="all_seven_audits_saved" or index.get("source_sha256")!=PRECISION_V2_SOURCE_SHA:
        raise ValueError("S21七股高精度审计尚未齐备或不是已批准v2源。")
    items=index.get("securities",[])
    if len(items)!=7 or {r.get("code") for r in items}!=expected_codes:
        raise ValueError("S21高精度审计证券范围不完整或重复。")
    refs=[file_record(root,index_path)]
    refs.append(verified_reference_file(root,"audit_s21_precision_v2.py",index["audit_script_sha256"]))
    refs.append(verified_reference_file(root,"audit_s21_precision_v2_available.py",index["runner_script_sha256"]))
    refs.append(verified_reference_file(root,"native-evaluation/CHECKALL-precision-v2.txt",PRECISION_V2_SOURCE_SHA))
    cfg_sha=hashlib.sha256(json.dumps(configuration(),ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()).hexdigest()
    reports=[];rows=trades=temporal=0
    for item in items:
        refs.append(verified_reference_file(root,item["audit_path"],item["audit_sha256"]))
        r=read_json(root/item["audit_path"])
        if (r["security_code"]!=item["code"] or r["native_source_sha256"]!=PRECISION_V2_SOURCE_SHA
                or r["native_ledger_sha256"]!=sha(ledger_path)
                or r["S21_formula_sha256"]!=actual_source["source_sha256"]
                or r["script_sha256"]!=index["audit_script_sha256"]
                or r["terminal_configuration_evidence"]["client_configuration_canonical_sha256"]!=cfg_sha):
            raise ValueError("S21真实核验的输入、运行源、审计源码或期末配置绑定不符。")
        if (not r.get("source_immutability_rechecked_before_publication")
                or not r.get("all_nonterminal_signal_dates_equal")
                or not r.get("all_native_display_prices_match_v2_fill_session_price")
                or not r.get("terminal_lifecycle",{}).get("matches")
                or not r.get("signals_terminal_and_prices_all_match")):
            raise ValueError("S21真实信号日期、显示价格或期末生命周期未全部一致。")
        refs.append(verified_reference_file(root,r["native_input_path"],r["native_input_sha256"]))
        inventory=r["raw_inventory_used_only_for_dates_and_coverage"]
        refs.append(verified_reference_file(root,inventory["path"],inventory["sha256"]))
        refs.append(verified_reference_file(root,r["native_source_path"],r["native_source_sha256"]))
        refs.append(verified_reference_file(root,"audit_strategy21_reference.py",r["reference_script_sha256"]))
        refs.append(verified_reference_file(root,str(Path(item["audit_path"]).with_name("values.csv")),r["values_csv_sha256"]))
        bind=r["native_source_binding"]["matching_record"]
        if (bind.get("sha256")!=r["native_input_sha256"] or bind.get("source_sha256")!=PRECISION_V2_SOURCE_SHA
                or bind.get("path")!=r["native_input_path"].replace("\\","/")
                or bind.get("code",bind.get("security"))!=item["code"] or bind.get("scale")!=1048576):
            raise ValueError("S21原生导出provenance内容不匹配。")
        provenance_path=(root/r["native_source_binding"]["provenance_path"]).resolve()
        if not provenance_path.is_relative_to(root):raise ValueError("S21导出provenance路径越界。")
        if bind not in [json.loads(line) for line in provenance_path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]:
            raise ValueError("S21原生导出绑定记录已不存在。")
        refs.append(file_record(root,provenance_path))
        if (r["rows"]!=item["rows"] or r["native_trade_rows"]!=item["native_trades"]
                or len(r["price_checks"])!=r["native_trade_rows"]
                or not all(c["same_2dp"] for c in r["price_checks"])):
            raise ValueError("S21真实逐笔价格核验范围或行数不完整。")
        cases=r["temporal_tests"]["cases"]
        if not cases or not all(c["truncation_equal"] and c["synthetic_future_perturbation_equal"] for c in cases):
            raise ValueError("S21独立参考时序检查未通过。")
        rows+=r["rows"];trades+=r["native_trade_rows"];temporal+=r["temporal_tests"]["comparisons"]
        reports.append({"security_code":item["code"],"rows":r["rows"],"native_trade_rows":r["native_trade_rows"],"signal_dates_verified":True,"display_execution_prices_verified":True,"terminal_lifecycle_verified":True,"metrics":r["metrics"],"audit_path":item["audit_path"],"audit_sha256":item["audit_sha256"]})
    if trades!=expected_ledger_rows or trades!=index["aggregate"]["native_trade_rows"] or rows!=index["aggregate"]["input_rows"]:
        raise ValueError("S21七股核验行数与当前原生完整流水或索引不一致。")
    return {"indicator_numeric_values_verified":True,"indicator_numeric_comparison_completed":True,
        "indicator_values_exactly_equal":False,"indicator_bitwise_identity_verified":False,
        "adjusted_execution_prices_verified":True,"signal_dates_verified":True,"terminal_lifecycle_verified":True,
        "execution_permission_verified":False,"status":"seven_security_native_signal_price_terminal_verified_numeric_differences_retained",
        "meaning":f"七股真实本机值已逐行比较，{trades}笔信号日期、前复权次开成交显示价及期末状态一致；数值初值/舍入差保留，不宣称内部bit或实际交易许可一致。",
        "scope":"actual_native_FQ1_precision_v2_all7; execution_price_comparison_at_native_two_decimal_display_only",
        "validated_security_codes":sorted(expected_codes),"native_input_rows":rows,"validated_native_trade_rows":trades,
        "reference_temporal_comparisons":temporal,"numeric_comparison":index["aggregate"]["indicators"],
        "numeric_exceptions":["七股各自首个完整20根STDP窗口（BC19）原生为0，原样保留；S21信号起点BC40/41不访问该初值。","300750于2023-10-19的FC原生-18.1649951935、独立-18.1650011008，差5.91e-6导致两位舍入不同，当日无买卖信号。","高精度逐值差异全留CSV；没有调整epsilon使结果通过。"],
        "per_security":reports,"evidence_path":index_path.relative_to(root).as_posix(),
        "evidence_sources":list({r["path"]:r for r in refs}.values())}


def s08_price_validation_only(root, expected_codes, actual_source, ledger_path, expected_ledger_rows):
    refs=[];count=0
    for code in sorted(expected_codes):
        path=root/f"native-evaluation/additional-6-8/S08-{code}-native-v2-check.json"
        r=read_json(path)
        if (r["security_code"]!=code or not r["all_native_display_fill_prices_match"]
                or len(r["all_native_trade_checks"])!=r["native_security_trade_rows"]
                or not all(c["native_price_matches_input_at_display_precision"] for c in r["all_native_trade_checks"])):
            raise ValueError("S8逐笔显示价格范围或核验未完成。")
        hashes={s["path"]:s["sha256"] for s in r["sources"]}
        if (hashes.get(actual_source["source_path"])!=actual_source["source_sha256"]
                or hashes.get(ledger_path.relative_to(root).as_posix())!=sha(ledger_path)
                or r["native_input_binding"]["source_sha256"]!=PRECISION_V2_SOURCE_SHA):
            raise ValueError("S8逐笔价格证据与实际运行源/流水/v2输入不符。")
        if r.get("explicit_native_client_configuration")!=configuration():
            raise ValueError("S8专项真实核验配置与已确认配置不符。")
        for ref in r["sources"]:
            if ref["path"]=="run-manifest.json":
                # Public reports refresh this output. Keep the exact original
                # full-byte configuration evidence, rather than accepting a
                # changed same-name manifest or a self-referential hash.
                candidates=[root/"report-history/run-manifest-by-sha256"/(ref["sha256"]+".json"),
                    root/"report-history/before-S21-full-validation-20261025-015628/run-manifest.json"]
                original=next((p for p in candidates if p.is_file() and sha(p)==ref["sha256"]),None)
                if original is None:
                    raise ValueError("S8原审计引用的完整run-manifest快照尚未保存。")
                if read_json(original)["client_configuration"]!=configuration():
                    raise ValueError("S8原完整run-manifest快照的配置不匹配。")
                refs.append(file_record(root,original))
            else:
                refs.append(verified_reference_file(root,ref["path"],ref["sha256"]))
        refs.append(file_record(root,path));count+=r["native_security_trade_rows"]
    if count!=expected_ledger_rows:raise ValueError("S8逐笔价格核验行数未覆盖全原生流水。")
    return {"adjusted_execution_prices_verified":True,"validated_native_trade_price_rows":count,
        "execution_price_scope":"all7 actual_native_FQ1_v2 next_open_or_explicit_terminal_close, native two_decimal_display",
        "signal_dates_verified":False,"indicator_numeric_values_verified":False,
        "status":"all_native_files_and_display_prices_saved_full_signal_validation_pending",
        "full_signal_validation_status":"pending_605488_actual_native_boolean_boundaries",
        "pending_native_boolean_security_codes":["605488"],"pending_native_boolean_dates":["2022-05-30","2022-06-09","2022-09-02"],
        "arithmetic_candidate_is_not_native_boolean_confirmation":True,"execution_permission_verified":False,
        "evidence_sources":list({r["path"]:r for r in refs}.values())}


def scale_in_preflight_evidence(root,sid):
    path=root/"native-evaluation/scale-in/real-input/preflight/preflight.json"
    r=read_json(path)
    if (r["kind"]!="actual_native_input_preflight_not_a_backtest" or r["security_count"]!=7
            or r["full_native_rows"]!=10443 or r["native_source_sha256"]!=PRECISION_V2_SOURCE_SHA
            or r["orders_generated"] is not False or r["fills_generated"] is not False or r["formal_returns"] is not None
            or sid not in r.get("strategy_ids",(11,13))):
        raise ValueError("分批真实输入预检状态或无收益边界不符。")
    refs=[file_record(root,path)]
    for item in r["sources"]:refs.append(verified_reference_file(root,item["path"],item["sha256"]))
    refs.append(verified_reference_file(root,r["permission_gaps_path"],r["permission_gaps_sha256"]))
    return {"kind":"read_only_real_input_preflight_no_execution","path":path.relative_to(root).as_posix(),"sha256":sha(path),"native_rows":r["full_native_rows"],"active_rows":r["active_rows"],"permission_gap_rows":r["permission_gap_rows"],"pending_confirmation_fields":r["pending_confirmation_fields"],"unobserved_native_behavior_fields":r["unobserved_native_behavior_fields"],"orders_generated":False,"fills_generated":False,"formal_returns":None,"source_files":list({r["path"]:r for r in refs}.values())}


def portfolio_adapter_evidence(root,sid):
    index_path=root/'portfolio14-20/native-runner/validation/index.json';index=read_json(index_path)
    refs=[file_record(root,index_path),verified_reference_file(root,index['path'],index['sha256'])]
    evidence=read_json(root/index['path'])
    if (evidence['kind']!='actual_input_adapter_readiness_not_a_backtest' or evidence['status']!='passed'
        or evidence['orders_generated'] is not False or evidence['fills_generated'] is not False
        or evidence['formal_returns'] is not None):raise ValueError('组合适配器证据不是无收益的真实预检。')
    tests=next(c for c in evidence['commands'] if c['name']=='adapter-tests')
    if tests['exit_code']!=0 or tests['test_count']!=evidence['adapter_tests']:raise ValueError('组合适配器测试记录不一致。')
    for command in evidence['commands']:
        if command['exit_code']!=command['expected_exit_code']:raise ValueError('组合适配器命令未通过。')
        refs.append(verified_reference_file(root,command['log'],command['log_sha256']))
    for item in evidence['source_hashes']:
        source=(root.parents[1]/item['project_path']).resolve()
        if not source.is_relative_to(root.parents[1]) or sha(source)!=item['sha256']:raise ValueError('组合适配器实现源码已改变。')
        if source.is_relative_to(root):refs.append(file_record(root,source))
    selected=[o for o in evidence['outcomes'] if o['strategy_id']==sid]
    if {o['mode'] for o in selected}!={'preflight','modeled-backtest'} or len(selected)!=2:raise ValueError('组合适配器缺所选SID独立门禁证据。')
    preflight=None
    for item in selected:
        refs.append(verified_reference_file(root,item['path'],item['sha256']));r=read_json(root/item['path'])
        if (r['strategy_id']!=sid or r['full_native_rows']!=10443 or r['active_rows']!=9387
            or r['formal_returns'] is not None or r['orders_generated'] or r['fills_generated']
            or not r['all_sources_rechecked_before_publication'] or r['selected_rules_confirmed']
            or r['fact_loader_errors']):raise ValueError('组合适配器真实预检或pending无收益边界不符。')
        expected='inputs_verified_no_replay' if item['mode']=='preflight' else 'blocked_no_formal_financial_result'
        if r['status']!=expected:raise ValueError('组合适配器运行状态不符。')
        for record in r['sources']:
            source=((root.parents[1] if record.get('repository_relative') else root)/record['path']).resolve()
            if not source.is_relative_to(root.parents[1]) or sha(source)!=record['sha256']:raise ValueError('组合真实预检源哈希不符。')
            if source.is_relative_to(root):refs.append(file_record(root,source))
        for record in r['artifacts']:refs.append(verified_reference_file(root,record['path'],record['sha256']))
        if item['mode']=='preflight':preflight=r
    return {'kind':'real_input_shared_portfolio_adapter_preflight_no_execution','path':index['path'],'sha256':index['sha256'],
        'adapter_test_count':evidence['adapter_tests'],'native_rows':preflight['full_native_rows'],'active_rows':preflight['active_rows'],
        'permission_unknown_inventory_count':preflight['permission_unknown_inventory_count'],'inventory_is_not_all_required_orders':True,
        'market_sessions':preflight['market_sessions'],'preannounced_week_end_count':preflight['preannounced_week_end_count'],
        'fact_bundle_count':preflight['fact_bundle_count'],'benchmark_available_mode':preflight['benchmark_available_mode'],
        'pending_modeled_guard_passed':True,'orders_generated':False,'fills_generated':False,'formal_returns':None,
        'source_files':list({r['path']:r for r in refs}.values())}


def completion_accounting(entries,supported,implementation_pending):
    saved=[e["strategy_id"] for e in entries if e["status"]=="native_files_saved"]
    accepted=[e["strategy_id"] for e in entries if e["status"]=="user_accepted_with_incomplete_ledger"]
    missing=[e["strategy_id"] for e in entries if e["strategy_id"] in supported and e["status"] not in ("native_files_saved","user_accepted_with_incomplete_ledger")]
    validation=[]
    for e in entries:
        extra=e.get("additional_native_validation",{})
        if e["strategy_id"]==8 and e["status"]=="native_files_saved" and not all(extra.get(k) for k in ("signal_dates_verified","adjusted_execution_prices_verified","terminal_lifecycle_verified")):
            validation.append(8)
        if e["strategy_id"]==21 and e["status"]=="native_files_saved" and not all(extra.get(k) for k in ("signal_dates_verified","adjusted_execution_prices_verified","terminal_lifecycle_verified")):
            validation.append(21)
    remaining=sorted(set(implementation_pending)|set(missing)|set(validation))
    return {"accepted_or_saved_strategy_count":len(saved)+len(accepted),"accepted_or_saved_strategy_ids":sorted(saved+accepted),"remaining_work_strategy_ids":remaining,"saved_native_validation_work_remaining_strategy_ids":validation,"admitted_sources_waiting_native_files_strategy_ids":missing,"pending_supported_strategy_ids_meaning":"Only hash-admitted source candidates still waiting for complete native files/audit; this is not the total remaining task. Use remaining_work_strategy_ids.","original_request_still_open":bool(remaining),"file_save_or_user_acceptance_is_not_full_validation":True}


def portfolio_research_progress(root):
    folder=root/"portfolio14-20";path=folder/"S14-candidate-share-input-integration.json"
    r=read_json(path);summary=r["summary"];candidates=r["candidates"]
    if (r["kind"]!="scoped_candidate_share_input_research_not_a_backtest" or len(summary)!=7
            or {x["symbol"] for x in summary}!={"300308","300750","601899","603728","605488","688017","688322"}
            or sum(x["technical_candidates"] for x in summary)!=150 or len(candidates)!=150
            or len({(x["symbol"],x["technical_candidate_date"]) for x in candidates})!=150
            or any(x.get("formal_profile_confirmed") or x.get("orders")!=0 or x.get("returns") is not None for x in candidates)):
        raise ValueError("组合候选研究范围或零订单/无收益状态不符。")
    refs=[file_record(root,path)]
    for item in r["inputs"]:
        rel=(folder/item["path"]).resolve().relative_to(root).as_posix()
        refs.append(verified_reference_file(root,rel,item["sha256"]))
    for item in summary:
        own=[c for c in candidates if c["symbol"]==item["symbol"]]
        if (len(own)!=item["technical_candidates"]
                or sum(bool(c.get("actual_effective_audited_interval")) for c in own)!=item["A_verified_interval_candidates"]
                or sum(bool(c.get("latest_known_scoped_research")) for c in own)!=item["B_scoped_research_candidates"]):
            raise ValueError("组合候选逐股统计与实际研究行数不符。")
    return {"kind":r["kind"],"status":"research_preparation_only_not_formal_strategy_execution","path":path.relative_to(root).as_posix(),"sha256":sha(path),"technical_candidate_count":150,"A_verified_interval_candidates":sum(x["A_verified_interval_candidates"] for x in summary),"B_scoped_research_candidates":sum(x["B_scoped_research_candidates"] for x in summary),"per_security":summary,"B_all_public_notice_completeness_claimed":False,"share_model_confirmed":False,"orders_generated":0,"formal_returns":None,"evidence_sources":refs}


def verified_checkpoint_tests(root,checkpoint):
    test=checkpoint["project_test"];path=root/test["evidence"];evidence=read_json(path)
    if evidence["status"]!="passed":raise ValueError("执行器/项目测试证据未通过。")
    run=next(r for r in evidence["commands"] if r["name"]=="project-unittest")
    if run["exit_code"]!=0 or run["test_count"]!=test["tests"] or run["log_sha256"]!=test["log_sha256"]:
        raise ValueError("当前检查点项目测试数量或日志哈希与原证据不匹配。")
    refs=[file_record(root,path),verified_reference_file(root,run["log"],run["log_sha256"])]
    for item in evidence["source_hashes"]:
        source=(root.parents[1]/item["project_path"]).resolve()
        if not source.is_relative_to(root.parents[1]) or sha(source)!=item["sha256"]:
            raise ValueError("项目测试所绑定实现或输入准备证据已改变。")
        if source.is_relative_to(root):refs.append(file_record(root,source))
    runner=next(r for r in evidence['commands'] if r['name']=='runner-and-permission-regressions')
    state=next(r for r in evidence['commands'] if r['name']=='state-synthetic-tests')
    return {**test,"evidence_sha256":sha(path),'runner_and_permission_test_count':runner['test_count'],'state_test_count':state['test_count'],"implementation_source_hashes":evidence["source_hashes"],"evidence_sources":refs}

def accepted_incomplete_summary(root, sid, expected_codes, reader, run_records, acceptance, partial=None):
    """Expose source-bound native summary without upgrading a partial ledger."""
    folder=root/"native-results"/f"S{sid:02d}"
    summary_path=folder/"native-summary.xls"
    partial_path=folder/"native-trades-partial.manifest.json"
    partial=read_json(partial_path) if partial is None else partial
    raw,meta=reader.read_native_tsv(summary_path,reader.SUMMARY_COLUMNS)
    rows=[reader.parse_summary(r) for r in raw]
    aggregates=[r for r in rows if r["aggregate"]]
    stocks=[r for r in rows if not r["aggregate"]]
    if len(rows)!=8 or len(aggregates)!=1 or len(stocks)!=7 or {r["code"] for r in stocks}!=expected_codes:
        raise ValueError("用户接受项的原生汇总仍须为综合+7股且代码完整。")
    matches=[r for r in run_records if r["sid"]==sid and r.get("summary_sha256")==meta["sha256"] and r.get("source_path") and r.get("source_sha256")]
    if not matches:
        raise ValueError("用户接受不能替代原生汇总与实际运行源的哈希绑定。")
    source=dict(matches[-1])
    path=(root/source["source_path"]).resolve()
    if not path.is_relative_to(root) or not path.is_file() or sha(path)!=source["source_sha256"]:
        raise ValueError("用户接受项的实际运行源哈希不匹配。")
    if partial["summary_sha256"]!=meta["sha256"] or partial["source_sha256"]!=source["source_sha256"]:
        raise ValueError("部分流水清单的源/汇总哈希不匹配。")
    files=[]
    saved_codes=set()
    actual_rows=0
    for item in partial["files"]:
        original=(root/item["path"]).resolve()
        if not original.is_relative_to(root):
            raise ValueError("部分流水路径越界。")
        body,body_meta=reader.read_native_tsv(original,reader.TRADE_COLUMNS)
        code=item["security_code"]
        if code in saved_codes or code not in expected_codes or {r["品种代码"] for r in body}!={code}:
            raise ValueError("部分流水证券范围不符或重复。")
        if body_meta["sha256"]!=item["sha256"] or len(body)!=item["native_rows"]:
            raise ValueError("部分流水原字节哈希或行数不匹配。")
        actual_rows+=len(body)
        saved_codes.add(code)
        files.append(file_record(root,original))
    expected_rows=aggregates[0]["total_times"]*2
    missing_codes=expected_codes-saved_codes
    if (len(files)!=partial["native_per_security_files_saved"] or actual_rows!=partial["native_rows_saved"]
        or actual_rows!=acceptance["native_rows_preserved"] or len(files)!=acceptance["native_per_security_files_preserved"]
        or actual_rows>=expected_rows or missing_codes!={acceptance["missing_security_code"]}
        or {r["security_code"] for r in partial["missing_native_files"]}!=missing_codes
        or sum(r["expected_entry_and_exit_rows"] for r in partial["missing_native_files"])!=expected_rows-actual_rows
        or expected_rows-actual_rows!=acceptance["missing_native_rows"]):
        raise ValueError("用户接受不能把1304条部分流水升级为1476条完整流水。")
    if (folder/"native-trades-all.xls").exists():
        raise ValueError("完整流水存在时必须走完整原生审计，不走部分接受分支。")
    source.update(current_summary_matches_recorded_run_hash=True,source_file_hash_verified=True,full_ledger_bound=False)
    return {"status":"user_accepted_with_incomplete_ledger","status_zh":"用户接受收尾（流水不全）",
        "native_summary":aggregates[0],"per_security":stocks,"summary_row_count":8,
        "summary_codes":[r["code"] for r in stocks],"returns":{"native_return_pct":aggregates[0]["native_return_pct"],"scope":"native_summary_only_full_ledger_not_audited"},
        "native_run_source":source,"source_files":[file_record(root,summary_path)]+files,
        "audit":{"status":"summary_and_partial_exports_only","full_ledger_audit_completed":False,"partial_native_rows":actual_rows,"missing_native_rows":expected_rows-actual_rows,"missing_codes":sorted(missing_codes),"path":partial_path.relative_to(root).as_posix()},
        "ledger_provenance":{"origin":"partial_native_per_security_exports","is_direct_combined_window_native_export":False,"full_ledger_available":False,"native_source_count":len(files),"source_files":files},
        "partial_native_ledger":{"native_per_security_files_saved":len(files),"native_rows_saved":actual_rows,"expected_full_entry_exit_rows":expected_rows,"missing_native_files":partial["missing_native_files"],"manifest_path":partial_path.relative_to(root).as_posix()},
        "user_acceptance_basis":"authorization.json#strategy17_incomplete_ledger_acceptance",
        "full_ledger_audit_completed":False,"native_files_saved_claim":False,"include_in_return_ranking":False,
        "pending_cancel_or_manual_recovery_decision":False}

def read_strategy(root, sid, expected_codes, supported, native_plan, reader, run_records):
    name_paths = list((root.parents[1] / "docs/strategies").glob(f"策略{sid}-*.md"))
    title = name_paths[0].stem.split("-", 1)[1] if name_paths else f"策略{sid}"
    entry = {"strategy_id": sid, "name": title, "status": "in_progress", "status_zh": "进行中",
             "returns": None, "native_summary": None, "per_security": None,
             "include_in_return_ranking": False, "source_files": [], "audit": None,
             "native_run_source": None}
    folder = root / "native-results" / f"S{sid:02d}"
    summary_path, ledger_path = folder / "native-summary.xls", folder / "native-trades-all.xls"
    entry["native_export_presence"] = {"summary_file_saved": summary_path.is_file(), "full_ledger_file_saved": ledger_path.is_file()}
    entry["additional_native_validation"] = {"indicator_numeric_values_verified": False,
        "adjusted_execution_prices_verified": False, "status": "pending_separate_numeric_and_execution_checks",
        "meaning": "保存及文件审计完成不等于指标逐值、复权与实际成交许可已核验。"}
    audit_path = root / "native-audit" / f"S{sid:02d}-audit.json"
    partial_path = folder / "native-trades-partial.manifest.json"
    if partial_path.is_file():
        partial=read_json(partial_path)
        if summary_path.is_file() and partial.get("summary_sha256")==sha(summary_path):
            entry["partial_native_ledger"]={k:partial[k] for k in ("native_per_security_files_saved","native_rows_saved","expected_full_entry_exit_rows","missing_native_files")}
            entry["partial_native_ledger"]["manifest_path"]=partial_path.relative_to(root).as_posix()
    if sid not in supported:
        entry.update(status="in_progress", status_zh="实现/本机验证中",
                     pending_reason=UNSUPPORTED_REASONS.get(sid, "本机实现、输出及实际来源绑定尚未完成。"),
                     local_variant_authorization_status="user_confirmed",
                     implementation_validation_status="not_ready_in_this_batch",
                     not_run_policy_basis="authorization.json#approved_rule_version.complex_strategy_policy",
                     permanent_platform_incapability_claim=False,
                     missing_core_rules_may_be_silently_replaced=False)
        if sid in (11,13):
            try:entry["implementation_preflight"]=scale_in_preflight_evidence(root,sid)
            except (ValueError,KeyError,OSError,UnicodeError) as exc:entry["implementation_preflight_error"]=str(exc)
        if sid in (14,20):
            try:entry['implementation_preflight']=portfolio_adapter_evidence(root,sid)
            except (ValueError,KeyError,OSError,UnicodeError,StopIteration) as exc:entry['implementation_preflight_error']=str(exc)
        if entry.get('implementation_preflight'):
            entry['implementation_validation_status']='real_input_preflight_ready_formal_execution_pending'
        if summary_path.exists() or ledger_path.exists():
            entry["pending_reason"] += " 当前已发现导出文件，但缺少可核验新增源码的实际运行绑定，不能据同名公式推定。"
        return entry
    acceptance=read_json(root/"authorization.json").get("strategy17_incomplete_ledger_acceptance",{})
    if sid==17 and acceptance.get("status")=="user_accepted_with_incomplete_ledger" and not ledger_path.exists():
        try:
            entry.update(accepted_incomplete_summary(root,sid,expected_codes,reader,run_records,acceptance))
        except (ValueError,KeyError,OSError,UnicodeError) as exc:
            entry["error"]=str(exc)
        return entry
    if not summary_path.exists() or not ledger_path.exists() or not audit_path.exists():
        entry["pending_reason"] = "等待本SID原生汇总、全股逐笔及对应审计全部保存。"
        if entry.get("partial_native_ledger"):
            partial=entry["partial_native_ledger"]
            entry["pending_reason"]=f"已保存{partial['native_per_security_files_saved']}/7股、{partial['native_rows_saved']}/{partial['expected_full_entry_exit_rows']}条原生逐股流水；等待剩余导出和全股审计。"
        return entry
    try:
        raw, meta = reader.read_native_tsv(summary_path, reader.SUMMARY_COLUMNS)
        rows = [reader.parse_summary(row) for row in raw]
        aggregates = [r for r in rows if r["aggregate"]]
        stocks = [r for r in rows if not r["aggregate"]]
        codes = [r["code"] for r in stocks]
        if len(rows) != 8 or len(aggregates) != 1 or len(codes) != 7 or set(codes) != expected_codes:
            raise ValueError("原生汇总必须为综合统计+7只指定股票，且代码无重复。")
        audit = read_json(audit_path)
        if not audit.get("status", "").startswith("native_exports_audited"):
            raise ValueError("对应SID审计尚未完成或报错。")
        if not audit.get("summary_codes_match_expected") or audit.get("ledger_codes_outside_expected"):
            raise ValueError("对应SID审计证券范围不匹配。")
        if (not audit.get("aggregate_total_times_matches_roundtrips")
                or audit.get("expected_trading_codes_missing_ledger")
                or any(row.get("sequence_errors") or not row.get("native_total_times_matches_roundtrips")
                       for row in audit.get("per_security", []) if row.get("native_total_times"))):
            raise ValueError("对应SID流水配对、逐股次数或完整范围仍有不符。")
        native_sources = {Path(s["path"]).name: s for s in audit["source_files"]}
        for path in (summary_path, ledger_path):
            if path.name not in native_sources or sha(path) != native_sources[path.name]["sha256"]:
                raise ValueError("审计输入哈希已过期，须先重新运行audit_native_exports.py。")
        actual_source = select_actual_run_source(root, sid, summary_path, ledger_path, run_records)
        ledger_provenance = verify_ledger_provenance(root, ledger_path, summary_path, actual_source, expected_codes)
        agg = aggregates[0]
        fees_sum = sum((r["native_fees"] for r in stocks), ZERO)
        net_sum = sum((r["native_net_profit"] for r in stocks), ZERO)
        totals = sum(r["total_times"] for r in stocks)
        if totals != agg["total_times"]:
            raise ValueError("原生总次数与7股行合计不一致；保留原导出待核对。")
        audit_brief = {k:audit.get(k) for k in (
            "status", "ledger_rows", "total_completed_roundtrips", "aggregate_total_times",
            "aggregate_total_times_matches_roundtrips", "ledger_display_fee_sum",
            "summary_aggregate_display_fees", "ledger_minus_aggregate_fee",
            "summary_component_net_minus_aggregate")}
        audit_brief["path"] = audit_path.relative_to(root).as_posix()
        audit_brief["sha256"] = sha(audit_path)
        audit_brief["stock_display_fee_sum_minus_native_aggregate"] = fees_sum - agg["native_fees"]
        audit_brief["stock_display_net_sum_minus_native_aggregate"] = net_sum - agg["native_net_profit"]
        audit_brief["forced_end_close_rows"] = audit.get("date_price_audit", {}).get("forced_close_count")
        audit_brief["sequence_error_count"] = sum(len(s.get("sequence_errors", [])) for s in audit.get("per_security", []))
        audit_brief["front_adjusted_execution_price_verified"] = False
        entry.update(status="native_files_saved", status_zh="已保存", native_summary=agg,
                     per_security=stocks, returns={"native_return_pct": agg["native_return_pct"]},
                     summary_row_count=len(rows), summary_codes=codes, audit=audit_brief,
                     native_run_source=actual_source,
                     ledger_provenance=ledger_provenance,
                     source_files=[file_record(root, path) for path in (summary_path, ledger_path)])
        if sha(summary_path) != meta["sha256"]:
            raise ValueError("汇总导出在读取期间变化。")
        plan = next((s for s in native_plan["strategies"] if s["sid"] == sid), {})
        entry["confirmed_local_rule_departures"] = plan.get("confirmed_local_settings_and_source_departures", []) if sid in set(native_plan["supported_sid"]) else []
        if sid == 8:
            entry["confirmed_local_rule_departures"] = ["过去5根已完成H/L通道加减本机MINDIFF；完成当日日线H/L触发后次开成交", "原动态STOP成交改为本机完成日线确认版本；只做多，同日上下侧都触发时退出优先"]
            subset_path=root/"native-evaluation/additional-6-8/S08-688322-real-native-check.json"
            if subset_path.is_file():
                subset=read_json(subset_path)
                refs=subset.get("sources",[])
                fresh=bool(refs) and all((root/r["path"]).resolve().is_relative_to(root)
                    and (root/r["path"]).is_file() and sha(root/r["path"])==r["sha256"] for r in refs)
                if fresh and subset.get("status")=="pass":
                    entry["additional_native_validation"].update(independent_signal_date_subset={
                        "security_code":"688322","native_rows":subset["security_ledger_rows"],
                        "all_projected_action_dates_match":subset["independent_signal_lifecycle_matches_native_all_dates"],
                        "signal_condition_matches":subset["signal_date_condition_matches"],
                        "raw_candidate_price_exact_matches":subset["price_exact_matches"],
                        "scope":"root_confirmed_no_action_688322_only; not_other_securities_or_permissions"},
                        evidence_path=subset_path.relative_to(root).as_posix())
                else:
                    entry["additional_native_validation"]["optional_reference_status"]="stale_or_not_passed; native file-save status unaffected"
        if sid == 21 and (root/"native-evaluation/S21-adaptation-manifest.json").is_file():
            adapter = read_json(root/"native-evaluation/S21-adaptation-manifest.json")
            if adapter["source_sha256"] == actual_source["source_sha256"]:
                entry["confirmed_local_rule_departures"] = adapter["source_differences"]
                entry["additional_native_validation"]["source_rule_manifest"] = "native-evaluation/S21-adaptation-manifest.json"
            else:
                entry["additional_native_validation"]["optional_rule_manifest_status"]="stale_source_hash; current native export binding remains separately verified"
            subset_path = root/"native-evaluation/S21-reference/688322-reference-review.json"
            if subset_path.is_file():
                subset=read_json(subset_path)
                if subset["native_ledger_sha256"] == sha(ledger_path) and subset["native_formula_sha256"] == actual_source["source_sha256"]:
                    entry["additional_native_validation"].update(
                        independent_signal_date_subset={"security_code":"688322","native_rows":subset["native_rows_for_security"],"all_projected_action_dates_match":subset["population_current_endpoint_projection_matches_native"],"scope":"raw_candidate_only; not_all_securities_or_numeric_values"},
                        evidence_path=subset_path.relative_to(root).as_posix())
        if sid in (8, 21):
            # Optional numerical QA cannot erase hash-bound native financial files.
            # A stale or incomplete QA graph remains pending, never a false pass.
            try:
                validator = s21_full_precision_validation if sid == 21 else s08_price_validation_only
                extra = validator(root, expected_codes, actual_source, ledger_path, audit["ledger_rows"])
                entry["additional_native_validation"].update(extra)
                entry["audit"]["front_adjusted_execution_price_verified"] = extra["adjusted_execution_prices_verified"]
                if sid == 21 and entry["additional_native_validation"].get("independent_signal_date_subset"):
                    entry["additional_native_validation"]["legacy_subset_superseded_by"] = extra["evidence_path"]
            except (ValueError, KeyError, OSError, UnicodeError) as exc:
                entry["additional_native_validation"].update(
                    validation_evidence_error=str(exc),
                    optional_reference_status="incomplete_or_stale_full_precision_evidence; native file-save status unaffected")
        calendar_audit_path = root / "native-audit" / f"S{sid:02d}-calendar-audit.json"
        if calendar_audit_path.exists():
            calendar_audit = read_json(calendar_audit_path)
            if calendar_audit["native_export_hashes"] != {p.name:sha(p) for p in (summary_path,ledger_path)}:
                raise ValueError("信号日历审计输入哈希已过期。")
            if not all(calendar_audit["checks"].values()):
                raise ValueError("信号日期与日历锚点或公告时序存在不符。")
            entry["calendar_signal_audit"] = {k:calendar_audit[k] for k in ("status","ordinary_signal_count","native_roundtrip_count","forced_end_close_count","anchor_date_mismatch_count","immediate_next_month_pair_mismatch_count","source_publication_timing_mismatch_count")}
            entry["calendar_signal_audit"]["path"] = calendar_audit_path.relative_to(root).as_posix()
    except (ValueError, KeyError, OSError, UnicodeError) as exc:
        entry.update(status="in_progress", status_zh="进行中", returns=None,
                     native_summary=None, per_security=None, audit=None, native_run_source=None, error=str(exc))
    return entry

def md_number(value, precision=None):
    if value is None:
        return "N/A"
    number = Decimal(str(value))
    return format(number, f",.{precision}f") if precision is not None else format(number, "f")

def markdown(data, post_audit):
    entries = data["strategies"]
    saved = [e for e in entries if e["status"] == "native_files_saved"]
    accepted = [e for e in entries if e["status"] == "user_accepted_with_incomplete_ledger"]
    prefix = "最终保存版" if data["status"] == "final" else "进行中版本"
    remaining=data.get("remaining_work_strategy_ids",[])
    progress_note = "尚余工作为策略"+"、".join(str(sid) for sid in remaining)+"；保存状态与完整验证分开记录。" if remaining else "当前记录无剩余实施工作。"
    price_verified = [e["strategy_id"] for e in saved if e["additional_native_validation"].get("adjusted_execution_prices_verified")]
    price_note = ("、".join(f"S{sid}" for sid in price_verified)+"已用同次本机前复权高精度导出核对全部逐笔显示成交价；其余策略的执行价口径未据此自动验证。") if price_verified else "各策略执行价格额外核验仍待完成。"
    login_note = "用户已确认“已经登录，继续没有完成的作业”，登录已完成，现继续恢复剩余评测；不再等待登录或强退审批。" if data.get("client_session_state", {}).get("client_login_completed") else ""
    if data.get("client_session_state",{}).get("manual_recovery_completed"):
        login_note="保存窗口曾无响应，用户回复“应该好了”后已确认恢复，688017的212条原生导出已校验归档。当前继续剩余导出，不再等待用户手动恢复、登录或强退审批。"
    if data.get("client_session_state",{}).get("technical_blocked"):
        login_note="当前最后688322明细加载停在“计算浮动盈亏(391/391)”。取消本次停滞加载被自动审批拒绝，原因是可能丢失尚未导出的172条、坐标一致性存疑；已请求用户明确允许取消重开或继续等待。此前SaveDialog手动恢复已完成，本次不是同一阻塞。保持进行中，不自动执行取消或强退。"
    if accepted:
        login_note="S17按用户明确接受收尾，保留原生汇总及6股1304条，缺688322的172条；不再等待取消决定或手动恢复，不假装全流水已审计。其余尚未完成项继续推进，新增原生文件逐项更新。"
    if data.get("client_session_state",{}).get("awaiting_login"):
        login_note+=" 当前客户端处于登录页，剩余原生评测等待用户手动登录，独立数据和执行器准备继续。"
    lines = ["# 策略3—21通达信原生评测结果", "",
        f"{prefix}，生成于{data['created_at_local']}。{len(saved)}项原生汇总/完整流水已保存并完成文件审计，{len(accepted)}项用户接受收尾但流水不全，合计{len(saved)+len(accepted)}项属于已保存或用户接受状态。{progress_note}",
        "", "原生文件保存、逐笔文件审计与额外指标逐值/成交核验是不同状态。表内“已保存”不表示全部本机指标数值、前复权序列或成交许可已验证。",
        "", "以下收益直接读取通达信原生导出。"+price_note+"价格比较限于原生流水两位显示精度，不证明复权算法或实际成交许可。金额和比例使用Decimal读取，显示差额原样保留，不补账。",
        "", "## 评测口径", "",
        "通达信金融终端V7.71，日线，2021-01-01至2026-10-23；公式B321NAT按SID选择用户已批准的本机版：只做多、收盘确认、次周期开盘成交，CCI20。每只股票独立100万元，每策略总基数700万元，使用全部可用资金；七个独立账户不是共享资金组合。",
        "", "前复权；买入费0.0500%、卖出费0.1500%；滑点0个最小变动价位；按原生选项处理一字板延迟；期末强制平仓计入收益，未设额外止损。向前多取2000根是软件请求值，并不表示每只股票真有2000根历史。参照品种为沪深300；客户端提示复权后负价数据不参与评测。界面交易类型显示多头及空头评测，但本批公式只输出多头开平指令。",
        "", "原生年化收益为软件线性口径，不是CAGR。原生综合回撤不是共享组合逐日权益回撤；没有用7个单股回撤构造组合日回撤。原生总次数与逐笔行数分别保存，能与开平配对吻合时才标注为往返次数。",
        "", "配置证据：[完整设置报告](ui-evidence/S03-complete-native-settings-report.txt)、[公共交易设置](ui-evidence/common-trade-settings-confirmed-0.jpg)、[用户授权](authorization.json)、[本机规则差异](native-evaluation/confirmed-rules.md)。",
        "", "用户已手动结束此前客户端，并明确要求“重启通达信继续完成任务”；重启已获授权。同名B321NAT可能在不同SID运行中使用万能源或最小等价源，逐次归属以[native-run-source-log.jsonl](native-run-source-log.jsonl)中绑定原导出哈希的记录为准，不用当前同名正文追改旧运行来源。",
        "", login_note,
        "", "S10恢复时曾因旧会话闭包保存重复旧截图；这些旧图不作为其文件名对应步骤的真实证据。当前原生导出已重新核对，另保存当前汇总与逐笔图；具体限制及旧新归属见[界面证据限制](界面证据限制.md)。",
        "", "## 19项状态与原生综合统计", "",
        "表按策略编号排列。N/A表示该字段尚无可展示的已绑定原生结果，不表示零收益或软件永久不支持。各策略重复使用同一组独立账户，不能把各策略净利润相加当作一份组合收益。", "",
        "|策略|状态|原生收益率%|原生净利润(元)|原生手续费(元)|原生线性年化%|原生回撤比%|",
        "|---|---|---:|---:|---:|---:|---:|"]
    for e in entries:
        a = e["native_summary"] or {}
        label = f"{e['strategy_id']} {e['name']}"
        if e["status"] in ("native_files_saved","user_accepted_with_incomplete_ledger"):
            label = f"[{label}](native-results/S{e['strategy_id']:02d}/native-summary.xls)"
        lines.append(f"|{label}|{e['status_zh']}|{md_number(a.get('native_return_pct'))}|{md_number(a.get('native_net_profit'),2)}|{md_number(a.get('native_fees'),2)}|{md_number(a.get('native_annualized_linear_pct'))}|{md_number(a.get('native_max_drawdown_pct'))}|")
    lines.extend(["", "策略8已有汇总及1126条流水，显示成交价已核对，605488三个原生布尔边界仍待界面输出；它在‘已保存’12项内，也在剩余工作内。策略21七股128条日期/显示价格/期末专项核验已完成，初值及舍入差异保留。`pending_supported_strategy_ids`只描述已有哈希准入源码却缺原生文件的窄范围，全部剩余工作以`remaining_work_strategy_ids`为准。", ""])
    if data.get("portfolio_research_preparation"):
        p=data["portfolio_research_preparation"]
        lines.extend(["## 组合数据准备进度", "",f"S14七股150个技术候选日中，A‘实际生效且审计完整区间’覆盖{p['A_verified_interval_candidates']}项；B‘取得的已公开来源范围内最新快照’研究涉及{p['B_scoped_research_candidates']}项。两种语义未替用户选择；B不代表全市场公告目录完整，也不是已执行组合订单。[逐候选来源与范围]({p['path']})。", "", "|股票|技术候选日|A已验证区间|B已公开来源范围研究|","|---|---:|---:|---:|"])
        for item in p["per_security"]:lines.append(f"|{item['symbol']}|{item['technical_candidates']}|{item['A_verified_interval_candidates']}|{item['B_scoped_research_candidates']}|")
        lines.extend(["", "宁德时代2025-05-30保留当时已公开A股4403394911股与已完成H股155915300股的组件值，合计4559310211股；6月公布的5月末A股多1股版本没有回填。2024-023的拟注销数也未当作当时已完成股本。[五事实与5月原件独审](portfolio14-20/share-events-300750-independent-review/independent-five-fact-audit.json)。组合仍未产出订单或收益，原生高精度输入和公开CSI300基准准备不等于正式组合评测已完成。", ""])
    test=data.get("project_test_checkpoint")
    if test:lines.extend(["## 当前测试与会话快照", "",f"主执行检查点记录项目全套{test.get('tests')}项测试通过；分批执行器另有{test.get('runner_and_permission_test_count')}项回归，状态机{test.get('state_test_count')}项测试包含在项目全套内。[执行器与真实输入准备证据](native-evaluation/scale-in/real-input/validation/evidence.json)。这些测试和真实输入预检均不冒充pending策略的市场收益。", ""])
    portfolio_entries=[e for e in entries if e['strategy_id'] in (14,20) and e.get('implementation_preflight')]
    if portfolio_entries:
        p=portfolio_entries[0]['implementation_preflight']
        lines.extend([f"组合实际输入CLI另通过{p['adapter_test_count']}项风险测试。S14/S20各自已完成七股10443根、评测区间9387根的只读预检，1648个市场日与297个已预告周末独立绑定；18774个未知开/收阶段仅为库存盘点，实际撮合才逐委托核对数量、方向、配置与价格。两份pending配置的正式模拟入口均正确阻断，0订单、0成交、收益null。共享初始100万元仍是待答复提议。[组合入口与证据](portfolio14-20/native-runner/README.md)。", ""])
    lines.extend(["", "## 各策略七股明细", ""])
    for e in sorted(saved+accepted,key=lambda e:e["strategy_id"]):
        sid = e["strategy_id"]
        source = e["native_run_source"]
        provenance = e["ledger_provenance"]
        if e["status"]=="user_accepted_with_incomplete_ledger":
            ledger_note="用户接受按本批收尾。以下是有实际源哈希绑定的七股原生汇总；只保留6股1304条流水，明确缺688322的172条，全流水审计未完成。没有生成全股流水或补造缺失交易，不纳入完整流水差额表。"
            links=f"[原生汇总](native-results/S{sid:02d}/native-summary.xls)、[六股部分流水清单](native-results/S{sid:02d}/native-trades-partial.manifest.json)。"
        else:
            ledger_note = f"全股流水文件为7份原生逐股导出的表头与body原字节拼接，非一次综合窗口原生导出；收益、费用和交易字段未重算。[合并来源与逐段哈希]({provenance['derivation_manifest_path']})。" if not provenance["is_direct_combined_window_native_export"] else "全股流水为原生综合视图导出。"
            links=f"[原生汇总](native-results/S{sid:02d}/native-summary.xls)、[全股逐笔](native-results/S{sid:02d}/native-trades-all.xls)、[逐笔审计](native-audit/S{sid:02d}-audit.json)。"
        lines.extend([f"### 策略{sid}：{e['name']}", "",
            links, "",
            f"实际运行源：[{source.get('source_kind','已记录源')}]({source['source_path']})，SHA256 `{source['source_sha256']}`，来源日志第{source['source_log_line']}行。", "",
            ledger_note, "",
            "|股票|原生收益率%|原生净利润(元)|原生费用(元)|原生总次数|原生回撤比%|",
            "|---|---:|---:|---:|---:|---:|"])
        if e.get("calendar_signal_audit"):
            c=e["calendar_signal_audit"]
            lines.insert(len(lines)-2, f"信号日期已按固定市场日历核对：{c['ordinary_signal_count']}条普通信号、{c['native_roundtrip_count']}次往返；锚点日期、紧接次月卖点配对及公告时序均0项不符，强制平盘{c['forced_end_close_count']}条单列。[完整日历核对]({c['path']})。仅验证信号日期，未替代成交价核验。\n")
        extra=e["additional_native_validation"]
        if sid == 8 and extra.get("adjusted_execution_prices_verified"):
            lines.insert(len(lines)-2,f"S8额外核验：七股{extra['validated_native_trade_price_rows']}条原生流水显示成交价，均与同次本机前复权高精度输入的约定成交时点一致。完整信号验证仍待605488在2022-05-30、2022-06-09、2022-09-02三个边界日的原生布尔输出；浮点候选模型没有替代该确认。较早688322的124条独立日期核对另行保留。[七股专项证据目录](native-evaluation/additional-6-8/)。\n")
        elif sid == 8 and extra.get("independent_signal_date_subset"):
            lines.insert(len(lines)-2,"S8额外核验单列：688322的124条原生行与独立参考对照已保存；全七股额外核验尚未通过当前来源检查，见机器状态中的待核原因。\n")
        if sid == 21 and extra.get("signal_dates_verified") and extra.get("terminal_lifecycle_verified"):
            lines.insert(len(lines)-2,f"S21额外核验：七股共{extra['native_input_rows']}根真实本机前复权高精度输入已逐值比较，全部{extra['validated_native_trade_rows']}条原生流水的信号日期、约定次开成交显示价及期末生命周期一致；本批无期末强平行。七股首个完整20根STDP窗口原生值均为0；300750于2023-10-19的FORCAST原生-18.1649951935、独立-18.1650011008，差约5.91e-6，跨过两位舍入边界但当日无买卖信号。全部高精度差异保留，未调epsilon，也不宣称逐bit相等。独立参考完成70项未来扰动/截断比较；这不证明客户端在其他前复权版本下历史重算不变。[完整七股核验与差异](native-evaluation/S21-precision-v2/README.md)。\n")
        elif sid == 21 and extra.get("independent_signal_date_subset"):
            lines.insert(len(lines)-2,"S21较早688322的12个真实日期核对保留；全七股高精度核验当前未通过来源完整性检查，见机器状态中的待核原因。\n")
        for s in e["per_security"]:
            lines.append(f"|{s['code']} {s['name']}|{md_number(s['native_return_pct'])}|{md_number(s['native_net_profit'],2)}|{md_number(s['native_fees'],2)}|{s['total_times']}|{md_number(s['native_max_drawdown_pct'])}|")
    lines.extend(["", "## 逐笔与汇总显示差额", "",
        "以下只核对导出显示值。逐笔费用、汇总费用、毛收益和可用资金存在显示精度差额时不添加修正交易；差额不能被当作已证明底层账务逐分一致。", "",
        "|策略|逐笔行数|配对往返|原生总次数|逐笔费用减原生综合费用(元)|7股费用合计减综合费用(元)|7股净利润合计减综合净利润(元)|期末平盘行|",
        "|---|---:|---:|---:|---:|---:|---:|---:|"])
    for e in saved:
        a=e["audit"]
        lines.append(f"|{e['strategy_id']}|{a['ledger_rows']}|{a['total_completed_roundtrips']}|{a['aggregate_total_times']}|{md_number(a['ledger_minus_aggregate_fee'])}|{md_number(a['stock_display_fee_sum_minus_native_aggregate'])}|{md_number(a['stock_display_net_sum_minus_native_aggregate'])}|{a['forced_end_close_rows']}|")
    lines.extend(["", "## 数据覆盖和未运行项", "",
        "当前自选股为688017、688322、605488、603728、300750、601899、300308。下载后旧日期未被删、重叠OHLC未变，但部分成交额、成交量和保留字段被更新；新旧副本及差异均已保存。[下载后输入审计](after-download/下载后输入审计.md)与[输入清单](after-download/input-manifest.json)记录实际输入哈希。", "",
        "|股票|下载后首日|记录数|2021年前记录|第200根日期|",
        "|---|---|---:|---:|---|"])
    for r in post_audit["securities"]:
        lines.append(f"|{r['security_code']}|{r['first_stored_date']}|{r['record_count']}|{r['records_before_2021']}|{r['first_date_with_200_complete_stored_records']}|")
    lines.extend(["", "688017、605488、688322的首日与官方上市日一致；上市前不补造行情。200根仅是可用性指标，不是所有策略共同附加的过滤。实际指标初始化仍按本机版规则及原生输出核对。", "",
        "用户选择本机版的授权已经生效。下列策略仍有具体实现、执行或原生核验工作；不表示软件永久不支持。共同设置不等待重复一般授权，既有具体参数问题和手动登录仍分别待完成。不能删去分批、共享组合或核心信号规则，任取尚未确定的参数来代替原策略。", ""])
    for e in entries:
        if e["status"] == "native_capability_not_available":
            lines.append(f"- 策略{e['strategy_id']} {e['name']}：{e['not_run_reason']} 收益N/A，未纳入排名。")
        elif e["status"] == "in_progress":
            detail=f"- 策略{e['strategy_id']}进行中：{e.get('error',e.get('pending_reason'))}"
            if e.get("implementation_preflight"):
                p=e["implementation_preflight"]
                if e['strategy_id'] in (14,20):
                    detail+=f" [真实输入预检]({p['path']})：{p['native_rows']}根，未知许可阶段库存{p['permission_unknown_inventory_count']}条；0委托、0成交，收益为空。[共享执行器说明](portfolio14-20/native-runner/README.md)。"
                else:
                    detail+=f" [真实输入预检]({p['path']})：{p['native_rows']}根，许可缺口库存{p['permission_gap_rows']}条；0委托、0成交，收益为空。[执行器与状态说明](native-evaluation/scale-in/real-input/README.md)。"
            lines.append(detail)
        elif e["status"] == "user_accepted_with_incomplete_ledger":
            lines.append("- 策略17由用户接受本批收尾；原生汇总可展示，但6股1304条不等于七股1476条，全流水审计明确未完成。")
    lines.extend(["", "本报告保存用户批准的本机变体，不能称为全部原文规则的完全复现。当前自选股回看历史存在样本选择/幸存者偏差，历史信号的重绘或删失偏差仍须单独评估。信号模型时序测试和客户端执行价格核验是不同证据，不能互相替代；实际交易许可、一字板可成交性及真实市场整手约束不能仅由OHLCV推断。", "",
        "完整配置与来源哈希见[run-manifest.json](run-manifest.json)，19项机器可读状态及七股原生指标见[strategy-status.json](strategy-status.json)。答复前的[准备快照](preparation-manifest.json)保持原样。", ""])
    return "\n".join(lines)

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-root",type=Path,default=BASE)
    parser.add_argument("--refresh-audit",action="store_true")
    parser.add_argument("--final",action="store_true",help="Requires all supported native summaries, ledgers and fresh audits.")
    args=parser.parse_args(argv)
    root=args.report_root.resolve()
    previous_manifest=root/"run-manifest.json"
    if previous_manifest.is_file():
        blob=previous_manifest.read_bytes()
        archive=root/"report-history/run-manifest-by-sha256"/(hashlib.sha256(blob).hexdigest()+".json")
        archive.parent.mkdir(parents=True,exist_ok=True)
        if archive.exists():
            if archive.read_bytes()!=blob:raise ValueError("历史run-manifest哈希归档内容冲突。")
        else:
            archive.write_bytes(blob)
    refresh_result=None
    if args.refresh_audit:
        result=subprocess.run([sys.executable,"-X","utf8",str(root/"audit_native_exports.py"),"--report-root",str(root)],capture_output=True,text=True,encoding="utf-8")
        refresh_result={"command":"python -X utf8 audit_native_exports.py","exit_code":result.returncode,"stdout_sha256":hashlib.sha256(result.stdout.encode()).hexdigest(),"stderr":result.stderr or None}
        if result.returncode == 0 and (root/"native-results/S19/native-trades-all.xls").is_file() and (root/"audit_strategy19_dates.py").is_file():
            dates=subprocess.run([sys.executable,"-X","utf8",str(root/"audit_strategy19_dates.py")],capture_output=True,text=True,encoding="utf-8")
            refresh_result["S19_calendar_audit_exit_code"]=dates.returncode
            if dates.returncode:
                refresh_result["exit_code"]=dates.returncode
                refresh_result["S19_calendar_audit_stderr"]=dates.stderr
    plan=read_json(root/"native-evaluation/manifest.json")
    authorization=read_json(root/"authorization.json")
    session_path=root/"native-session-status.json"
    session=read_json(session_path) if session_path.is_file() else {}
    inp=read_json(root/"after-download/input-manifest.json")
    post=read_json(root/"after-download/input-audit.json")
    expected={r["security_code"] for r in inp["securities"]}
    run_records=read_run_source_log(root)
    supported=current_native_candidates(root,plan,run_records)
    implementation_pending=sorted(set(range(3,22))-supported)
    reader=load_auditor(root)
    entries=[read_strategy(root,sid,expected,supported,plan,reader,run_records) for sid in range(3,22)]
    accounting=completion_accounting(entries,supported,implementation_pending)
    for e in entries:
        if e["strategy_id"]==8 and e["status"]=="native_files_saved" and 8 in accounting["remaining_work_strategy_ids"]:
            e["status_zh"]="已保存，信号边界待验"
    saved=[e for e in entries if e["status"]=="native_files_saved"]
    accepted=[e for e in entries if e["status"]=="user_accepted_with_incomplete_ledger"]
    incomplete=[e["strategy_id"] for e in entries if e["strategy_id"] in supported and e["status"] not in ("native_files_saved","user_accepted_with_incomplete_ledger")]
    errors=[{"strategy_id":e["strategy_id"],"error":e["error"]} for e in entries if "error" in e]
    if args.final and (accounting["remaining_work_strategy_ids"] or errors or session.get("technical_blocked") or (refresh_result and refresh_result["exit_code"])):
        raise SystemExit("Cannot generate final: incomplete/stale/error native results: "+json.dumps({"remaining_work":accounting["remaining_work_strategy_ids"],"incomplete":incomplete,"errors":errors},ensure_ascii=True))
    now=datetime.now().astimezone().isoformat(timespec="seconds")
    data={"schema_version":1,"kind":"native_export_result_status_not_local_backtest","status":"final" if args.final else "in_progress","created_at_local":now,"expected_strategy_count":19,"native_files_saved_count":len(saved),"native_capability_not_available_count":0,"pending_supported_strategy_ids":incomplete,"ranking_performed":False,"money_policy":"Decimal from native exports, serialized as exact strings; missing/not-run results are null, never zero-filled.","native_summary_scope":"Seven independent 1000000 CNY accounts; 7000000 total base per strategy; not a shared portfolio.","strategies":entries,"errors":errors}
    data.update(user_accepted_with_incomplete_ledger_count=len(accepted),user_accepted_strategy_ids=[e["strategy_id"] for e in accepted],implementation_and_validation_pending_strategy_ids=implementation_pending,original_request_still_open=bool(incomplete or implementation_pending),native_candidate_strategy_ids=sorted(supported),additional_numeric_validation_pending_strategy_ids=[e['strategy_id'] for e in saved if not e['additional_native_validation']['indicator_numeric_values_verified']])
    data.update(accounting)
    data["validated_native_signal_price_terminal_strategy_ids"]=[e["strategy_id"] for e in saved if all(e["additional_native_validation"].get(k) for k in ("signal_dates_verified","adjusted_execution_prices_verified","terminal_lifecycle_verified"))]
    data["execution_price_verified_strategy_ids"]=[e["strategy_id"] for e in saved if e["additional_native_validation"].get("adjusted_execution_prices_verified")]
    data["validation_status_by_strategy"]={str(e["strategy_id"]):e["additional_native_validation"]["status"] for e in entries}
    login=authorization.get("login_completion_and_resume_addendum",{})
    data["client_session_state"]={"client_login_completed":bool(login.get("client_login_completed")),"awaiting_login":bool(login.get("awaiting_login",False)),"pending_force_termination_approval":False,"recovery_status":"native_exports_finalized" if args.final else "resumed_native_evaluation_in_progress","source":"authorization.json#login_completion_and_resume_addendum"}
    if session:
        data["client_session_state"].update(technical_blocked=bool(session.get("technical_blocked")),manual_recovery_completed=bool(session.get("manual_recovery_completed")),awaiting_user_manual_recovery=bool(session.get("awaiting_user_manual_recovery")),awaiting_login=bool(session.get("awaiting_login",False)),recovery_status="native_exports_finalized" if args.final else session.get("status"),source="native-session-status.json")
        data["client_session_state"].update(awaiting_user_cancel_decision=bool(session.get("awaiting_user_cancel_decision")),S17_user_accepted_with_incomplete_ledger=bool(session.get("S17_user_accepted_with_incomplete_ledger")))
    checkpoint_path=root/"resume-eight-checkpoint-20261025.json"
    checkpoint=read_json(checkpoint_path) if checkpoint_path.is_file() else {}
    if checkpoint:
        ui=checkpoint.get("ui",{})
        if checkpoint.get("kind")!="work_checkpoint_not_final_results":raise ValueError("当前会话检查点类型不符。")
        waiting=ui.get("state")=="login_page_after_authorized_restart"
        data["client_session_state"].update(source=checkpoint_path.name,source_sha256=sha(checkpoint_path),historical_session_source="native-session-status.json",historical_session_is_not_current_UI=True,current_ui_state=ui.get("state"),current_window_id=ui.get("window_id"),client_login_completed=not waiting,awaiting_login=waiting,recovery_status=ui.get("state"),general_authorization_missing=bool(ui.get("general_authorization_missing")),current_pending_user_decisions=checkpoint.get("pending_user_decisions",[]))
        try:data["project_test_checkpoint"]=verified_checkpoint_tests(root,checkpoint)
        except (ValueError,KeyError,OSError,UnicodeError,StopIteration) as exc:data["project_test_evidence_error"]=str(exc)
    try:data["portfolio_research_preparation"]=portfolio_research_progress(root)
    except (ValueError,KeyError,OSError,UnicodeError) as exc:data["portfolio_research_preparation_error"]=str(exc)
    status_path=root/"strategy-status.json"
    report_path=root/"评测结果.md"
    sources=set()
    for relative in ("preparation-manifest.json","authorization.json","after-download/input-manifest.json","after-download/input-audit.json","after-download/before-after-differences.json","calendar.json","strategy19-calendar.json","native-evaluation/manifest.json","native-evaluation/B321NAT-expert.txt","native-evaluation/confirmed-rules.md","native-evaluation/official-semantics.md","native-evaluation/signal-causality-evidence.json","native-price-scope-review.md","native-audit/index.json","audit_native_exports.py","build_native_report.py"):
        path=root/relative
        if path.is_file(): sources.add(path)
    for r in inp["securities"]:
        sources.add(root/"after-download"/r["snapshot_path"])
    for relative in ("evidence-limitations.json","界面证据限制.md","native-export-provenance.jsonl"):
        if (root/relative).is_file():
            sources.add(root/relative)
    for relative in ("audit_strategy19_dates.py","native-audit/S19-calendar-audit.json","native-audit/S19-calendar-audit.md"):
        if (root/relative).is_file():
            sources.add(root/relative)
    for relative in ("native-session-status.json","native-recovery-events.jsonl","原生操作与恢复记录.md","recovery-history/native-operation-history-before-S17-save-block-20261024.md","audit_strategy17_partial.py","native-results/S17/native-trades-partial.manifest.json","native-results/S17/流水未完成说明.md"):
        if (root/relative).is_file():
            sources.add(root/relative)
    if checkpoint_path.is_file():sources.add(checkpoint_path)
    partial_path=root/"native-results/S17/native-trades-partial.manifest.json"
    if partial_path.is_file():
        sources.update(root/r["path"] for r in read_json(partial_path)["files"])
    if (root/"native-run-source-log.jsonl").is_file():
        sources.add(root/"native-run-source-log.jsonl")
    for record in run_records:
        for field in ("source_path", "summary_snapshot_path", "trades_snapshot_path"):
            if record.get(field):
                path=(root/record[field]).resolve()
                if path.is_relative_to(root) and path.is_file():
                    sources.add(path)
    for e in saved:
        sources.update(root/r["path"] for r in e["source_files"])
        sources.add(root/e["audit"]["path"])
        provenance=e["ledger_provenance"]
        if provenance.get("derivation_manifest_path"):
            sources.add(root/provenance["derivation_manifest_path"])
            sources.update(root/r["path"] for r in provenance["source_files"])
    for e in accepted:
        sources.update(root/r["path"] for r in e["source_files"])
    for e in entries:
        for record in e.get("implementation_preflight",{}).get("source_files",[]):
            verified_reference_file(root,record["path"],record["sha256"])
            sources.add(root/record["path"])
    if any(e.get("implementation_preflight") for e in entries):sources.add(root/"native-evaluation/scale-in/real-input/README.md")
    if any(e['strategy_id'] in (14,20) and e.get('implementation_preflight') for e in entries):sources.add(root/'portfolio14-20/native-runner/README.md')
    for key in ("project_test_checkpoint","portfolio_research_preparation"):
        for record in data.get(key,{}).get("evidence_sources",[]):
            verified_reference_file(root,record["path"],record["sha256"])
            sources.add(root/record["path"])
    for relative in ("portfolio14-20/share-events-300750-independent-review/independent-five-fact-audit.json","portfolio14-20/share-events-300750-independent-review/may-source-manifest.json","portfolio14-20/share-events-300750-independent-review/audit_root_five_facts.py","portfolio14-20/real-input-preflight/preflight.json"):
        if (root/relative).is_file():sources.add(root/relative)
    for e in saved:
        for key in ("source_rule_manifest", "evidence_path"):
            value=e["additional_native_validation"].get(key)
            if value and (root/value).is_file(): sources.add(root/value)
        for reference in e["additional_native_validation"].get("evidence_sources",[]):
            # The validator checked this graph. Recheck before manifest publication.
            verified_reference_file(root,reference["path"],reference["sha256"])
            sources.add(root/reference["path"])
    if any(e["strategy_id"]==21 and e["additional_native_validation"].get("signal_dates_verified") for e in saved):
        sources.add(root/"native-evaluation/S21-precision-v2/README.md")
    sources.update(f for f in (root/"ui-evidence").glob("*") if f.is_file())
    not_run=[e["strategy_id"] for e in entries if not any(e["native_export_presence"].values())]
    # Validate the optional graph before publishing a promoted status/report.
    write_json(status_path,data)
    report_path.write_text(markdown(data,post),encoding="utf-8")
    manifest={"schema_version":1,"kind":"native_evaluation_exports_run_manifest","status":data["status"],"created_at_local":now,"client_configuration":configuration(),"configuration_evidence":"主执行代理确认本批公共配置；S03完整设置报告及各SID参数截图已保存，证据逐项哈希列在sources。","formula_native_execution_identity_verified":False,"execution_price_ui_comparison_completed":False,"local_backtest_performed_by_report_builder":False,"native_export_encoding":"GB18030 TSV despite .xls extension","supported_strategy_ids":sorted(supported),"not_run_strategy_ids":not_run,"saved_strategy_ids":[e["strategy_id"] for e in saved],"pending_strategy_ids":incomplete,"expected_security_codes":sorted(expected),"configuration_version_policy":"User-confirmed local variant; per-entry departures and actual source hashes are preserved. The initial simple-strategy manifest is an immutable snapshot, not a live exclusion list.","native_metrics_policy":{"annualized":"Native linear annualization, not CAGR","drawdown":"Native reported metric, not reconstructed portfolio daily equity drawdown","total_times":"Keep native field separate from ledger rows and observed paired roundtrips","precision":"Native display values preserved; Decimal differences reported without corrective entries"},"audit_refresh":refresh_result,"sources":[file_record(root,p) for p in sorted(sources)],"outputs":[file_record(root,p) for p in (status_path,report_path)],"python":platform.python_version(),"limits":["Execution prices use a front-adjusted native basis; comparisons to raw unadjusted snapshots are candidates only.","No real order permission, limit-price fill permission, volume unit interpretation or corporate-action correctness inferred from OHLCV.","Unfinished strategies retain null returns; newly completed native files are not permanently excluded by the original eight-SID snapshot.","The preparation manifest remains an unchanged historical snapshot."]}
    manifest["formula_source_policy"]="Per-SID actual run-source log binds immutable source SHA256 to native export SHA256; current same-name formula text never changes prior run attribution."
    manifest["actual_formula_source_by_saved_strategy"]={str(e["strategy_id"]):e["native_run_source"] for e in saved}
    manifest["user_accepted_with_incomplete_ledger_strategy_ids"]=[e["strategy_id"] for e in accepted]
    manifest["actual_formula_source_by_user_accepted_strategy"]={str(e["strategy_id"]):e["native_run_source"] for e in accepted}
    manifest["original_request_still_open"]=data["original_request_still_open"]
    manifest["work_completion_accounting"]=accounting
    manifest["remaining_work_strategy_ids"]=accounting["remaining_work_strategy_ids"]
    manifest["accepted_or_saved_strategy_count"]=accounting["accepted_or_saved_strategy_count"]
    manifest["portfolio_research_preparation"]=data.get("portfolio_research_preparation")
    manifest["project_test_checkpoint"]=data.get("project_test_checkpoint")
    manifest["implementation_and_validation_pending_strategy_ids"]=implementation_pending
    manifest["additional_numeric_validation_pending_strategy_ids"]=data["additional_numeric_validation_pending_strategy_ids"]
    for key in ("validated_native_signal_price_terminal_strategy_ids","execution_price_verified_strategy_ids","validation_status_by_strategy"):
        manifest[key]=data[key]
    manifest["extra_validation_scope"]={str(e["strategy_id"]):{
        k:v for k,v in e["additional_native_validation"].items()
        if k in ("status","scope","signal_dates_verified","terminal_lifecycle_verified","adjusted_execution_prices_verified","indicator_numeric_comparison_completed","indicator_values_exactly_equal","indicator_bitwise_identity_verified","execution_permission_verified","native_input_rows","validated_native_trade_rows","validated_native_trade_price_rows","full_signal_validation_status","numeric_exceptions","evidence_path","validation_evidence_error")}
        for e in saved if e["strategy_id"] in (8,21)}
    manifest["limits"][0]="Execution-price identity is verified only for strategy IDs listed in execution_price_verified_strategy_ids against hash-bound actual native FQ1 inputs at ledger display precision. Raw unadjusted comparisons remain candidates only; no adjustment-algorithm or execution-permission certification."
    manifest["run_source_log_record_count"]=len(run_records)
    manifest["ledger_provenance_by_saved_strategy"]={str(e["strategy_id"]):e["ledger_provenance"] for e in saved}
    manifest["user_manually_terminated_previous_client"]=True
    manifest["client_restart_explicitly_authorized"]=True
    manifest["pending_force_termination_approval"]=False
    manifest["client_session_state"]=data["client_session_state"]
    manifest["current_client_formula_state"]={"status":"needs_fresh_UI_observation_after_login","checkpoint":checkpoint_path.name} if checkpoint else session.get("current_client_formula")
    manifest["historical_client_formula_state"]=session.get("current_client_formula")
    manifest["recovery_plan_warning"]=session.get("recovery_plan_warning")
    manifest["tests_reported_by_root_this_recovery_turn"]=checkpoint.get("project_test") if checkpoint else session.get("tests_reported_by_root_this_turn")
    manifest["historical_recovery_tests"]=session.get("tests_reported_by_root_this_turn")
    manifest["not_run_authorization_relationship"]={"local_variant_user_authorized":True,"basis":"authorization.json#approved_rule_version.complex_strategy_policy","meaning":"Unfinished implementations or native checks remain in progress; completed additional adapters are admitted only through actual hash-bound source/export records. No permanent platform-incapability claim.","no_silent_substitution_of_core_rules":True,"unfinished_returns_remain_null":True}
    if (root/"evidence-limitations.json").is_file():
        limitations=read_json(root/"evidence-limitations.json")
        manifest["evidence_limitations"]={"path":"evidence-limitations.json","sha256":sha(root/"evidence-limitations.json"),"stale_capture_paths_not_accepted_as_named_step_evidence":[path for group in limitations["invalid_as_named_step_evidence"] for path in group["paths"]],"fresh_current_captures":limitations["fresh_current_captures"]}
    write_json(root/"run-manifest.json",manifest)
    print(json.dumps({"status":data["status"],"saved":[e["strategy_id"] for e in saved],"pending":incomplete,"implementation_pending":implementation_pending,"not_run":not_run,"errors":errors,"output_files":["评测结果.md","strategy-status.json","run-manifest.json"]},ensure_ascii=True))
    return 1 if errors else 0

if __name__=="__main__":
    sys.exit(main())
