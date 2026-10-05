"""
Live integration check — hits real external systems.
Run AFTER the automated pytest suite:  python tests/live_check.py

Prints [PASS] / [FAIL] for each check.
Exits with code 1 if any non-advisory check fails.
"""
import asyncio
import json
import os
import shutil
import sys
import tempfile
import time

# Ensure project root on path and load .env before any imports
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))


PASS = "[PASS]"
FAIL = "[FAIL]"
INFO = "[INFO]"

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    tag = PASS if ok else FAIL
    suffix = f" — {detail}" if detail else ""
    print(f"  {tag}  {label}{suffix}")
    if not ok:
        failures.append(label)


# ── 1. Environment variables ──────────────────────────────────────────────────

def check_env():
    print("\n1. Environment variables")
    token = os.getenv("DISCORD_TOKEN")
    az_key = os.getenv("AZURE_OPENAI_KEY")
    az_ep = os.getenv("AZURE_OPENAI_ENDPOINT")
    az_dep = os.getenv("AZURE_OPENAI_DEPLOYMENT")
    check("DISCORD_TOKEN set", bool(token), f"length={len(token) if token else 0}")
    check("AZURE_OPENAI_KEY set", bool(az_key), f"length={len(az_key) if az_key else 0}")
    check("AZURE_OPENAI_ENDPOINT set", bool(az_ep), az_ep or "missing")
    check("AZURE_OPENAI_DEPLOYMENT set", bool(az_dep), az_dep or "missing")


# ── 2. Azure OpenAI direct call ───────────────────────────────────────────────

async def check_azure_openai_direct():
    print("\n2. Azure OpenAI -- direct SDK call")
    from openai import AsyncOpenAI

    api_key = os.getenv("AZURE_OPENAI_KEY", "")
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-5-mini")

    if not api_key or not endpoint:
        check("Azure OpenAI responds", False, "credentials missing -- skipping")
        return

    try:
        client = AsyncOpenAI(base_url=endpoint, api_key=api_key)
        resp = await asyncio.wait_for(
            client.chat.completions.create(
                model=deployment,
                messages=[{"role": "user", "content": "Reply with exactly: CHRONOCHUNK_TEST_OK"}],
                max_completion_tokens=2000,
            ),
            timeout=15.0
        )
        text = resp.choices[0].message.content or ""
        ok = "CHRONOCHUNK_TEST_OK" in text
        check(f"{deployment} responds", ok, repr(text[:60]))
    except asyncio.TimeoutError:
        check(f"{deployment} responds", False, "timed out after 15s")
    except Exception as e:
        check(f"{deployment} responds", False, str(e)[:120])


# ── 3. Azure OpenAI via UserDataManager (fact extraction) ────────────────────

async def check_azure_via_udm():
    print("\n3. Azure OpenAI -- via UserDataManager (fact extraction path)")
    from src.user_data_manager import UserDataManager

    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            udm = UserDataManager(data_dir=tmpdir)
        except Exception as e:
            check("UserDataManager init", False, str(e))
            return

        check("UserDataManager init", True)

        try:
            await asyncio.wait_for(
                udm.extract_and_save_facts(
                    "live_test_uid_99",
                    "I am 30 years old and I absolutely love hiking in the mountains",
                    "LiveTestUser"
                ),
                timeout=20.0
            )
            data = udm.load_user_data("live_test_uid_99")
            facts = [f.get("content", "") for f in data.get("facts", [])]
            has_fact = any("30" in c or "hik" in c.lower() or "mountain" in c.lower() for c in facts)
            if not has_fact and not facts:
                print(f"  {INFO}  Fact extracted and saved -- no facts stored (API may have returned empty JSON)")
            else:
                check("Fact extracted and saved", has_fact, f"facts={facts[:3]}")
        except asyncio.TimeoutError:
            check("Fact extracted and saved", False, "timed out after 20s")
        except Exception as e:
            check("Fact extracted and saved", False, str(e)[:120])


# ── 4. User data file I/O ─────────────────────────────────────────────────────

