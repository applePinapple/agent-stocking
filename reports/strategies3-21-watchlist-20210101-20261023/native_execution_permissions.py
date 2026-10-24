"""Explicit permission CSV contract shared by supplementary historical runners.

Missing means unknown. Quotations never populate permissions. An approved
historical execution scenario remains a scenario, not historical known facts.
"""
from dataclasses import dataclass
from datetime import date,datetime,time,timedelta,timezone
from decimal import Decimal,InvalidOperation,ROUND_HALF_UP
import csv
import hashlib
import io
import json
import re
from pathlib import Path
from types import MappingProxyType

COLUMNS=("date","symbol","phase","buy_allowed","sell_allowed","available_at","source","price_basis")
# Requested 2021--2026 Chinese exchange sessions use the explicit UTC+08:00
# project convention; do not depend on an optional system tzdata installation.
ZONE=timezone(timedelta(hours=8),"UTC+08:00")


def digest(blob):return hashlib.sha256(blob).hexdigest()


def explicit_decimal(value,name):
    """Preserve JSON financial decimals; never accept float/bool coercion."""
    if not isinstance(value,str) or not value.strip():
        raise ValueError(f'{name} must be an explicit decimal string, never a JSON float/bool')
    try:parsed=Decimal(value)
    except InvalidOperation as exc:raise ValueError(f'{name} is not a valid decimal string') from exc
    if not parsed.is_finite():raise ValueError(f'{name} must be finite')
    return parsed


def read_explicit_json(blob):
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise ValueError("duplicate explicit configuration/permission JSON field: "+key)
            result[key]=value
        return result
    return json.loads(blob.decode("utf-8-sig"),object_pairs_hook=unique)


def phase_time(day,phase):
    if phase not in {"open","close"}:raise ValueError("phase must be explicit open/close")
    return datetime.combine(day,time(9,30) if phase=="open" else time(15),ZONE)


def aware(text):
    value=datetime.fromisoformat(text)
    if value.tzinfo is None or value.utcoffset() is None:raise ValueError("aware available_at required")
    return value


def boolean(text):
    if text not in {"true","false","unknown"}:raise ValueError("permission must be true/false/unknown")
    return {"true":True,"false":False,"unknown":None}[text]


@dataclass(frozen=True)
class PermissionRecord:
    day:date
    symbol:str
    phase:str
    buy_allowed:bool|None
    sell_allowed:bool|None
    available_at:datetime
    source:str
    price_basis:str
    kind:str


@dataclass(frozen=True)
class NativeExecutionWitness:
    day:date
    symbol:str
    phase:str
    side:str
    quantity:int
    quantity_unit:str
    signal_date:date
    native_ledger_row_1based:int
    native_display_price:Decimal
    configuration_canonical_sha256:str
    native_model_id:str
    observed_strategy_id:int
    ledger_sha256:str
    formula_sha256:str
    input_sha256:str


@dataclass(frozen=True)
class PermissionSet:
    by_key:dict
    kind:str
    csv_path:Path
    csv_sha256:str
    metadata_path:Path
    metadata_sha256:str
    evidence:tuple
    observed_execution_witnesses:tuple=()
    native_configuration_canonical_sha256:str|None=None

    def __post_init__(self):
        object.__setattr__(self,"by_key",MappingProxyType(dict(self.by_key)))
        object.__setattr__(self,"evidence",tuple(MappingProxyType(dict(item)) for item in self.evidence))

    def get(self,day,symbol,phase):return self.by_key.get((day,symbol,phase))

    def authorize(self,day,symbol,phase,side,quantity,*,quantity_unit=None,native_configuration_canonical_sha256=None,execution_price=None):
        if side not in {"buy","sell"} or type(quantity) is not int or quantity<=0:
            raise ValueError("Execution authorization needs actual positive quantity and side")
        record=self.get(day,symbol,phase)
        if record is None:return None
        if self.kind!="native_model_observed_execution":
            return record.buy_allowed if side=="buy" else record.sell_allowed
        if native_configuration_canonical_sha256!=self.native_configuration_canonical_sha256:
            raise ValueError("Observed native execution configuration does not match requested runtime model")
        if not isinstance(execution_price,Decimal) or not execution_price.is_finite() or execution_price<=0:
            raise ValueError("Native observed witness needs an explicit execution price for display-precision scope")
        return True if any(w.day==day and w.symbol==symbol and w.phase==phase and w.side==side
            and w.quantity==quantity and w.quantity_unit==quantity_unit
            and execution_price.quantize(Decimal(".01"),rounding=ROUND_HALF_UP)==w.native_display_price
            for w in self.observed_execution_witnesses) else None

    def verify_immutable(self):
        if digest(self.csv_path.read_bytes())!=self.csv_sha256 or digest(self.metadata_path.read_bytes())!=self.metadata_sha256:
            raise ValueError("permission CSV/metadata changed during run")
        for item in self.evidence:
            if digest(Path(item["resolved_path"]).read_bytes())!=item["sha256"]:
                raise ValueError("permission original source evidence changed during run")


