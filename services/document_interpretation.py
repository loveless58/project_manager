"""Controlled, fail-closed document interpretation boundary."""
from __future__ import annotations
from typing import Any
import re
import math
from contracts.document_interpretation import DocumentInterpretationSchemaError, parse_candidate_document_interpretation
from platform_core.models import BusinessContextEvidence, BusinessContextQuery

SCHEMA_VERSION="candidate_document_interpretation.v1"
PROMPT_VERSION="document_interpretation.v1"
POLICY_VERSION="document_interpretation_policy.v1"
_REQUEST_FAILED="DOCUMENT_INTERPRETATION.REQUEST_FAILED"
_ALLOWED_ERROR_CODES={_REQUEST_FAILED,"DOCUMENT_INTERPRETATION.LLM.REQUEST_FAILED","DOCUMENT_INTERPRETATION.LLM.RESPONSE_INVALID"}
_FIELDS={"invoice_number","invoice_date","amount","tax_amount","total_amount","buyer_name","buyer_tax_id","seller_name","seller_tax_id","project_code","project_name","contract_code","contract_name"}
_FACTS={"contract_code","project_code","amount","date"}
_EVIDENCE={"buyer.tax_id","seller.tax_id","buyer.name","seller.name","contract_code","project_code","amount","date"}
_ARTIFACT=re.compile(r"^artifact:[a-z][a-z0-9_-]{0,31}:[A-Za-z0-9_.:-]{1,128}$")
_DIAGNOSTICS={"BUSINESS_CONTEXT.CANDIDATES_FOUND","BUSINESS_CONTEXT.NO_CANDIDATES","BUSINESS_CONTEXT.CONFLICTS_FOUND"}

class DocumentInterpretationService:
 def __init__(self,retrieval_service:Any,interpreter:Any)->None: self.retrieval_service,self.interpreter=retrieval_service,interpreter
 def interpret(self,evidence_pack:Any)->dict[str,Any]:
  ref=_artifact(evidence_pack.get("parse_artifact_ref")) if type(evidence_pack) is dict else ""
  try:
   doc=_document(evidence_pack)
   context=self.retrieval_service.find_business_candidates(BusinessContextQuery(doc["document_type_hint"],doc["candidate_fields"],doc["text_segments"]))
   if not isinstance(context,BusinessContextEvidence): raise ValueError
   request=_request(ref,doc,context)
   result=parse_candidate_document_interpretation(self.interpreter.complete_json(request))
   _identity(result,self.interpreter); _trace(result,request["business_context"])
  except DocumentInterpretationSchemaError: return _blocked(ref,"DOCUMENT_INTERPRETATION.SCHEMA_INVALID")
  except Exception as error:
   code=getattr(error,"code",None)
   return _blocked(ref,code if type(code) is str and code in _ALLOWED_ERROR_CODES else _REQUEST_FAILED)
  out=dict(result); out["parse_artifact_ref"]=ref; out["confirmed"]=False
  if out["status"]=="success" and out["relations"]: out["status"]="needs_review"
  return out

def _text(v:object)->str: return v if type(v) is str and 0<len(v)<=512 and v==v.strip() and not re.search(r"(^[A-Za-z]:[\\/]|^/|^\\\\|authorization|bearer|token|api[_-]?key)", v, re.I) else ""
def _artifact(v:object)->str: return v if type(v) is str and bool(_ARTIFACT.fullmatch(v)) else ""
def _document(raw:object)->dict[str,Any]:
 if type(raw) is not dict: raise DocumentInterpretationSchemaError("input")
 hint=_text(raw.get("document_type_hint")); fields=raw.get("candidate_fields"); segs=raw.get("text_segments")
 if not _artifact(raw.get("parse_artifact_ref")) or not hint or type(fields) is not dict or type(segs) is not list or len(segs)>64: raise DocumentInterpretationSchemaError("input")
 clean={}
 for key,value in fields.items():
  if key not in _FIELDS: continue
  if type(value) is str and _text(value): clean[key]=value
  elif key in {"amount","tax_amount","total_amount"} and type(value) in (int,float): clean[key]=value
 # vendor-shaped buyer/seller are allowed only as exact small object
 for role in ("buyer","seller"):
  party=fields.get(role)
  if type(party) is dict:
   for key in ("name","tax_id"):
    value=party.get(key); target=f"{role}_{key}"
    if _text(value): clean[target]=value
 clean_segs=[]
 for seg in segs:
  if type(seg) is not dict or set(seg)-{"id","text"} or not _text(seg.get("id")) or type(seg.get("text")) is not str or len(seg["text"])>4096: raise DocumentInterpretationSchemaError("segment")
  clean_segs.append({"id":seg["id"],"text":seg["text"]})
 return {"document_type_hint":hint,"candidate_fields":fields,"llm_candidate_fields":clean,"text_segments":clean_segs}

