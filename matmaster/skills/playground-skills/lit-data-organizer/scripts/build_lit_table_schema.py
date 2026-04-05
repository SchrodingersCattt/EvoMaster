"""Canonical field names and alias mapping for lit evidence table."""

from typing import Any

CANONICAL_FIELDS = [
    'source_id',
    'source_type',
    'source_title',
    'source_url_or_path',
    'topic',
    'claim_text',
    'quote_text',
    'summary_text',
    'evidence_span',
    'tags',
    'confidence',
    'created_at',
    'material_name',
    'formula',
    'composition',
    'phase_or_polymorph',
    'independent_vars',
    'property_name',
    'property_value',
    'property_unit',
    'property_role',
    'test_method',
    'conditions',
    'uncertainty',
    'conflict_group_id',
    'conflict_note',
    'enrich_note',
    'enrich_keep',
]


DEFAULT_ALIASES: dict[str, list[str]] = {
    # mat_sn_search-papers-enhanced: doi, paperId
    'source_id': [
        'source_id',
        'source.id',
        'doc_id',
        'paper_id',
        'id',
        'doi',
        'paperId',
    ],
    'source_type': ['source_type', 'source.type', 'origin_type'],
    # mat_sn_search-papers-enhanced: enName, zhName; web-search/mat_sn_web-search: title
    'source_title': [
        'source_title',
        'source.title',
        'title',
        'document_title',
        'paper_title',
        'name',
        'enName',
        'zhName',
    ],
    # mat_sn_search-papers-enhanced: paperUrl; web-search/mat_sn_web-search: link
    'source_url_or_path': [
        'source_url_or_path',
        'source_url',
        'url',
        'source.path',
        'path',
        'file_path',
        'pdf_path',
        'source',
        'paperUrl',
        'link',
    ],
    'topic': ['topic', 'subject', 'domain', 'field'],
    'claim_text': ['claim_text', 'claim', 'finding', 'result_claim', 'statement'],
    # web-search/mat_sn_web-search: snippet
    'quote_text': ['quote_text', 'quote', 'evidence_text', 'snippet', 'text'],
    # mat_sn_search-papers-enhanced: enAbstract, zhAbstract; web-search/mat_sn_web-search: snippet
    'summary_text': [
        'summary_text',
        'summary',
        'abstract',
        'note',
        'enAbstract',
        'zhAbstract',
        'snippet',
        'pieces',
    ],
    'evidence_span': [
        'evidence_span',
        'span',
        'page_span',
        'locator',
        'section',
        'paragraph',
        'page',
    ],
    'tags': ['tags', 'keywords', 'labels', 'facet'],
    'confidence': ['confidence', 'score', 'confidence_score'],
    # mat_sn_search-papers-enhanced: coverDateStart, year (from evidence_cards)
    'created_at': [
        'created_at',
        'timestamp',
        'date',
        'published_at',
        'year',
        'coverDateStart',
    ],
    'material_name': ['material_name', 'material', 'compound', 'compound_name'],
    'formula': ['formula', 'chemical_formula', 'composition.formula'],
    'composition': ['composition', 'metal_composition', 'alloy_composition'],
    'phase_or_polymorph': ['phase_or_polymorph', 'phase', 'polymorph', 'crystal_form'],
    'independent_vars': [
        'independent_vars',
        'features',
        'inputs',
        'independent_variables',
        'data_points',
    ],
    'property_name': ['property_name', 'target_name', 'property', 'measurement_name'],
    'property_value': ['property_value', 'target_value', 'value', 'measurement_value'],
    'property_unit': ['property_unit', 'unit', 'units'],
    'property_role': ['property_role', 'role', 'variable_role'],
    'test_method': ['test_method', 'method', 'measurement_method', 'protocol'],
    'conditions': ['conditions', 'condition', 'experimental_conditions'],
    'uncertainty': ['uncertainty', 'error_bar', 'std', 'sigma'],
    'conflict_group_id': ['conflict_group_id'],
    'conflict_note': ['conflict_note'],
    'enrich_note': ['enrich_note'],
    'enrich_keep': ['enrich_keep'],
}


def canonical_aliases(schema_cfg: dict[str, Any]) -> dict[str, list[str]]:
    """Merge schema field_aliases with DEFAULT_ALIASES."""
    aliases = {k: list(v) for k, v in DEFAULT_ALIASES.items()}
    schema_aliases = schema_cfg.get('field_aliases', {})
    if isinstance(schema_aliases, dict):
        for field, alias_values in schema_aliases.items():
            if field not in CANONICAL_FIELDS:
                continue
            if isinstance(alias_values, str):
                alias_list = [alias_values]
            elif isinstance(alias_values, list):
                alias_list = [str(v) for v in alias_values]
            else:
                continue
            aliases[field] = [field] + [a for a in alias_list if a != field]
    else:
        for field in CANONICAL_FIELDS:
            aliases[field] = [field] + [a for a in aliases[field] if a != field]
    return aliases
