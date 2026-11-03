"""Historical configuration inputs for the decision-model repair journey."""

RETIRED_SETTINGS = {
    "context_reduction_strategy": "stepped_session_map",
    "compaction_keep_recent": 4,
    "compaction_token_threshold": 5000,
    "session_map_author_model": "test",
    "session_map_author_thinking": "low",
    "session_map_low_watermark_tokens": 1500,
    "session_map_min_retained_groups": 3,
    "live_session_memory_mode": "observe",
    "live_session_memory_decision_model": "jev",
    "live_session_memory_author_model": "test",
    "live_session_memory_eligibility_turns": 4,
    "live_session_memory_broad_change_threshold": 0.5,
    "live_session_memory_field_change_threshold": 0.5,
    "live_session_memory_max_pending_turns": 12,
    "live_session_memory_max_pending_tokens": 24000,
    "live_session_memory_task_timeout_seconds": 120,
    "live_session_memory_max_concurrent_tasks": 1,
    "session_map_gate_model": "jev",
    "session_map_gate_threshold": 0.5,
    "session_map_gate_max_input_tokens": 24000,
}
