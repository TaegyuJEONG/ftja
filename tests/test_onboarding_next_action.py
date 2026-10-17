import pytest

from ftja.onboarding import apply_action, read_state, next_action, OnboardingError


def confirmed_summary(root):
    apply_action(root, {"type": "set_profile_sources", "sources": [{"path": "resume.pdf"}]})
    apply_action(root, {"type": "confirm_profile_sources"})
    apply_action(root, {"type": "set_profile_summary", "summary": "Senior GRC consultant. EBIOS RM and supplier risk. Reads English and French."})
    return apply_action(root, {"type": "confirm_profile_summary"})


def test_resume_without_target_role_or_product_names_produces_editable_proposal(tmp_path):
    root = str(tmp_path)
    state = confirmed_summary(root)
    assert state["next_action"]["type"] == "draft_search_settings"
    assert read_state(root)["next_action"] == state["next_action"]
    state = apply_action(root, {"type": "set_profile_draft", "titles": ["Cyber Security Consultant"],
        "location": "France", "stage0": {"keywords": ["supplier risk", "risque fournisseur"],
        "languages": ["en", "fr"], "exclude_keywords": []}})
    assert state["next_action"]["actions"] == ["confirm_profile"]
    assert state["phase"] == "title_proposal"
    assert not (tmp_path / "criteria.json").exists()
    state = apply_action(root, {"type": "confirm_profile", "titles": ["GRC Consultant"], "location": "France"})
    assert state["phase"] == "stage0"
    assert state["cards"]["stage0"]["keywords"] == ["supplier risk", "risque fournisseur"]
    assert state["profile"]["titles"] == ["GRC Consultant"]


def test_essential_question_is_saved_in_web_state_and_resumes_after_answer(tmp_path):
    root = str(tmp_path)
    confirmed_summary(root)
    state = apply_action(root, {"type": "ask_search_question", "question": "Which country should we search?"})
    assert next_action(state) == {"type": "wait", "actions": ["answer_search_question"], "surface": "web"}
    with pytest.raises(OnboardingError):
        apply_action(root, {"type": "answer_search_question", "answer": " "})
    state = apply_action(root, {"type": "answer_search_question", "answer": "France", "expected_revision": state["revision"]})
    assert read_state(root)["search_answers"][0]["answer"] == "France"
    assert state["next_action"]["type"] == "draft_search_settings"
    with pytest.raises(OnboardingError):
        apply_action(root, {"type": "answer_search_question", "answer": "Germany"})
