import asyncio

from folder_intelligence.assistant import Assistant
from folder_intelligence.commands import registry
from folder_intelligence.memory import create_memory
from folder_intelligence.router import Router, clean_args


class FakeAI:
    """Finto Ollama: restituisce i piani preparati, in ordine, e registra le richieste."""

    model = "fake"

    def __init__(self, *plans, available=True, fail=False):
        self.plans = list(plans)
        self.prompts = []
        self.available = available
        self.fail = fail

    async def is_available(self):
        return self.available

    async def can_see(self):
        return False

    async def can_embed(self):
        return False

    async def has(self, _model):
        return False

    async def generate_json(self, prompt, system="", schema=None, model=None):
        self.prompts.append({"prompt": prompt, "system": system, "schema": schema})
        if self.fail:
            raise TimeoutError()
        return self.plans.pop(0) if self.plans else {"steps": []}


def route(ai, text, ctx=None):
    return asyncio.run(Router(registry(), ai, mode="ai").route(text, ctx))


def test_ai_plan_with_multiple_steps_and_folder_inheritance():
    ai = FakeAI({"steps": [{"command": "index", "path": "Documents"},
                           {"command": "similar", "args": {"kind": "documents", "bogus": 1}}]})
    plan = route(ai, "indicizza Documents e poi trova i documenti simili")
    assert plan.source == "ai" and [s.type for s in plan.steps] == ["index", "similar"]
    assert plan.steps[0].path.name == "Documents" and plan.steps[1].path == plan.steps[0].path
    assert plan.steps[1].args["kind"] == "documents" and "bogus" not in plan.steps[1].args
    schema_names = ai.prompts[0]["schema"]["properties"]["steps"]["items"]["properties"]["command"]["enum"]
    assert "similar" in schema_names and "exit" not in schema_names


def test_destructive_goes_last_and_only_one():
    ai = FakeAI({"steps": [{"command": "dedupe"}, {"command": "organize"}, {"command": "largest", "args": {"n": "5"}}]})
    plan = route(ai, "x")
    assert [s.type for s in plan.steps] == ["largest", "dedupe"]
    assert plan.steps[0].args == {"n": 5}
    assert any("solo la prima" in n for n in plan.notes)


def test_unknown_commands_and_bad_args_are_dropped():
    notes = []
    assert clean_args({"kind": "video", "n": "tanti", "ref": "tutto", "format": "CSV"}, notes) == {"format": "csv"}
    plan = route(FakeAI({"steps": [{"command": "rm_rf"}, {"command": "exit"}]}), "cancella tutti i file pdf")
    # l'AI non ha proposto niente di valido → riserva a regole (che non riconoscono la frase)
    assert plan.source == "rules" and plan.steps[0].type == "unknown"


def test_direct_reply_without_commands_and_no_invented_reply_with_steps():
    plan = route(FakeAI({"steps": [], "reply": "Ciao! Come posso aiutarti?"}), "ciao")
    assert plan.steps == [] and plan.reply.startswith("Ciao")
    plan = route(FakeAI({"steps": [{"command": "organize"}], "reply": "Sto spostando tutto nel Cestino"}), "riordina")
    assert plan.reply == ""


def test_shortcuts_and_fallbacks_skip_the_ai():
    ai = FakeAI()
    assert route(ai, "esci").source == "shortcut"
    assert route(ai, "aiuto").steps[0].type == "help"
    assert ai.prompts == []
    assert route(FakeAI(available=False), "trova foto simili").source == "rules"
    plan = route(FakeAI(fail=True), "trovami i duplicati")
    assert plan.source == "rules" and plan.steps[0].type == "duplicates" and plan.notes


def make(sample_dir, ai):
    return Assistant(sample_dir, memory=create_memory("inmemory"), ollama=ai, use_ai=True)


def test_plan_runs_in_order_and_asks_confirmation_at_the_end(sample_dir):
    ai = FakeAI({"steps": [{"command": "largest", "args": {"n": 2}}, {"command": "dedupe"}]})
    a = make(sample_dir, ai)

    async def flow():
        out = await a.handle("mostrami i 2 file più grandi e poi cancella i duplicati")
        assert out.index("Farò, in ordine") < out.index("file più grandi") < out.index("Confermi")
        assert a.pending is not None and out.data["command"] == "plan"
        assert out.data["route"]["source"] == "ai"
        done = await a.handle("sì")  # scorciatoia: niente AI
        assert "spostati nel Cestino" in done
        assert len(ai.prompts) == 1

    asyncio.run(flow())


def test_plan_pauses_on_question_and_resumes_after_yes(sample_dir):
    ai = FakeAI({"steps": [{"command": "similar", "args": {"kind": "documents"}},
                           {"command": "largest", "args": {"n": 1}}]})
    a = make(sample_dir, ai)

    async def flow():
        out = await a.handle("trova i documenti simili e poi il file più grande")
        lines = out.splitlines()
        assert "(sì/no)" in lines[-1] or "(sì/no)" in "\n".join(lines[-3:])
        assert "proseguo con" in out and a.plan_rest
        done = await a.handle("sì")
        assert "file più grandi" in done and not a.plan_rest

    asyncio.run(flow())


def test_new_request_drops_paused_plan(sample_dir):
    ai = FakeAI({"steps": [{"command": "similar", "args": {"kind": "documents"}},
                           {"command": "largest"}]},
                {"steps": [{"command": "summary"}]})
    a = make(sample_dir, ai)

    async def flow():
        await a.handle("trova i documenti simili e poi i file più grandi")
        assert a.plan_rest
        await a.handle("fammi un riepilogo")
        assert not a.plan_rest and a.pending is None

    asyncio.run(flow())
