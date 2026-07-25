"""Fail-closed contract for candidate_document_interpretation.v1."""
from __future__ import annotations
from datetime import date
import json
import math
import re
from typing import Any

DOCUMENT_INTERPRETATION_SCHEMA_VERSION = "candidate_document_interpretation.v1"
MAX_RESPONSE_BYTES, MAX_DEPTH, MAX_NODES, MAX_ARRAY, MAX_STRING = 32_000, 32, 2_048, 256, 512
_ROOT = {"schema_version","status","document_type","fields","relations","evidence","confidence","interpreter","model","prompt_version","policy_version","blocked_reason"}
_STATUSES = {"success","needs_review","blocked"}
_DOCUMENT_TYPES = {"invoice","project","contract","bid","tender","other"}
_FIELD_TYPES = {
 "invoice_number":str,"invoice_date":str,"amount":(int,float),"tax_amount":(int,float),"total_amount":(int,float),
 "buyer_name":str,"buyer_tax_id":str,"seller_name":str,"seller_tax_id":str,
 "project_code":str,"project_name":str,"contract_code":str,"contract_name":str,
}
_RELATION_TYPES = {"invoice_contract","invoice_project","contract_project"}
_EVIDENCE_FIELDS = {"buyer.tax_id","seller.tax_id","buyer.name","seller.name","contract_code","project_code","amount","date"}
_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_TAX_ID = re.compile(r"^[A-Za-z0-9]{8,32}$")

class DocumentInterpretationSchemaError(ValueError):
    """Public fail-closed schema error."""

def parse_candidate_document_interpretation(payload: object) -> dict[str, Any]:
    try:
        value = _decode(payload)
        _tree(value)
        if type(value) is not dict: raise DocumentInterpretationSchemaError("root")
        _only(value, _ROOT); _required(value, _ROOT-{"blocked_reason"})
        if value["schema_version"] != DOCUMENT_INTERPRETATION_SCHEMA_VERSION: raise DocumentInterpretationSchemaError("version")
        if type(value["status"]) is not str or value["status"] not in _STATUSES: raise DocumentInterpretationSchemaError("status")
        if type(value["document_type"]) is not str or value["document_type"] not in _DOCUMENT_TYPES: raise DocumentInterpretationSchemaError("document type")
        _fields(value["fields"]); _relations(value["relations"]); _evidence(value["evidence"])
        confidence=value["confidence"]
        if type(confidence) not in (int,float) or not math.isfinite(confidence) or not 0 <= confidence <= 1: raise DocumentInterpretationSchemaError("confidence")
        for key in ("interpreter","model","prompt_version","policy_version"):
            if not _text(value[key]): raise DocumentInterpretationSchemaError(key)
        if value["status"] == "blocked":
            if not _text(value.get("blocked_reason")): raise DocumentInterpretationSchemaError("blocked")
        elif "blocked_reason" in value: raise DocumentInterpretationSchemaError("blocked reason")
        return value
    except DocumentInterpretationSchemaError: raise
    except Exception as error: raise DocumentInterpretationSchemaError("invalid interpretation") from None

def _decode(payload: object) -> object:
    if type(payload) is str:
        if len(payload.encode("utf-8")) > MAX_RESPONSE_BYTES: raise DocumentInterpretationSchemaError("size")
        try: return json.loads(payload, object_pairs_hook=_pairs, parse_constant=lambda _: (_ for _ in ()).throw(DocumentInterpretationSchemaError("nonfinite")))
        except DocumentInterpretationSchemaError: raise
        except Exception: raise DocumentInterpretationSchemaError("json") from None
    return payload

def _pairs(pairs: list[tuple[str,object]]) -> dict[str,object]:
    out={}
    for key,value in pairs:
        if key in out: raise DocumentInterpretationSchemaError("duplicate")
        out[key]=value
    return out

def _tree(root: object) -> None:
    stack=[(root,1)]; nodes=0; bytes_=0
    while stack:
        value,depth=stack.pop(); nodes+=1
        if nodes > MAX_NODES or depth > MAX_DEPTH: raise DocumentInterpretationSchemaError("tree limit")
        if type(value) is str:
            if len(value)>MAX_STRING: raise DocumentInterpretationSchemaError("string limit")
            bytes_+=len(value.encode("utf-8"))
        elif type(value) in (int,bool) or value is None: pass
        elif type(value) is float:
            if not math.isfinite(value): raise DocumentInterpretationSchemaError("nonfinite")
        elif type(value) is list:
            if len(value)>MAX_ARRAY: raise DocumentInterpretationSchemaError("array limit")
            stack.extend((child,depth+1) for child in value)
        elif type(value) is dict:
            if len(value)>MAX_ARRAY: raise DocumentInterpretationSchemaError("object limit")
            for key,child in value.items():
                if type(key) is not str: raise DocumentInterpretationSchemaError("key")
                stack.append((key,depth+1)); stack.append((child,depth+1))
        else: raise DocumentInterpretationSchemaError("non-json type")
        if bytes_ > MAX_RESPONSE_BYTES: raise DocumentInterpretationSchemaError("size")

def _only(value: dict[str,Any], allowed:set[str]) -> None:
    if set(value)-allowed: raise DocumentInterpretationSchemaError("unknown field")
def _required(value:dict[str,Any], required:set[str]) -> None:
    if not required <= set(value): raise DocumentInterpretationSchemaError("missing field")
def _text(value:object) -> bool:
    return type(value) is str and 0 < len(value) <= MAX_STRING and value == value.strip()
def _id(value:object) -> bool: return _text(value) and bool(_ID.fullmatch(value))

def _fields(value:object) -> None:
    if type(value) is not dict: raise DocumentInterpretationSchemaError("fields")
    _only(value,set(_FIELD_TYPES))
    for key,item in value.items():
        allowed=_FIELD_TYPES[key]
        if key == "invoice_date":
            if not _iso_date(item): raise DocumentInterpretationSchemaError("field")
        elif key in {"buyer_tax_id", "seller_tax_id"}:
            if not _text(item) or not _TAX_ID.fullmatch(item): raise DocumentInterpretationSchemaError("field")
        elif allowed is str:
            if not _text(item): raise DocumentInterpretationSchemaError("field")
        elif type(item) not in allowed or not math.isfinite(item): raise DocumentInterpretationSchemaError("field")

def _iso_date(value:object) -> bool:
    if type(value) is not str or len(value) != 10 or value != value.strip():
        return False
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return False
    return parsed.isoformat() == value

def _relations(value:object) -> None:
    if type(value) is not list: raise DocumentInterpretationSchemaError("relations")
    for item in value:
        if type(item) is not dict: raise DocumentInterpretationSchemaError("relation")
        _only(item,{"relation_type","target_candidate_id"}); _required(item,{"relation_type","target_candidate_id"})
        if type(item["relation_type"]) is not str or item["relation_type"] not in _RELATION_TYPES or not _id(item["target_candidate_id"]): raise DocumentInterpretationSchemaError("relation")

def _evidence(value:object) -> None:
    if type(value) is not list or not value: raise DocumentInterpretationSchemaError("evidence")
    for item in value:
        if type(item) is not dict: raise DocumentInterpretationSchemaError("evidence")
        _only(item,{"kind","candidate_id","field"}); _required(item,{"kind","candidate_id","field"})
        if item["kind"]!="business_context" or not _id(item["candidate_id"]) or item["field"] not in _EVIDENCE_FIELDS: raise DocumentInterpretationSchemaError("evidence")