def canonical_hash(value):return digest(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode("utf-8"))


def native_execution_witnesses(meta,evidence,root,declared_rows):
    """Verify exact observed orders, never generalize a native fill into a mask."""
    model=meta["native_model"]
    if (model["kind"]!="tdx_program_trade_evaluation" or model["client_version"]!="V7.71"
            or not model["model_id"]):raise ValueError("Explicit supported native evaluation model/version required")
    def src(key):return evidence[model[key]]
    cfg_source=src("configuration_source_id");cfg=read_explicit_json(Path(cfg_source["resolved_path"]).read_bytes())["client_configuration"]
    cfg_hash=canonical_hash(cfg)
    if (cfg_hash!=model["configuration_canonical_sha256"] or cfg["client"]!="通达信金融终端V7.71"
            or cfg["system"]!="程序交易评测系统" or cfg["price_adjustment"]!="前复权"
            or cfg["entry_price"]!="次周期开盘价" or cfg["exit_price"]!="次周期开盘价"):
        raise ValueError("Observed native model configuration/source mismatch")
    ledger_source=src("ledger_source_id");formula_source=src("formula_source_id");summary_source=src("summary_source_id")
    log_source=src("run_source_log_id");ledger_sha=ledger_source["sha256"];formula_sha=formula_source["sha256"]
    sid=model["strategy_id"]
    if type(sid) is not int or not 3<=sid<=21:raise ValueError("Observed native strategy ID required")
    matched=[]
    for raw in Path(log_source["resolved_path"]).read_text(encoding="utf-8-sig").splitlines():
        if not raw.strip():continue
        r=read_explicit_json(raw.encode("utf-8"))
        def alias(keys):
            values=[r[k] for k in keys if r.get(k) is not None]
            if len({str(v) for v in values})>1:raise ValueError("Conflicting native source-log aliases")
            return values[0] if values else None
        if (alias(("sid","strategy_id"))==sid and alias(("source_sha256","formula_sha256"))==formula_sha
                and alias(("summary_sha256","native_summary_sha256"))==summary_source["sha256"]
                and alias(("trades_sha256","ledger_sha256","native_trades_all_sha256","native_ledger_sha256"))==ledger_sha):
            path=alias(("source_path","formula_path","source"))
            if path is None or (root/path).resolve()!=Path(formula_source["resolved_path"]):raise ValueError("Native formula source path does not match recorded run")
            if r.get("runtime_sid") is not None and r["runtime_sid"]!=sid:raise ValueError("Native runtime SID mismatch")
            matched.append(r)
    if not matched:raise ValueError("Observed native source, summary and ledger are not bound to an actual recorded run")
    ledger=list(csv.reader(io.StringIO(Path(ledger_source["resolved_path"]).read_bytes().decode("gb18030")),delimiter="\t"))
    expected=("品种代码","品种名称","时间","信号","价格(元)","交易量(股/手)")
    if tuple(ledger[0][:6])!=expected:raise ValueError("Native ledger schema mismatch")
    indicator=src("indicator_source_id")
    approved_indicator="f46b14646455e7b64ecd1cc283d8205d4d7819f8e66b93e3535c6344d849f17e"
    if indicator["sha256"]!=approved_indicator:raise ValueError("Approved native v2 indicator source required")
    provenance=src("input_provenance_source_id")
    provenance_rows=[read_explicit_json(line.encode("utf-8")) for line in Path(provenance["resolved_path"]).read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    witnesses=[];used=set();input_cache={}
    for item in meta["native_execution_observations"]:
        number=item["native_ledger_row_1based"]
        if type(number) is not int or not 2<=number<=len(ledger):raise ValueError("Exact native ledger row required")
        row=ledger[number-1]
        if len(row)<6:raise ValueError("Observed native ledger row is incomplete")
        symbol=item["symbol"];signal=date.fromisoformat(row[2].split()[0].replace("/","-"))
        quantity=item["quantity"];raw_qty=Decimal(row[5]);side={"买开":"buy","卖平":"sell","平盘":"sell"}.get(row[3])
        if (type(quantity) is not int or quantity<=0 or not raw_qty.is_finite() or raw_qty!=quantity
                or row[0]!=symbol or item["side"]!=side or item["quantity_unit"]!="native_ledger_quantity"):
            raise ValueError("Observed native stock/side/quantity/unit does not match the original ledger row")
        input_source=evidence[item["native_input_source_id"]];input_path=Path(input_source["resolved_path"])
        cache_key=(symbol,input_source["sha256"])
        if cache_key not in input_cache:
            blob=input_path.read_bytes();table=list(csv.reader(io.StringIO(blob.decode("gb18030")),delimiter="\t"))
            if not table or not table[0] or symbol not in table[0][0]:raise ValueError("Observed native date inventory security mismatch")
            header_index=next((i for i,r in enumerate(table) if "CHECKALL.FQ" in [s.strip() for s in r] and "CHECKALL.BC" in [s.strip() for s in r]),None)
            if header_index is None:raise ValueError("Observed native input lacks FQ/BC headers")
            header=[v.strip() for v in table[header_index]];fq=header.index("CHECKALL.FQ");bc=header.index("CHECKALL.BC")
            dates=[]
            for line in table[header_index+1:]:
                if not line or not re.fullmatch(r"\s*\d{4}/\d{2}/\d{2}\s*",line[0]):continue
                if len(line)<=max(fq,bc):raise ValueError("Observed native input date row incomplete")
                if Decimal(line[fq])!=1 or Decimal(line[bc])!=len(dates):raise ValueError("Observed native input FQ/BC continuity mismatch")
                dates.append(date.fromisoformat(line[0].strip().replace("/","-")))
            if not dates or dates!=sorted(set(dates)):raise ValueError("Observed native input date inventory incomplete/duplicate")
            bound=[r for r in provenance_rows if r.get("sha256")==input_source["sha256"] and r.get("code",r.get("security"))==symbol
                and r.get("source_sha256")==approved_indicator and r.get("scale")==1048576 and r.get("FQ")==1
                and (root/r["path"]).resolve()==input_path]
            if not bound:raise ValueError("Observed native date input lacks exact source/security provenance")
            input_cache[cache_key]=dates
        dates=input_cache[cache_key]
        if signal not in dates:raise ValueError("Native signal date missing from exact input inventory")
        if row[3]=="平盘":
            if cfg["force_end_close_included"] is not True or signal!=dates[-1]:raise ValueError("Observed terminal row/config/final date mismatch")
            phase="close";execution=signal
        else:
            i=dates.index(signal)
            if i+1>=len(dates):raise ValueError("Native next-open fill has no next observed security record")
            phase="open";execution=dates[i+1]
        key=(execution,symbol,phase)
        declared=declared_rows.get(key)
        if (item["execution_date"]!=str(execution) or item["phase"]!=phase or declared is None
                or declared["buy_allowed" if side=="buy" else "sell_allowed"]!="true"
                or declared["source"]!=model["ledger_source_id"]
                or not cfg["start"]<=str(signal)<=cfg["end"]):
            raise ValueError("Observed execution phase/date/CSV/source/config range does not match original native fill")
        unique=(number,input_source["sha256"])
        if unique in used:raise ValueError("Duplicate observed native ledger witness")
        used.add(unique)
        witnesses.append(NativeExecutionWitness(execution,symbol,phase,side,quantity,item["quantity_unit"],signal,number,Decimal(row[4]),cfg_hash,model["model_id"],sid,ledger_sha,formula_sha,input_source["sha256"]))
    for key,row in declared_rows.items():
        for side in ("buy","sell"):
            if row[side+"_allowed"]=="true" and not any((w.day,w.symbol,w.phase)==key and w.side==side for w in witnesses):
                raise ValueError("CSV true lacks a matching quantity-scoped native execution witness")
    return tuple(witnesses),cfg_hash


def load_permission_csv(path,metadata_path,*,allowed_symbols,price_basis,evidence_root):
    path=Path(path).resolve();metadata_path=Path(metadata_path).resolve()
    root=Path(evidence_root).resolve()
    blob=path.read_bytes();meta_blob=metadata_path.read_bytes();meta=read_explicit_json(meta_blob)
    kind=meta.get("kind")
    if meta.get("schema_version")!=1 or kind not in {"actual_historical_permission","user_approved_historical_execution_scenario","native_model_observed_execution"}:
        raise ValueError("explicit permission kind/schema required")
    required={"actual_historical_permission":"verified","user_approved_historical_execution_scenario":"user_confirmed","native_model_observed_execution":"verified_native_model_observation"}[kind]
    if meta.get("status")!=required:raise ValueError("permission evidence/scenario is still pending")
    if meta.get("csv_sha256")!=digest(blob) or meta.get("price_basis")!=price_basis:
        raise ValueError("permission CSV hash or price basis mismatch")
    symbols=set(meta["security_codes"])
    if not symbols or not symbols<=set(allowed_symbols):raise ValueError("permission metadata security scope mismatch")
    start,end=date.fromisoformat(meta["start"]),date.fromisoformat(meta["end"])
    if start>end:raise ValueError("permission metadata date range reversed")
    evidence={}
    for item in meta["source_evidence"]:
        p=(metadata_path.parent/item["path"]).resolve()
        if not p.is_relative_to(root) or item["id"] in evidence or digest(p.read_bytes())!=item["sha256"]:
            raise ValueError("permission source evidence missing/outside allowed root/hash mismatch")
        evidence[item["id"]]={**item,"resolved_path":str(p)}
    if not evidence:raise ValueError("permission source evidence required")
    approval=None
    if kind=="user_approved_historical_execution_scenario":
        approval=aware(meta["model_approved_at"])
        if meta["approval_source_id"] not in evidence:raise ValueError("scenario needs bound user approval evidence")
    reader=csv.DictReader(io.StringIO(blob.decode("utf-8-sig"),newline=""))
    if tuple(reader.fieldnames or ())!=COLUMNS:raise ValueError("permission CSV columns do not match shared contract")
    records={};declared_rows={}
    for number,row in enumerate(reader,2):
        if None in row or any(v is None for v in row.values()):raise ValueError("incomplete or extra permission CSV columns")
        day=date.fromisoformat(row["date"]);phase=row["phase"];symbol=row["symbol"]
        when=phase_time(day,phase);available=aware(row["available_at"])
        if not start<=day<=end or symbol not in symbols or row["price_basis"]!=price_basis:
            raise ValueError("permission row outside declared security/date/price scope")
        if row["source"] not in evidence:raise ValueError("permission row lacks bound evidence source")
        if kind=="actual_historical_permission" and available>when:
            raise ValueError("historical permission was not available by execution time")
        if approval is not None and available!=approval:
            raise ValueError("scenario row must retain its actual model approval time, never a backdated historical publication")
        key=(day,symbol,phase)
        if key in records:raise ValueError("duplicate permission row")
        buy,sell=boolean(row["buy_allowed"]),boolean(row["sell_allowed"])
        if kind=="native_model_observed_execution":
            if buy is False or sell is False or available!=aware(meta["observed_at"]):
                raise ValueError("Native observed execution cannot infer denials or backdate its observation time")
            declared_rows[key]=row
            # Generic consumers see unknown until quantity/config-scoped authorize().
            buy=sell=None
        records[key]=PermissionRecord(day,symbol,phase,buy,sell,available,row["source"],price_basis,kind)
    if path.read_bytes()!=blob or metadata_path.read_bytes()!=meta_blob:raise ValueError("permission input changed while reading")
    witnesses=();cfg_hash=None
    if kind=="native_model_observed_execution":witnesses,cfg_hash=native_execution_witnesses(meta,evidence,root,declared_rows)
    result=PermissionSet(records,kind,path,digest(blob),metadata_path,digest(meta_blob),tuple(evidence.values()),witnesses,cfg_hash)
    result.verify_immutable()
    return result
