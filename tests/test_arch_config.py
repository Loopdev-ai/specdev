import json
import sys
from pathlib import Path
import pytest

# Add the tools directory to sys.path
sys.path.insert(0, str(Path(__file__).parents[1] / "assets" / "specdev" / "tools"))
import arch_config as ac

MOD_PATH = Path(__file__)


def write_seed(tmp_path):
    """Helper to write seed datastore to tmp_path/.specdev/architecture-config.json"""
    config_dir = tmp_path / ".specdev"
    config_dir.mkdir(parents=True, exist_ok=True)
    seed = {
        "schema_version": ac.SCHEMA_VERSION,
        "environments": {
            "production": {cat: [] for cat in ac.CATEGORIES}
        }
    }
    (config_dir / "architecture-config.json").write_text(json.dumps(seed, indent=2))


def test_categories_are_the_ten_expected():
    assert set(ac.CATEGORIES) == {
        "cloud_tenants", "network_ranges", "databases", "app_servers",
        "service_endpoints", "api_endpoints", "service_accounts",
        "app_registrations", "key_vaults", "storage_accounts",
    }


def test_seed_file_matches_schema_shape():
    seed = json.loads(
        (MOD_PATH.parents[1] / "assets" / "specdev" / "architecture-config.json").read_text()
    )
    assert seed["schema_version"] == ac.SCHEMA_VERSION
    assert set(seed["environments"]["production"]) == set(ac.CATEGORIES)


def test_allowed_fields_includes_common_and_secret(tmp_path):
    fields = ac.allowed_fields("databases")
    assert {"name", "host", "secret_ref", "description", "default"} <= fields


def test_load_save_round_trip(tmp_path):
    write_seed(tmp_path)
    doc = ac.load(tmp_path)
    doc["environments"]["production"]["key_vaults"].append({"name": "kv"})
    ac.save(tmp_path, doc)
    again = ac.load(tmp_path)
    assert again["environments"]["production"]["key_vaults"][0]["name"] == "kv"


def test_load_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        ac.load(tmp_path)


def test_find_record(tmp_path):
    env_obj = {"key_vaults": [{"name": "kv"}]}
    assert ac.find_record(env_obj, "key_vaults", "kv")["name"] == "kv"
    assert ac.find_record(env_obj, "key_vaults", "nope") is None
