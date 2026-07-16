"""
Real-API conversation quality tests.
Calls Azure OpenAI with the actual system prompt and scores responses.
Run:  python tests/conversation_quality_test.py
"""
import asyncio
import os
import sys
import re

# Force UTF-8 on Windows so Unicode in print() doesn't crash
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from src.ai_response_handler import AIResponseHandler

PASS = "\033[92m[PASS]\033[0m"
FAIL = "\033[91m[FAIL]\033[0m"
INFO = "\033[94m[INFO]\033[0m"

failures = []

def score(label, ok, response="", detail=""):
    tag = PASS if ok else FAIL
    print(f"  {tag}  {label}")
    if response:
        print(f"         response: {response[:120]!r}")
    if detail:
        print(f"         detail  : {detail}")
    if not ok:
        failures.append(label)


def make_handler():
    return AIResponseHandler(
        endpoint=os.getenv("AZURE_OPENAI_ENDPOINT", ""),
        api_key=os.getenv("AZURE_OPENAI_KEY", ""),
        deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-5-mini"),
        important_topics=[],
    )


# helpers
THIRD_PERSON_PATTERNS = [
    r'\btell\s+\w+\b',          # "tell kruskal", "tell him"
    r'\b(he|him|his)\b',        # male 3rd person
    r'\b\w+ (should|needs to|might want)\b',  # "kruskal should"
]
AI_SLIP_PATTERNS = [
    r'\bas an ai\b', r'\bi am an ai\b', r'\blanguage model\b',
    r'\bi cannot\b', r'\bi\'m unable\b', r'\bi don\'t have\b',
    r'\bi should note\b', r'\bplease note\b',
]
MENU_PATTERNS = [
    r'option [a-z1-9][\):]', r'\d\.\s+\w', r'here are',
    r'would you like me to', r'shall i', r'want me to',
]

def has_pattern(text, patterns):
    t = text.lower()
    return any(re.search(p, t) for p in patterns)


