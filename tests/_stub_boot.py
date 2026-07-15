import sys, types

def _mod(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m

_mod("pdfplumber", open=lambda *a, **k: None)
_mod("anthropic", Anthropic=type("Anthropic", (), {}), APIError=type("APIError", (Exception,), {}))
_mod("stripe")
_mod("boto3", client=lambda *a, **k: None)
_mod("weasyprint", HTML=type("HTML", (), {}))
j = _mod("jinja2",
         TemplateNotFound=type("TemplateNotFound", (Exception,), {}),
         # Real pdf_service registers custom filters (env.filters[...] = fn), so the
         # stub needs a filters dict per instance.
         Environment=type("Environment", (), {"__init__": lambda self, *a, **k: setattr(self, "filters", {}),
                                              "get_template": lambda self, *a, **k: None}),
         FileSystemLoader=type("FileSystemLoader", (), {"__init__": lambda self, *a, **k: None}),
         select_autoescape=lambda *a, **k: None)
_mod("firebase_admin", initialize_app=lambda *a, **k: None, credentials=types.SimpleNamespace(Certificate=lambda *a, **k: None), auth=types.SimpleNamespace())
aps = _mod("apscheduler")
_mod("apscheduler.schedulers", )
_mod("apscheduler.schedulers.background", BackgroundScheduler=type("BackgroundScheduler", (), {}))
_mod("apscheduler.triggers", )
_mod("apscheduler.triggers.cron", CronTrigger=type("CronTrigger", (), {}))
_mod("slowapi", Limiter=type("Limiter", (), {"__init__": lambda self, *a, **k: None}),
     _rate_limit_exceeded_handler=lambda *a, **k: None)
_mod("slowapi.util", get_remote_address=lambda *a, **k: "0.0.0.0")
_mod("slowapi.errors", RateLimitExceeded=type("RateLimitExceeded", (Exception,), {}))
