# -*- coding: utf-8 -*-
"""Память диалога: извлечение сообщений, заголовки, сжатие истории (без вызова модели)."""
import asyncio
import types

from app import memory


def _event(role, text, ev_id):
    return types.SimpleNamespace(
        id=ev_id, timestamp=0.0,
        content=types.SimpleNamespace(
            role=role, parts=[types.SimpleNamespace(text=text, thought=False)]))


class TestExtractAndTitle:
    def test_extract_messages(self):
        s = types.SimpleNamespace(events=[
            _event("user", "привет", "1"),
            _event("model", "здравствуйте", "2"),
            types.SimpleNamespace(id="3", content=None),          # пустое событие
            _event("model", "", "4"),                              # без текста
        ])
        msgs = memory.extract_messages(s)
        assert [m["role"] for m in msgs] == ["user", "agent"]
        assert msgs[1]["text"] == "здравствуйте"

    def test_dialog_title_first_sentence(self):
        assert memory.dialog_title("Подбери вакансии C# в Ростове-на-Дону. Ещё вопрос") == \
            "Подбери вакансии C# в Ростове-на-Дону."
        assert memory.dialog_title("   ") == "Диалог"

    def test_think_not_in_answer(self):
        """Рассуждения (thought) не смешиваются с ответом: они идут отдельным полем think."""
        s = types.SimpleNamespace(events=[
            _event("user", "привет", "1"),
            types.SimpleNamespace(id="2", content=types.SimpleNamespace(role="model", parts=[
                types.SimpleNamespace(text="сначала подумаю", thought=True),
                types.SimpleNamespace(text="готово", thought=False),
            ])),
        ])
        msgs = memory.extract_messages(s)
        assert msgs[1]["text"] == "готово"
        assert msgs[1]["think"] == "сначала подумаю"

    def test_tool_results_kept_for_cards(self):
        """Результаты инструментов сохраняются в истории — по ним UI восстанавливает
        кликабельные карточки вакансий при переключении диалогов."""
        part = types.SimpleNamespace(
            text=None, thought=False, function_call=None,
            function_response=types.SimpleNamespace(
                name="search_vacancies",
                response={"found": 1, "vacancies": [{"vacancy_id": "42", "name": "X"}]}))
        s = types.SimpleNamespace(events=[
            _event("user", "найди вакансии", "1"),
            types.SimpleNamespace(id="2", content=types.SimpleNamespace(
                role="model", parts=[part, types.SimpleNamespace(text="нашёл", thought=False)])),
        ])
        msgs = memory.extract_messages(s)
        assert msgs[1]["tools"][0]["name"] == "search_vacancies"
        assert msgs[1]["tools"][0]["payload"]["vacancies"][0]["vacancy_id"] == "42"
        assert msgs[1]["text"] == "нашёл"

    def test_tool_events_with_user_role_join_agent_turn(self):
        """ADK кладёт function_response в события с role='user' — они должны попадать
        в ход агента (для карточек), а не превращаться в пустые реплики пользователя."""
        def tool_event(ev_id, name, payload):
            return types.SimpleNamespace(id=ev_id, content=types.SimpleNamespace(
                role="user", parts=[types.SimpleNamespace(
                    text=None, thought=False, function_call=None,
                    function_response=types.SimpleNamespace(name=name, response=payload))]))
        s = types.SimpleNamespace(events=[
            _event("user", "найди вакансии", "1"),
            _event("model", "сейчас поищу", "2"),
            tool_event("3", "search_vacancies", {"vacancies": [{"vacancy_id": "7"}]}),
            _event("model", "вот подборка", "4"),
        ])
        msgs = memory.extract_messages(s)
        assert [m["role"] for m in msgs] == ["user", "agent"]
        assert msgs[1]["text"] == "сейчас поищу\nвот подборка"
        assert msgs[1]["tools"][0]["payload"]["vacancies"][0]["vacancy_id"] == "7"

    def test_dialog_title_truncates_long(self):
        t = memory.dialog_title("очень длинный вопрос про вакансии и зарплаты " * 10)
        assert len(t) <= 61 and t.endswith("…")


class TestCompaction:
    def test_fallback_summary(self):
        s = memory._fallback_summary([
            {"role": "user", "text": "ищу вакансии"}, {"role": "agent", "text": "нашёл"}])
        assert "ищу вакансии" in s

    def test_compact_replaces_old_events_with_summary(self, tmp_path, monkeypatch):
        """История за бюджетом сжимается: старые события удаляются, сводка добавляется."""
        monkeypatch.setattr(memory.config, "DATA", str(tmp_path))
        monkeypatch.setattr(memory, "MIN_MESSAGES", 4)
        monkeypatch.setattr(memory, "CHAR_BUDGET", 100)

        async def fake_summarize(text):
            return "Пользователь искал вакансии C#, агент показал подборку."
        monkeypatch.setattr(memory, "_llm_summarize", fake_summarize)

        from google.adk.sessions import DatabaseSessionService
        svc = DatabaseSessionService(db_url="sqlite+aiosqlite:///" +
                                    str(tmp_path / "sessions.db").replace("\\", "/"))
        runner = types.SimpleNamespace(session_service=svc, app_name="app")

        async def run():
            s = await svc.create_session(app_name="app", user_id="u1")
            for i in range(12):  # 12 событий: 6 пар вопрос/ответ
                from google.adk.events.event import Event
                from google.genai import types as gt
                role = "user" if i % 2 == 0 else "model"
                ev = Event(author="user" if i % 2 == 0 else "agent",
                           content=gt.Content(role=role, parts=[
                               gt.Part(text=f"сообщение номер {i} " + "х" * 40)]))
                await svc.append_event(s, ev)
            summary = await memory.compact_if_needed(runner, "u1", s.id)
            assert summary and "вакансии C#" in summary
            after = await svc.get_session(app_name="app", user_id="u1", session_id=s.id)
            # старые события удалены: осталось KEEP_EVENTS + сводка
            assert len(after.events) == memory.KEEP_EVENTS + 1
            assert memory.SUMMARY_PREFIX in after.events[0].content.parts[0].text or \
                   any(memory.SUMMARY_PREFIX in (p.text or "")
                       for e in after.events for p in e.content.parts)
        asyncio.run(run())
        asyncio.run(svc.db_engine.dispose())  # закрыть aiosqlite-движок

    def test_compact_skips_short_history(self, tmp_path, monkeypatch):
        monkeypatch.setattr(memory.config, "DATA", str(tmp_path))
        monkeypatch.setattr(memory, "MIN_MESSAGES", 8)
        monkeypatch.setattr(memory, "CHAR_BUDGET", 12000)

        from google.adk.sessions import DatabaseSessionService
        svc = DatabaseSessionService(db_url="sqlite+aiosqlite:///" +
                                    str(tmp_path / "sessions.db").replace("\\", "/"))
        runner = types.SimpleNamespace(session_service=svc, app_name="app")

        async def run():
            s = await svc.create_session(app_name="app", user_id="u1")
            assert await memory.compact_if_needed(runner, "u1", s.id) is None
        asyncio.run(run())
        asyncio.run(svc.db_engine.dispose())  # закрыть aiosqlite-движок
