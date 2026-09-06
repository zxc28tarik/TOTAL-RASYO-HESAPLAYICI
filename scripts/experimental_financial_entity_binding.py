"""Archived financial entity tokens; never a price/share-class mapping."""
import re
from scripts.experimental_financial_ticker_lineage import candidate_source_tickers


def entity_tokens(source_entity):
    text = str(source_entity)
    if not re.fullmatch(r'[A-Z0-9]+(?:-[A-Z0-9]+)*', text):
        return ()
    tokens = tuple(text.split('-'))
    return tokens if len(tokens) == len(set(tokens)) else ()


def matching_financial_entities(ticker, cutoff, entities):
    candidates = set(candidate_source_tickers(ticker, cutoff))
    return tuple(sorted(e for e in entities if candidates.intersection(entity_tokens(e))))


def financial_entity_binding(ticker, cutoff, report):
    source = report['source_entity_code']
    tokens = entity_tokens(source)
    matched = sorted(set(candidate_source_tickers(ticker, cutoff)).intersection(tokens))
    if not matched:
        raise ValueError('FINANCIAL_ENTITY_TOKEN_MEMBERSHIP_MISSING')
    return {'contract':'EXPERIMENTAL_ARCHIVED_FINANCIAL_ENTITY_BINDING_V1',
        'target_ticker':ticker,'source_entity_code':source,'entity_tokens':list(tokens),
        'matched_source_tickers':matched,'archive_name':report.get('archive_name'),
        'member_name':report.get('member_name'),'archive_sha256':report.get('archive_sha256'),
        'member_sha256':report.get('member_sha256'),
        'composite_entity':len(tokens)>1,'financial_statement_identity_only':True,
        'price_transfer_allowed':False,'nominal_share_transfer_allowed':False,
        'risk_ids':['ARCHIVED_COMPOSITE_ENTITY_FINANCIAL_BINDING_NOT_SHARE_CLASS_PROOF'] if len(tokens)>1 else []}
