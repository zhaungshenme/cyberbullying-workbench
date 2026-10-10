"""Standalone copy of the existing language-observation model schema.

Source: cyberbullying_assistant_v1/service/schema.py, MODEL_OUTPUT_SCHEMA.
The gateway enforces this allowlist; the business server verifies evidence.
"""

ATTACK_OBSERVATIONS = {
    "direct_personal_insult", "implicit_targeted_derogation", "obfuscated_personal_insult",
    "threat_expression", "hostile_death_wish", "personal_information_exposure_pattern",
    "incitement_to_target", "sexualized_insult", "repeated_targeted_attack",
    "multi_actor_targeted_attack", "explicit_coordination_expression",
}
CONTEXT_OBSERVATIONS = {
    "quoted_insult", "quoted_threat_without_endorsement", "condemnation_of_attack",
    "normal_criticism", "consumer_complaint", "educational_analysis", "fictional_dialogue",
    "help_or_report", "identity_unverified", "target_unresolved", "source_context_incomplete",
    "consent_unknown", "reported_attack_unverified", "defensive_role_with_attack", "multiple_targets",
}
MODEL_OUTPUT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "decision": {"type": "string", "enum": ["supported", "not_supported", "insufficient_context"]},
        "observations": {"type": "array", "items": {"type": "string", "enum": sorted(ATTACK_OBSERVATIONS | CONTEXT_OBSERVATIONS)}},
        "evidence": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "message_id": {"type": "string"}, "quote": {"type": "string", "minLength": 1},
                "occurrence": {"type": "integer", "minimum": 1},
            }, "required": ["message_id", "quote", "occurrence"],
        }},
        "target_actor_key": {"type": ["string", "null"]},
        "rationale": {"type": "string"},
        "context_flags": {"type": "array", "items": {"type": "string"}},
        "missing_info": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["decision", "observations", "evidence", "target_actor_key", "rationale", "context_flags", "missing_info"],
}
