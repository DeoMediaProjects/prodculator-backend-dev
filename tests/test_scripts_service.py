from app.modules.scripts.service import ScriptAnalysisService


def test_parse_json_payload_from_fenced_json():
    raw = """```json
{"locations":[{"name":"Lagos"}],"budgetEstimate":{"range":"low"}}
```"""
    data = ScriptAnalysisService._parse_json_payload(raw)
    assert data["locations"][0]["name"] == "Lagos"


def test_parse_json_payload_from_mixed_text():
    raw = """
Here is the analysis:

{
  "locations": [{"name": "London"}],
  "budgetEstimate": {"range": "medium"}
}

Let me know if you need more.
"""
    data = ScriptAnalysisService._parse_json_payload(raw)
    assert data["locations"][0]["name"] == "London"