async def run_tests():
    h = make_handler()
    print("\n" + "=" * 60)
    print("ChronoChunk — Conversation Quality (Real API)")
    print("=" * 60)

    # ── 1. Third-person test ────────────────────────────────────────
    print("\n1. Third-person avoidance")
    r = await h.generate_response("/how is life", "", "Kruskal")
    score("No 'tell Kruskal' / no third-person",
          not has_pattern(r, THIRD_PERSON_PATTERNS), r)
    score("Not an AI-assistant slip",
          not has_pattern(r, AI_SLIP_PATTERNS), r)

    # ── 2. Follow-up context retention ─────────────────────────────
    print("\n2. Follow-up context retention")
    history = (
        "RESPONDING TO: Kruskal\n"
        "RECENT CONVERSATION (NEWEST LAST):\n"
        "Kruskal: i am just drinking tea and looking at my discord\n"
        ">>> YOU (ChronoChunk): ngl that sounds hella cozy, what tea u sippin??\n"
        ">>> Kruskal: some traditional tea"
    )
    r = await h.generate_response("/some traditional tea", history, "Kruskal")
    score("Remembers 'tea' context (no 'u mean the drink or gossip')",
          "drink or" not in r.lower() and "gossip" not in r.lower(), r)
    score("Responds to the tea topic naturally",
          any(w in r.lower() for w in ["tea", "traditional", "cozy", "nice", "vibes", "brew"]), r)

    await asyncio.sleep(2)
    # ── 3. Short follow-up stays on topic ──────────────────────────
    print("\n3. Short follow-up stays on topic")
    history2 = (
        "RESPONDING TO: Kruskal\n"
        "RECENT CONVERSATION (NEWEST LAST):\n"
        "Kruskal: what games do u play\n"
        ">>> YOU (ChronoChunk): bro i been on valorant rn, the ranked grind is real fr"
    )
    r = await h.generate_response("/why", history2, "Kruskal")
    score("'why' follow-up stays on valorant/games topic",
          any(w in r.lower() for w in [
              "valorant", "ranked", "game", "grind", "comp", "iron", "bronze",
              "aim", "clutch", "queue", "queuing", "addicting", "climb", "fun",
              "sweaty", "match", "play", "skill", "round"
          ]), r)

    await asyncio.sleep(2)
    # ── 4. No AI-assistant behavior ─────────────────────────────────
    print("\n4. No AI-assistant behavior")
    r = await h.generate_response("/yo", "", "TestUser")
    score("No menu / options list",    not has_pattern(r, MENU_PATTERNS), r)
    score("No 'I am an AI' slip",      not has_pattern(r, AI_SLIP_PATTERNS), r)
    score("No formal capitalization",
          r == r.lower() or r[0].isupper() and r[1:] == r[1:].lower(), r,
          "check for ALL CAPS sentences")

    await asyncio.sleep(2)
    # ── 5. Personality consistency ─────────────────────────────────
    print("\n5. Personality / voice consistency")
    casual_markers = ["u", "ur", "fr", "ngl", "lol", "lmao", "bruh", "bro", "my g", "tbh", "ong", "nah", "yeah", "yea", "wtf", "omg", "tho", "deadass"]
    r = await h.generate_response("/what do you think about school", "", "TestUser")
    score("Uses casual/gen-z language",
          any(m in r.lower() for m in casual_markers), r)
    score("Not a formal essay response",
          len(r.split("\n")) <= 3 and "furthermore" not in r.lower(), r)

    await asyncio.sleep(2)
    # ── 6. Direct question -> direct answer (not evasion)
    print("\n6. Direct question -> direct answer (not evasion)")
    r = await h.generate_response("/what is 9+10", "", "TestUser")
    score("9+10 = 21 meme response",
          "21" in r, r)

    await asyncio.sleep(2)
    # ── 7. Meme / culture recognition ─────────────────────────────
    print("\n7. Meme recognition")
    r = await h.generate_response("/bruh", "", "TestUser")
    score("Responds naturally to 'bruh' (not a formal explanation)",
          len(r.split()) < 40 and not has_pattern(r, AI_SLIP_PATTERNS), r)

    await asyncio.sleep(2)
    # ── 8. Group chat -- distinguishes users
    print("\n8. Group chat -- distinguishes users")
    history_group = (
        "RESPONDING TO: Alex\n"
        "RECENT CONVERSATION (NEWEST LAST):\n"
        "Jordan: yo wtf is up\n"
        "YOU (ChronoChunk): yo jordan nothing much what's good\n"
        ">>> Alex: i just got a new pc"
    )
    r = await h.generate_response("/i just got a new pc", history_group, "Alex")
    score("Responds to Alex about PC (not Jordan)",
          any(w in r.lower() for w in ["pc", "specs", "build", "game", "setup", "bro", "damn", "ayo"]), r)
    score("Does not mix up users", "jordan" not in r.lower(), r)

    await asyncio.sleep(2)
    # ── 9. User fact memory ────────────────────────────────────────
    print("\n9. User fact memory")
    history_facts = (
        "RESPONDING TO: Kruskal\n"
        "WHAT YOU KNOW ABOUT THEM:\n"
        "  You are from Brazil\n"
        "  You play guitar\n"
        "RECENT CONVERSATION (NEWEST LAST):\n"
        ">>> Kruskal: what should i do today"
    )
    r = await h.generate_response("/what should i do today", history_facts, "Kruskal")
    score("References known facts (Brazil or guitar)",
          any(w in r.lower() for w in ["brazil", "guitar", "music", "play", "brazilian"]), r)

    await asyncio.sleep(2)
    # ── 10. Response length sanity ─────────────────────────────────
    print("\n10. Response length sanity")
    r_short = await h.generate_response("/yo", "", "TestUser")
    r_long  = await h.generate_response(
        "/bro i just had the worst day ever my boss yelled at me my car broke down and i spilled coffee on my laptop",
        "", "TestUser"
    )
    score("Short message -> short reply (<=30 words)",  len(r_short.split()) <= 30, r_short)
    score("Long message -> longer reply (>=10 words)", len(r_long.split()) >= 10,  r_long)

    # ── Summary ───────────────────────────────────────────────────
    print("\n" + "=" * 60)
    total = 10 + 2 + 1 + 2 + 2 + 1 + 1 + 2 + 1 + 2  # rough count
    if failures:
        print(f"RESULT: {len(failures)} check(s) FAILED:")
        for f in failures:
            print(f"  [X]  {f}")
    else:
        print("RESULT: All quality checks passed")
    print("=" * 60)
    return len(failures)


if __name__ == "__main__":
    n = asyncio.run(run_tests())
    sys.exit(1 if n else 0)
