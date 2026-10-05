import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()
from openai import AsyncOpenAI
from src.ai_response_handler import _SYSTEM_PROMPT

async def test():
    client = AsyncOpenAI(
        base_url=os.getenv("AZURE_OPENAI_ENDPOINT"),
        api_key=os.getenv("AZURE_OPENAI_KEY")
    )
    dep = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-5-mini")

    tests = [
        ("no history", [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user",   "content": "[Kruskal is talking to you right now]: \"how is life\""},
        ]),
        ("with history", [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user",   "content": (
                "RESPONDING TO: Kruskal\n"
                "RECENT CONVERSATION:\n"
                "Kruskal: i am drinking tea\n"
                ">>> YOU: what tea u sippin??\n"
                ">>> Kruskal: some traditional tea\n\n"
                "[Kruskal is talking to you right now]: \"some traditional tea\""
            )},
        ]),
        ("2000 tokens limit no history", [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user",   "content": "[Kruskal is talking to you right now]: \"how is life\""},
        ]),
    ]

    for label, messages in tests:
        limit = 2000 if "2000" in label else 500
        try:
            r = await client.chat.completions.create(
                model=dep, messages=messages, max_completion_tokens=limit
            )
            ch = r.choices[0]
            print(f"\n[{label}]")
            print(f"  finish_reason : {ch.finish_reason}")
            print(f"  content       : {repr((ch.message.content or '')[:120])}")
        except Exception as e:
            print(f"\n[{label}] ERROR: {e}")

asyncio.run(test())
