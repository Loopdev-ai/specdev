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


def test_add_then_get_round_trip(tmp_path):
    write_seed(tmp_path)
    rc = ac.main([
        "--root", str(tmp_path), "add", "--env", "production", "--category", "key_vaults",
        "--name", "core-kv", "--set", "cloud=azure", "--set", "vault_uri=https://core.vault.azure.net",
        "--default",
    ])
    assert rc == 0
    doc = ac.load(tmp_path)
    rec = doc["environments"]["production"]["key_vaults"][0]
    assert rec["name"] == "core-kv" and rec["default"] is True


def test_add_coerces_port_to_int(tmp_path):
    write_seed(tmp_path)
    ac.main([
        "--root", str(tmp_path), "add", "--env", "production", "--category", "databases",
        "--name", "db", "--set", "engine=postgres", "--set", "host=h",
        "--set", "port=5432", "--set", "database=app",
    ])
    assert ac.load(tmp_path)["environments"]["production"]["databases"][0]["port"] == 5432


def test_add_with_secret_ref(tmp_path):
    write_seed(tmp_path)
    ac.main([
        "--root", str(tmp_path), "add", "--env", "production", "--category", "key_vaults",
        "--name", "core-kv", "--set", "cloud=azure", "--set", "vault_uri=https://x",
    ])
    rc = ac.main([
        "--root", str(tmp_path), "add", "--env", "production", "--category", "databases",
        "--name", "db", "--set", "engine=postgres", "--set", "host=h", "--set", "port=5432",
        "--set", "database=app",
        "--secret-ref", "provider=key_vault,vault=core-kv,secret_name=db-pw",
    ])
    assert rc == 0
    ref = ac.load(tmp_path)["environments"]["production"]["databases"][0]["secret_ref"]
    assert ref == {"provider": "key_vault", "vault": "core-kv", "secret_name": "db-pw"}


def test_add_duplicate_name_exits(tmp_path):
    write_seed(tmp_path)
    args = ["--root", str(tmp_path), "add", "--env", "production", "--category", "app_servers",
            "--name", "s", "--set", "hostname=h"]
    ac.main(args)
    with pytest.raises(SystemExit):
        ac.main(args)


def test_add_invalid_record_aborts_write(tmp_path):
    write_seed(tmp_path)
    before = ac.config_path(tmp_path).read_text()
    with pytest.raises(SystemExit):
        ac.main([  # dangling vault ref -> validation fails -> no write
            "--root", str(tmp_path), "add", "--env", "production", "--category", "databases",
            "--name", "db", "--set", "engine=postgres", "--set", "host=h", "--set", "port=5432",
            "--set", "database=app", "--secret-ref", "provider=key_vault,vault=ghost,secret_name=x",
        ])
    assert ac.config_path(tmp_path).read_text() == before  # unchanged


def test_add_env_and_list_envs(tmp_path, capsys):
    write_seed(tmp_path)
    ac.main(["--root", str(tmp_path), "add-env", "--env", "staging"])
    assert set(ac.load(tmp_path)["environments"]) == {"production", "staging"}
    ac.main(["--root", str(tmp_path), "list-envs"])
    out = capsys.readouterr().out
    assert "staging" in out and "production" in out


def test_list_shows_default_flag(tmp_path, capsys):
    write_seed(tmp_path)
    ac.main(["--root", str(tmp_path), "add", "--env", "production", "--category", "key_vaults",
             "--name", "core-kv", "--set", "cloud=azure", "--set", "vault_uri=https://x", "--default"])
    ac.main(["--root", str(tmp_path), "list", "--category", "key_vaults"])
    assert "*default" in capsys.readouterr().out


def test_same_name_different_env_independent(tmp_path):
    write_seed(tmp_path)
    ac.main(["--root", str(tmp_path), "add-env", "--env", "staging"])
    for env in ("production", "staging"):
        ac.main(["--root", str(tmp_path), "add", "--env", env, "--category", "app_servers",
                 "--name", "web", "--set", f"hostname={env}-web"])
    doc = ac.load(tmp_path)
    assert doc["environments"]["production"]["app_servers"][0]["hostname"] == "production-web"
    assert doc["environments"]["staging"]["app_servers"][0]["hostname"] == "staging-web"


def _seed_with_kv(tmp_path):
    write_seed(tmp_path)
    ac.main(["--root", str(tmp_path), "add", "--env", "production", "--category", "app_servers",
             "--name", "web", "--set", "hostname=h1"])


def test_edit_updates_field(tmp_path):
    _seed_with_kv(tmp_path)
    rc = ac.main(["--root", str(tmp_path), "edit", "--env", "production", "--category", "app_servers",
                  "--name", "web", "--set", "hostname=h2"])
    assert rc == 0
    assert ac.load(tmp_path)["environments"]["production"]["app_servers"][0]["hostname"] == "h2"


def test_edit_missing_record_exits(tmp_path):
    write_seed(tmp_path)
    with pytest.raises(SystemExit):
        ac.main(["--root", str(tmp_path), "edit", "--env", "production", "--category", "app_servers",
                 "--name", "ghost", "--set", "hostname=h"])


def test_edit_no_default_clears_flag(tmp_path):
    write_seed(tmp_path)
    ac.main(["--root", str(tmp_path), "add", "--env", "production", "--category", "key_vaults",
             "--name", "kv", "--set", "cloud=azure", "--set", "vault_uri=https://x", "--default"])
    ac.main(["--root", str(tmp_path), "edit", "--env", "production", "--category", "key_vaults",
             "--name", "kv", "--no-default"])
    assert "default" not in ac.load(tmp_path)["environments"]["production"]["key_vaults"][0]


