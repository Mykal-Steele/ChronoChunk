"""
The Cloudflare Tunnel that puts the Uniloom sign-in pages on https://chrono.oakar.dev
(docs/uniloom-plan.md, section 4). Only those two pages are reachable from the internet.
"""
import re


def _compose() -> str:
    with open("deployment/docker-compose.yml") as f:
        return f.read()


def _service(name: str) -> str:
    """One service's block of the compose file: from its name to the next service or the end."""
    found = re.search(rf"^  {name}:\n(.*?)(?=^  \w[\w-]*:\n|\Z)", _compose(), re.MULTILINE | re.DOTALL)
    assert found, f"no {name} service"
    return found.group(1)


def _setup() -> str:
    with open("deployment/tunnel-setup.sh") as f:
        return f.read()


def _image() -> str:
    found = re.search(r"^\s+image: (cloudflare/cloudflared:\S+)$", _service("tunnel"), re.MULTILINE)
    assert found
    return found.group(1)


def test_the_bot_knows_its_public_address():
    assert re.search(r"^\s+PUBLIC_URL: https://chrono\.oakar\.dev$", _service("bot"), re.MULTILINE)


def test_the_tunnel_runs_a_pinned_cloudflared_with_nothing_extra():
    tunnel = _service("tunnel")
    assert re.fullmatch(r"cloudflare/cloudflared:\d{4}\.\d+\.\d+", _image())
    assert re.search(r"cap_drop:\n\s+- ALL", tunnel) and "no-new-privileges:true" in tunnel
    # It reads its credentials and never writes them
    assert re.search(r"- \./data/cloudflared:/etc/cloudflared:ro", tunnel)
    # No port on the VM: the tunnel connects out to Cloudflare
    assert "ports:" not in _compose()


def test_the_tunnel_and_setup_use_the_same_cloudflared():
    assert f"IMAGE={_image()}" in _setup()


def test_only_the_sign_in_pages_go_through_the_tunnel():
    text = _setup()
    assert r"path: ^/oauth/(callback|client\.json)$" in text
    assert "service: http://bot:8080" in text
    # Everything else, such as /status, gets a 404 at Cloudflare
    assert re.search(r"- service: http_status:404\s*$", text.split("EOF")[1].strip() + "\n", re.MULTILINE)


def test_the_account_wide_certificate_is_deleted_after_setup():
    # cert.pem can manage every tunnel and DNS record of the account. Running the tunnel needs
    # only its own credentials.json
    assert 'rm -f "$DIR/cert.pem"' in _setup()