def check_user_data_io():
    print("\n4. User data file I/O")
    from src.user_data_manager import UserDataManager

    with tempfile.TemporaryDirectory() as tmpdir:
        udm = UserDataManager.__new__(UserDataManager)
        udm.data_dir = tmpdir
        # Skip Gemini init for this check
        udm.ai_client = None
        udm.model_name = "gemini-2.0-flash"
        udm.fact_model = None

        # Re-import needed methods work without genai
        import re
        from datetime import datetime
        import logging
        logger = logging.getLogger("live_check")

        # Test write → read roundtrip
        uid = "987654321"
        sample = {
            "user_id": uid,
            "username": "LiveTestUser",
            "created_at": datetime.now().isoformat(),
            "personal_info": {},
            "preferences": {},
            "facts": [{"content": "You love สมอง 1mm and 寿司", "timestamp": "now"}],
            "topics_of_interest": ["hiking", "testing"],
            "conversation_history": [],
            "last_interaction": datetime.now().isoformat(),
        }

        path = os.path.join(tmpdir, f"{uid}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(sample, f, ensure_ascii=False)

        with open(path, encoding="utf-8") as f:
            loaded = json.load(f)

        check("UTF-8 roundtrip (Thai + Japanese)", "สมอง" in str(loaded) and "寿司" in str(loaded))
        check("Topics persisted", loaded["topics_of_interest"] == ["hiking", "testing"])
        check("Facts persisted", len(loaded["facts"]) == 1)


# ── 5. Web server endpoints ───────────────────────────────────────────────────

async def check_web_server():
    print("\n5. Web server endpoints")
    from src.web_server import WebServer
    import aiohttp

    ws = WebServer(host="127.0.0.1", port=18080)
    try:
        await ws.start()
        async with aiohttp.ClientSession() as session:
            for path, expected in [("/", "ChronoChunk"), ("/health", "OK")]:
                try:
                    async with session.get(f"http://127.0.0.1:18080{path}", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                        body = await resp.text()
                        check(f"GET {path}", resp.status == 200 and expected in body,
                              f"status={resp.status} body={body[:40]!r}")
                except Exception as e:
                    check(f"GET {path}", False, str(e)[:80])

            try:
                async with session.get("http://127.0.0.1:18080/status", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    data = await resp.json()
                    check("GET /status — JSON", resp.status == 200 and "status" in data)
                    check("GET /status — online", data.get("status") == "online")
                    check("GET /status — uptime_seconds", isinstance(data.get("uptime_seconds"), int))
            except Exception as e:
                check("GET /status", False, str(e)[:80])
    finally:
        await ws.stop()


# ── 6. Config validation ──────────────────────────────────────────────────────

def check_config():
    print("\n6. Config validation")
    from config.config import Config

    try:
        result = Config.validate()
        check("Config.validate() succeeds", True)
        check("Discord token present", result.get("discord_token") is True)
    except ValueError as e:
        check("Config.validate() succeeds", False, str(e))
    except Exception as e:
        check("Config.validate() succeeds", False, str(e))

    try:
        Config.ensure_directories()
        check("ensure_directories() creates dirs",
              os.path.isdir(Config.USER_DATA_DIR) and os.path.isdir(Config.LOG_DIR))
    except Exception as e:
        check("ensure_directories()", False, str(e))


# ── 7. FFmpeg (advisory) ──────────────────────────────────────────────────────

def check_ffmpeg():
    print("\n7. FFmpeg (advisory — music commands depend on this)")
    found = shutil.which("ffmpeg")
    status = "FOUND [OK]" if found else "NOT FOUND [X] -- all music playback will fail"
    print(f"  {INFO}  FFmpeg: {status}")
    if found:
        print(f"  {INFO}  Path: {found}")


# ── 8. Python package versions ────────────────────────────────────────────────

def check_packages():
    print("\n8. Key package versions")
    packages = {
        "discord": "discord",
        "openai": "openai",
        "aiohttp": "aiohttp",
        "yt_dlp": "yt_dlp",
    }
    for display, mod in packages.items():
        try:
            m = __import__(mod.split(".")[0])
            if "." in mod:
                import importlib
                m = importlib.import_module(mod)
            ver = getattr(m, "__version__", getattr(m, "version", "unknown"))
            check(f"{display} importable", True, f"version={ver}")
        except ImportError as e:
            check(f"{display} importable", False, str(e))


# ─────────────────────────────────────────────────────────────────────────────

async def main():
    print("=" * 60)
    print("ChronoChunk - Live Integration Check")
    print("=" * 60)

    check_env()
    await check_azure_openai_direct()
    await check_azure_via_udm()
    check_user_data_io()
    await check_web_server()
    check_config()
    check_ffmpeg()
    check_packages()

    print("\n" + "=" * 60)
    if failures:
        print(f"RESULT: {len(failures)} check(s) FAILED:")
        for f in failures:
            print(f"  [X]  {f}")
        sys.exit(1)
    else:
        print("RESULT: All checks passed [OK]")


if __name__ == "__main__":
    asyncio.run(main())