def _request(ref:str,doc:dict[str,Any],ctx:BusinessContextEvidence)->dict[str,Any]:
 return {"schema_version":"document_interpretation_evidence_pack.v1","parse_artifact_ref":ref,"document":{"document_type_hint":doc["document_type_hint"],"candidate_fields":doc["llm_candidate_fields"],"text_segments":doc["text_segments"]},"business_context":{
  "status":ctx.status if type(ctx.status) is str and len(ctx.status)<=64 else "unknown",
  "candidates":[_candidate(x) for x in ctx.candidates if type(x) is dict],
  "evidence":[_evidence(x) for x in ctx.evidence_refs if type(x) is dict],
  "conflicts":[],"diagnostics":[{"code":x["code"]} for x in ctx.diagnostics if type(x) is dict and x.get("code") in _DIAGNOSTICS],
 }}
def _candidate(x:dict[str,Any])->dict[str,Any]:
 out={};
 for k in ("id","document_type"):
  if _text(x.get(k)): out[k]=x[k]
 parties=x.get("parties")
 if type(parties) is dict:
  p={}
  for role in ("buyer","seller"):
   item=parties.get(role)
   if type(item) is dict:
    q={k:item[k] for k in ("name","tax_id") if _text(item.get(k))}
    if q:p[role]=q
  if p:out["parties"]=p
 facts=x.get("facts")
 if type(facts) is dict:
  q={k:facts[k] for k in _FACTS if _text(facts.get(k)) or (k=="amount" and type(facts.get(k)) in (int,float))}
  if q:out["facts"]=q
 return out
def _evidence(x:dict[str,Any])->dict[str,str]:
 field=x.get("field"); candidate=x.get("candidate_id")
 if field in _EVIDENCE and _text(candidate): return {"kind":"business_context","candidate_id":candidate,"field":field}
 return {}
def _identity(r:dict[str,Any],i:Any)->None:
 if (getattr(i,"schema_version",None),getattr(i,"prompt_version",None),getattr(i,"policy_version",None)) != (SCHEMA_VERSION,PROMPT_VERSION,POLICY_VERSION) or (r["schema_version"],r["prompt_version"],r["policy_version"]) != (getattr(i,"schema_version",None),getattr(i,"prompt_version",None),getattr(i,"policy_version",None)) or r["interpreter"]!=getattr(i,"name",None) or r["model"]!=getattr(i,"model",None): raise DocumentInterpretationSchemaError("identity")
def _trace(r:dict[str,Any],ctx:dict[str,Any])->None:
 allowed={(x.get("kind"),x.get("candidate_id"),x.get("field")) for x in ctx["evidence"] if type(x) is dict}
 ids={x.get("id") for x in ctx["candidates"] if type(x) is dict}
 used={(x.get("kind"),x.get("candidate_id"),x.get("field")) for x in r["evidence"]}
 if not allowed or not used<=allowed: raise DocumentInterpretationSchemaError("evidence")
 for relation in r["relations"]:
  target=relation["target_candidate_id"]
  if target not in ids or not any(e[1]==target for e in used): raise DocumentInterpretationSchemaError("relation trace")
def _blocked(ref:str,reason:str)->dict[str,Any]: return {"status":"blocked","blocked_reason":reason[:128],"parse_artifact_ref":ref[:512],"confirmed":False}