def test_delete_removes_record(tmp_path):
    _seed_with_kv(tmp_path)
    rc = ac.main(["--root", str(tmp_path), "delete", "--env", "production", "--category", "app_servers",
                  "--name", "web"])
    assert rc == 0
    assert ac.load(tmp_path)["environments"]["production"]["app_servers"] == []


def test_delete_missing_exits(tmp_path):
    write_seed(tmp_path)
    with pytest.raises(SystemExit):
        ac.main(["--root", str(tmp_path), "delete", "--env", "production", "--category", "app_servers",
                 "--name", "ghost"])


def test_validate_command_passes_on_seed(tmp_path):
    write_seed(tmp_path)
    assert ac.main(["--root", str(tmp_path), "validate"]) == 0


def test_validate_command_fails_on_bad_doc(tmp_path):
    write_seed(tmp_path)
    doc = ac.load(tmp_path)
    doc["environments"]["production"]["key_vaults"].append({"name": "kv"})  # missing required
    ac.save(tmp_path, doc)
    assert ac.main(["--root", str(tmp_path), "validate"]) == 1


def test_secret_ref_vault_scoped_per_environment():
    # A vault catalogued only in staging must NOT satisfy a production ref.
    d = seed_doc()
    d["environments"]["staging"] = {cat: [] for cat in ac.CATEGORIES}
    d["environments"]["staging"]["key_vaults"].append(_kv())  # core-kv only in staging
    d["environments"]["production"]["databases"].append({
        "name": "db", "engine": "postgres", "host": "h", "port": 5432, "database": "app",
        "secret_ref": {"provider": "key_vault", "vault": "core-kv", "secret_name": "x"},
    })
    assert any("no matching key_vaults record" in e for e in ac.validate_doc(d))


def test_non_object_json_exits_cleanly(tmp_path):
    # A file that is valid JSON but not an object must die cleanly, not traceback.
    (tmp_path / ".specdev").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".specdev" / "architecture-config.json").write_text("[]", encoding="utf-8")
    with pytest.raises(SystemExit):
        ac.main(["--root", str(tmp_path), "validate"])


def test_malformed_set_exits_cleanly(tmp_path):
    write_seed(tmp_path)
    with pytest.raises(SystemExit):
        ac.main(["--root", str(tmp_path), "add", "--env", "production",
                 "--category", "app_servers", "--name", "s", "--set", "noequalssign"])


# --- M-B: leak guard catches bare (keyword-less) long secret blobs ---

def test_leak_guard_flags_bare_base64_blob():
    # An access key / connection secret pasted with no keyword= prefix.
    blob = "dGhpc2lzYVZlcnlMb25nQmFzZTY0RW5jb2RlZFNlY3JldEtleTEyMzQ1Njc4OTA="
    d = _doc_with("key_vaults", {
        "name": "kv", "cloud": "azure", "vault_uri": "https://x", "description": blob,
    })
    assert any("literal secret" in e for e in ac.validate_doc(d))


def test_leak_guard_flags_bare_hex_blob():
    d = _doc_with("app_servers", {"name": "s", "hostname": "h", "role": "a" * 64})
    assert any("literal secret" in e for e in ac.validate_doc(d))


def test_leak_guard_allows_realistic_values():
    # GUIDs, HTTPS vault/base URLs, and storage account names must NOT trip the guard.
    d = seed_doc()
    p = d["environments"]["production"]
    p["cloud_tenants"].append({
        "name": "t", "cloud": "azure",
        "tenant_id": "12345678-1234-1234-1234-123456789012",
        "account_id": "87654321-4321-4321-4321-210987654321",
        "default_region": "eastus",
    })
    p["key_vaults"].append({
        "name": "kv", "cloud": "azure",
        "vault_uri": "https://mycompany-core-kv.vault.azure.net/",
    })
    p["api_endpoints"].append({
        "name": "api", "base_url": "https://api.mycompany.example.com/v2/resources",
        "auth_type": "oauth",
    })
    p["storage_accounts"].append({
        "name": "st", "cloud": "azure",
        "account_name": "mycompanyprodstorage01", "kind": "blob",
    })
    p["app_registrations"].append({
        "name": "app", "cloud": "azure",
        "client_id": "abcdef01-2345-6789-abcd-ef0123456789",
        "tenant_id": "12345678-1234-1234-1234-123456789012",
        # Slash-joined ARM resource id must not read as a bare secret blob.
        "target_resource": "/subscriptions/aaaabbbbccccddddeeeeffff00001111/resourceGroups/rg/providers/Microsoft.KeyVault/vaults/kv",
    })
    assert ac.validate_doc(d) == []


# --- M-C: unknown --category is rejected on every command that names one ---

def test_get_unknown_category_exits(tmp_path):
    write_seed(tmp_path)
    with pytest.raises(SystemExit):
        ac.main(["--root", str(tmp_path), "get", "--env", "production",
                 "--category", "bogus", "--name", "x"])


def test_edit_unknown_category_exits(tmp_path):
    write_seed(tmp_path)
    with pytest.raises(SystemExit):
        ac.main(["--root", str(tmp_path), "edit", "--env", "production",
                 "--category", "bogus", "--name", "x", "--set", "a=b"])


def test_delete_unknown_category_exits(tmp_path):
    write_seed(tmp_path)
    with pytest.raises(SystemExit):
        ac.main(["--root", str(tmp_path), "delete", "--env", "production",
                 "--category", "bogus", "--name", "x"])


def test_list_unknown_category_exits(tmp_path):
    write_seed(tmp_path)
    with pytest.raises(SystemExit):
        ac.main(["--root", str(tmp_path), "list", "--category", "bogus"])
