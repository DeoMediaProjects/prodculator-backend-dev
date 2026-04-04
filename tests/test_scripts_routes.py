from app.core.dependencies import get_current_user, get_supabase
from app.modules.scripts.router import get_script_service


class FakeStorageBucket:
    def __init__(self):
        self.uploaded = {}

    def upload(self, path, payload, options):
        self.uploaded[path] = {"payload": payload, "options": options}
        return {"path": path}


class FakeStorage:
    def __init__(self):
        self.bucket = FakeStorageBucket()

    def from_(self, name):
        return self.bucket


class FakeSupabase:
    def __init__(self):
        self.storage = FakeStorage()


class FakeScriptService:
    @staticmethod
    def validate_file(filename: str, file_size: int):
        return True, None

    @staticmethod
    def extract_text(filename: str, file_bytes: bytes):
        return "INT. ROOM - DAY"

    @staticmethod
    def analyze(script_content: str, script_title: str):
        return {
            "locations": [],
            "budgetEstimate": {
                "range": "low",
                "minUSD": 500000,
                "maxUSD": 5000000,
                "confidence": 0.8,
                "indicators": ["test"],
            },
            "productionScale": {
                "crewSize": "small",
                "principalCast": "small",
                "supportingCast": "small",
                "backgroundExtras": "small",
                "estimatedShootingDays": 10,
            },
            "equipment": {
                "cameraEquipment": "arri",
                "specialEquipment": [],
                "vfxRequirements": "minimal",
            },
            "metadata": {
                "genres": ["Drama"],
                "format": "feature",
                "tone": "test",
                "targetAudience": "test",
            },
            "challenges": {
                "weatherDependent": False,
                "historicalPeriod": False,
                "specialPermits": False,
                "stunts": False,
                "animalWrangling": False,
                "waterWork": False,
                "nightShooting": False,
                "notes": [],
            },
            "rawResponse": "{}",
        }


def test_validate_rejects_invalid_extension(client):
    response = client.post(
        "/api/scripts/validate",
        files={"file": ("malware.exe", b"bad", "application/octet-stream")},
    )
    assert response.status_code == 200
    assert response.json()["valid"] is False


def test_upload_rejects_invalid_file_type(client, auth_user):
    client.app.dependency_overrides[get_current_user] = lambda: auth_user
    client.app.dependency_overrides[get_supabase] = lambda: FakeSupabase()
    response = client.post(
        "/api/scripts/analyze",
        headers={"Authorization": "Bearer token"},
        files={"file": ("script.exe", b"bad", "application/octet-stream")},
    )
    assert response.status_code == 400


def test_analyze_script_success(client, auth_user):
    client.app.dependency_overrides[get_current_user] = lambda: auth_user
    client.app.dependency_overrides[get_script_service] = lambda: FakeScriptService()
    response = client.post(
        "/api/scripts/analyze",
        headers={"Authorization": "Bearer token"},
        files={"file": ("script.txt", b"INT. ROOM - DAY", "text/plain")},
    )
    assert response.status_code == 200
    assert response.json()["budgetEstimate"]["range"] == "low"
