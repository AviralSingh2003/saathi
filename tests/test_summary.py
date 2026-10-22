"""Summary phrasing: the facts given, feelings in the person's words, Saathi's hedged impression, nothing clinical."""

from __future__ import annotations

from saathi.core import people, summary

PERSON = {"person_id": "meera", "display_name": "Meera", "share_reminders": True,
          "share_selfreported_mood": True, "share_topics": False, "share_impressions": True}
CONTACT = {"name": "Priya"}


def test_template_states_only_facts():
    facts = [{"kind": "reminder_done", "text": "take my BP tablet", "time": "14:00", "spoken_time": "2 in the afternoon"},
             {"kind": "mood_selfreport", "word": "pretty good"}]
    body = summary.template_body(PERSON, facts)
    assert body == 'Meera completed the reminder "take my BP tablet" at 2 in the afternoon. Meera described feeling "pretty good".'
    assert summary.blocklist_violations(body, facts) == []


def test_phrasing_cannot_add_moods_that_are_not_in_the_facts():
    facts = [{"kind": "reminder_done", "text": "take my tablet", "time": "14:00", "spoken_time": "2 in the afternoon"}]
    assert summary.blocklist_violations("Meera seemed cheerful and took her tablet.", facts) == ["cheerful", "seemed"]


def test_todays_impression_is_allowed_and_templated():
    facts = [{"kind": "impression", "text": "Meera seemed cheerful today, especially about the cricket."}]
    body = summary.template_body(PERSON, facts)
    assert body == "Meera seemed cheerful today, especially about the cricket."
    assert summary.blocklist_violations(body, facts) == []


def test_clinical_language_is_never_allowed():
    facts = [{"kind": "impression", "text": "Meera seemed confused today."}]  # even if it got into the facts
    assert summary.blocklist_violations("Meera seemed confused today.", facts) == ["confused"]


def test_self_reported_word_is_allowed():
    facts = [{"kind": "mood_selfreport", "word": "happy"}]
    assert summary.blocklist_violations('Meera said she feels "happy".', facts) == []


def test_bedrock_output_with_mood_words_falls_back_to_template(monkeypatch, seeded):
    monkeypatch.setenv("MOCK", "0")
    monkeypatch.setenv("BEDROCK_MODEL_ID", "fake-model")
    monkeypatch.setattr(summary, "_bedrock_body", lambda *a: "Meera sounded lonely but took her tablet.")
    facts = [{"kind": "reminder_done", "text": "take my tablet", "time": "14:00", "spoken_time": "2 in the afternoon"}]
    body, method = summary.compose(PERSON, CONTACT, facts, None)
    assert "lonely" not in body
    assert method.startswith("template")


def test_clean_bedrock_output_is_used(monkeypatch, seeded):
    monkeypatch.setenv("MOCK", "0")
    monkeypatch.setenv("BEDROCK_MODEL_ID", "fake-model")
    monkeypatch.setattr(summary, "_bedrock_body", lambda *a: "Meera took her BP tablet at 2 this afternoon.")
    facts = [{"kind": "reminder_done", "text": "take my BP tablet", "time": "14:00", "spoken_time": "2 in the afternoon"}]
    body, method = summary.compose(PERSON, CONTACT, facts, "The roses are out")
    assert method == "bedrock"
    assert body == 'Meera took her BP tablet at 2 this afternoon. Meera asked me to tell you: "The roses are out."'


def test_bedrock_failure_falls_back(monkeypatch, seeded):
    monkeypatch.setenv("MOCK", "0")
    monkeypatch.setenv("BEDROCK_MODEL_ID", "fake-model")

    def boom(*_):
        raise RuntimeError("no credentials")

    monkeypatch.setattr(summary, "_bedrock_body", boom)
    facts = [{"kind": "mood_selfreport", "word": "okay"}]
    body, method = summary.compose(PERSON, CONTACT, facts, None)
    assert body == 'Meera described feeling "okay".'
    assert "unavailable" in method


def test_mood_recorded_verbatim(seeded):
    people.log_mood_selfreport("meera", '  "pretty good" ')
    person = people.get_person("meera")
    facts = summary.collect_facts(person, {"name": "Priya"})
    assert facts == [{"kind": "mood_selfreport", "word": "pretty good"}]
