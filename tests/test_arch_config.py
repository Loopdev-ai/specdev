import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MOD_PATH = ROOT / "assets" / "specdev" / "tools" / "arch_config.py"
SEED_PATH = ROOT / "assets" / "specdev" / "architecture-config.json"


def load_mod():
    spec = importlib.util.spec_from_file_location("arch_config", MOD_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ac = load_mod()


def seed_doc():
    return {
        "schema_version": ac.SCHEMA_VERSION,
        "environments": {
            "production": {cat: [] for cat in ac.CATEGORIES},
        },
    }


def write_seed(root):
    (root / ".specdev").mkdir(parents=True, exist_ok=True)
    (root / ".specdev" / "architecture-config.json").write_text(
        json.dumps(seed_doc(), indent=2) + "\n", encoding="utf-8"
    )


def test_categories_are_the_ten_expected():
    assert set(ac.CATEGORIES) == {
        "cloud_tenants", "network_ranges", "databases", "app_servers",
        "service_endpoints", "api_endpoints", "service_accounts",
        "app_registrations", "key_vaults", "storage_accounts",
    }


def test_seed_file_matches_schema_shape():
    seed = json.loads(SEED_PATH.read_text())
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


def _doc_with(category, record):
    d = seed_doc()
    d["environments"]["production"][category].append(record)
    return d


def test_valid_seed_passes():
    assert ac.validate_doc(seed_doc()) == []


def test_wrong_schema_version_fails():
    d = seed_doc()
    d["schema_version"] = 2
    assert any("schema_version" in e for e in ac.validate_doc(d))


def test_environments_must_be_object():
    assert any("environments" in e for e in ac.validate_doc({"schema_version": 1, "environments": []}))


def test_unknown_category_fails():
    d = seed_doc()
    d["environments"]["production"]["bogus"] = []
    assert any("unknown category" in e for e in ac.validate_doc(d))


def test_missing_required_field_fails():
    d = _doc_with("key_vaults", {"name": "kv"})  # missing cloud, vault_uri
    errs = ac.validate_doc(d)
    assert any("cloud" in e for e in errs)
    assert any("vault_uri" in e for e in errs)


def test_unknown_field_fails():
    d = _doc_with("key_vaults", {"name": "kv", "cloud": "azure", "vault_uri": "https://x", "wat": 1})
    assert any("unknown field 'wat'" in e for e in ac.validate_doc(d))


def test_duplicate_name_fails():
    d = seed_doc()
    d["environments"]["production"]["app_servers"] = [
        {"name": "a", "hostname": "h1"}, {"name": "a", "hostname": "h2"},
    ]
    assert any("duplicate name" in e for e in ac.validate_doc(d))


def test_two_defaults_in_category_fail():
    d = seed_doc()
    d["environments"]["production"]["key_vaults"] = [
        {"name": "kv1", "cloud": "azure", "vault_uri": "https://1", "default": True},
        {"name": "kv2", "cloud": "azure", "vault_uri": "https://2", "default": True},
    ]
    assert any("default" in e for e in ac.validate_doc(d))


def test_defaults_in_different_envs_are_independent():
    d = seed_doc()
    d["environments"]["staging"] = {cat: [] for cat in ac.CATEGORIES}
    for env in ("production", "staging"):
        d["environments"][env]["key_vaults"] = [
            {"name": "kv", "cloud": "azure", "vault_uri": "https://x", "default": True}
        ]
    assert ac.validate_doc(d) == []


def _kv(name="core-kv", default=False):
    r = {"name": name, "cloud": "azure", "vault_uri": f"https://{name}.vault.azure.net"}
    if default:
        r["default"] = True
    return r


def test_secret_ref_key_vault_resolves():
    d = seed_doc()
    p = d["environments"]["production"]
    p["key_vaults"].append(_kv())
    p["databases"].append({
        "name": "db", "engine": "postgres", "host": "h", "port": 5432, "database": "app",
        "secret_ref": {"provider": "key_vault", "vault": "core-kv", "secret_name": "db-pw"},
    })
    assert ac.validate_doc(d) == []


def test_secret_ref_dangling_vault_fails():
    d = seed_doc()
    d["environments"]["production"]["databases"].append({
        "name": "db", "engine": "postgres", "host": "h", "port": 5432, "database": "app",
        "secret_ref": {"provider": "key_vault", "vault": "ghost", "secret_name": "x"},
    })
    assert any("no matching key_vaults record" in e for e in ac.validate_doc(d))


def test_secret_ref_env_requires_env_var():
    d = seed_doc()
    d["environments"]["production"]["databases"].append({
        "name": "db", "engine": "postgres", "host": "h", "port": 5432, "database": "app",
        "secret_ref": {"provider": "env"},
    })
    assert any("env_var" in e for e in ac.validate_doc(d))


def test_secret_ref_unknown_provider_fails():
    d = seed_doc()
    d["environments"]["production"]["databases"].append({
        "name": "db", "engine": "postgres", "host": "h", "port": 5432, "database": "app",
        "secret_ref": {"provider": "smoke-signal", "x": "y"},
    })
    assert any("provider" in e for e in ac.validate_doc(d))


def test_secret_ref_must_be_object_not_string():
    d = seed_doc()
    d["environments"]["production"]["databases"].append({
        "name": "db", "engine": "postgres", "host": "h", "port": 5432, "database": "app",
        "secret_ref": "hunter2",
    })
    assert any("must be a secret_ref object" in e for e in ac.validate_doc(d))


def test_leak_guard_flags_literal_secret_in_plain_field():
    d = seed_doc()
    d["environments"]["production"]["databases"].append({
        "name": "db", "engine": "postgres", "host": "Password=hunter2", "port": 5432, "database": "app",
    })
    assert any("literal secret" in e for e in ac.validate_doc(d))


def test_leak_guard_flags_private_key():
    d = seed_doc()
    d["environments"]["production"]["app_servers"].append({
        "name": "s", "hostname": "-----BEGIN RSA PRIVATE KEY-----",
    })
    assert any("literal secret" in e for e in ac.validate_doc(d))
