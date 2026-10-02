"""Compare financial declarations with effects supplied by the executing node.

This cannot measure nonfinancial benefit, real-world identity, or hidden costs.
Caller-written effects and benefit/cost numbers are never treated as evidence.
"""
import math
import re
import unicodedata


def bounded_score(value):
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value) and 0 <= value <= 1)


def ledger_policy(data, effects):
    warnings = ['Nonfinancial benefit and unobserved costs are unknown, not zero.']
    declaration = data.get('ethics', {})
    relation = declaration.get('relationship') if isinstance(declaration, dict) else None
    message = data.get('message', '')
    text = unicodedata.normalize('NFKC', message).casefold() if isinstance(message, str) else ''
    text = ''.join(c for c in text if unicodedata.category(c) != 'Cf')
    # Structured declarations and the explicit legacy label are supported.
    # This is not a claim to understand every natural-language paraphrase.
    legacy_claim = any(not re.search(r'\b(?:not|no|without)(?:\s+\w+){0,3}\s*$', text[max(0, m.start()-40):m.start()])
                       for m in re.finditer(r'\bmutual\s+benefit\b', text))
    claims_mutual = relation == 'mutual_benefit' or legacy_claim
    warnings.append('Opt-out is unverified by this gate; a missing opt-out does not block.')
    if effects is None:
        warnings.append('Ledger net effects are unknown for this action.')
        return False, None, warnings
    negatives = any(value < 0 for value in effects.values())
    positives = any(value > 0 for value in effects.values())
    if negatives:
        warnings.append('At least one party has a negative measured ledger net.')
    if claims_mutual and negatives:
        return True, ('Declared mutual ledger benefit contradicts the measured net effects; '
                      'no independently verified compensation is present.'), warnings
    if negatives and positives:
        warnings.append('One-way ledger value; an honestly declared gift/payment may proceed.')
    return False, None, warnings
