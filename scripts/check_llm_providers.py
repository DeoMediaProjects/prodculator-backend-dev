"""Exercise every configured LLM provider with a real generation.

Run this after adding or rotating a key (`python -m scripts.check_llm_providers`).
It goes further than the /health config flags and the admin data-source test,
which only confirm a key is present or that the models endpoint answers: this
sends an actual prompt through the same service code a report uses, so it also
catches a model id the account cannot access and a Gemini thinking budget that
swallows the whole answer.

Read-only and cheap — one tiny generation per provider.
"""
from app.core.config import get_settings
from app.modules.scripts.service import ScriptAnalysisService

PROMPT = "Reply with exactly this JSON and nothing else: {\"ok\": true}"

settings = get_settings()
service = ScriptAnalysisService(settings)

print("Anthropic model:", settings.ANTHROPIC_MODEL or "(not configured)")
print("Fallback chain:", " -> ".join(service._fallback_chain()) or "(none configured)")
print()

targets: list[tuple[str, str]] = []
if settings.ANTHROPIC_API_KEY:
    targets.append(("anthropic", settings.ANTHROPIC_MODEL))
targets.extend((name, service._provider_model(name)) for name in service._fallback_chain())

if not targets:
    raise SystemExit("No provider is configured — set ANTHROPIC_API_KEY, OPENAI_API_KEY or GEMINI_API_KEY.")

failures = 0
for provider, model in targets:
    # ASCII only: this runs in a Windows console (cp1252) as often as in Docker.
    print(f"[{provider}] {model}")
    try:
        if provider == "anthropic":
            response = service._call_anthropic_with_retry(
                system_prompt="You are a JSON emitter.",
                user_content=PROMPT,
                temperature=0.0,
                stage="script_chunk",
            )
        else:
            response = service._call_fallback_provider(
                provider,
                system_prompt="You are a JSON emitter.",
                user_content=PROMPT,
                temperature=0.0,
                stage="script_chunk",
            )
    except Exception as exc:
        failures += 1
        print(f"   FAILED  {type(exc).__name__}: {exc}")
        print(f"   treated as provider-unavailable: {service._is_provider_unavailable_error(exc)}")
        continue

    text = service._extract_text_response(response).strip()
    usage = getattr(response, "usage", None)
    if not text:
        failures += 1
        print(f"   FAILED  empty body (stop_reason={getattr(response, 'stop_reason', None)})")
        if provider == "gemini":
            print("   the answer budget was probably spent on thought tokens: "
                  "raise GEMINI_MAX_TOKENS_MULTIPLIER or lower GEMINI_THINKING_LEVEL")
        continue

    print(f"   OK  stop_reason={getattr(response, 'stop_reason', None)} "
          f"in={getattr(usage, 'input_tokens', None)} out={getattr(usage, 'output_tokens', None)}")
    print(f"   {text[:200]}")

print()
raise SystemExit(failures)
