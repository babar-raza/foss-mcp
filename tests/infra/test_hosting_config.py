"""Tests for infra/hosting/{nginx.conf,compose.yaml,pilots.txt} (plan / TC-685).

RT2 (runtime/contract sub-program, section 2.7): the nginx-gateway +
cloudflared-tunnel layer that sits in front of the 32-pilot demo fleet is
versioned here, verbatim, from the real operator-local hosting config. These
tests are real-content regression tests, not mere existence checks: they
pin the literal diagnostic directives that corroborate RT1's tail-latency
finding (a single nginx worker, per-request DNS re-resolution, and a
non-poolable variable `proxy_pass`), so a future accidental edit to the
committed config that silently drops one of those facts fails loudly rather
than drifting. They also guard against pilots.txt and compose.yaml's pilot
service set silently diverging from each other.

All tests are offline: no docker, no network, no subprocess.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HOSTING_DIR = REPO_ROOT / "infra" / "hosting"
NGINX_CONF = HOSTING_DIR / "nginx.conf"
COMPOSE_YAML = HOSTING_DIR / "compose.yaml"
PILOTS_TXT = HOSTING_DIR / "pilots.txt"

# A pilot identifier is `family/platform`: two lowercase-alnum segments
# separated by a single slash.
PILOT_RE = re.compile(r"^[a-z0-9]+/[a-z0-9]+$")

EXPECTED_PILOT_SERVICE_COUNT = 32
EXPECTED_TOTAL_SERVICE_COUNT = 34  # 32 pilots + mcp-gateway + mcp-tunnel


def _load_pilots() -> list[str]:
    text = PILOTS_TXT.read_text(encoding="utf-8")
    return [line.strip() for line in text.splitlines() if line.strip()]


def _load_compose() -> dict:
    with COMPOSE_YAML.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def test_nginx_conf_contains_the_three_real_diagnostic_directives():
    """Pin the literal diagnostic facts that explain the gateway's tail latency.

    These three facts (one worker, per-request DNS re-resolution via a short
    resolver TTL, and a variable proxy_pass that defeats connection pooling)
    are the real, read evidence this card's purpose text records for RT4's
    future fix. The negative control mutates the first of these away from
    its real operator-local value to prove this test reads committed file
    content, not a hardcoded expectation.
    """
    content = NGINX_CONF.read_text(encoding="utf-8")

    assert "worker_processes 1;" in content, "nginx.conf must pin the real single-worker directive"
    assert "resolver 127.0.0.11 valid=10s" in content, (
        "nginx.conf must pin the real short-TTL resolver directive "
        "(per-request DNS re-resolution of the upstream container name)"
    )
    # A *variable* proxy_pass target (built from a request-path capture
    # group, not a static upstream name) is what defeats nginx's
    # connection pooling/keepalive for the upstream.
    assert re.search(r"proxy_pass\s+http://mcp-\$pilot:", content), (
        "nginx.conf must pin the real variable proxy_pass pattern "
        "(http://mcp-$pilot:...), not a static upstream"
    )


def test_compose_yaml_parses_and_defines_exactly_34_services():
    """compose.yaml is valid YAML with 32 pilot services + mcp-gateway + mcp-tunnel."""
    compose = _load_compose()

    assert isinstance(compose, dict)
    services = compose.get("services")
    assert isinstance(services, dict), "compose.yaml must have a top-level 'services' map"

    assert len(services) == EXPECTED_TOTAL_SERVICE_COUNT, (
        f"expected exactly {EXPECTED_TOTAL_SERVICE_COUNT} services "
        f"(32 pilots + mcp-gateway + mcp-tunnel), got {len(services)}: "
        f"{sorted(services)}"
    )

    assert "mcp-gateway" in services
    assert "mcp-tunnel" in services

    pilot_services = {
        name: svc for name, svc in services.items() if name not in ("mcp-gateway", "mcp-tunnel")
    }
    assert len(pilot_services) == EXPECTED_PILOT_SERVICE_COUNT

    known_pilots = set(_load_pilots())

    for name, svc in pilot_services.items():
        env = svc.get("environment")
        assert isinstance(env, dict), f"service {name!r} must have an 'environment' map"
        pilot_value = env.get("FOSS_MCP_PILOT")
        assert isinstance(pilot_value, str), f"service {name!r} must set FOSS_MCP_PILOT to a string"
        assert PILOT_RE.match(pilot_value), (
            f"service {name!r}'s FOSS_MCP_PILOT value {pilot_value!r} is not a valid family/platform string"
        )
        assert pilot_value in known_pilots, (
            f"service {name!r}'s FOSS_MCP_PILOT value {pilot_value!r} "
            "does not appear in infra/hosting/pilots.txt"
        )


def test_gateway_mounts_nginx_conf_and_tunnel_depends_on_gateway_anonymously():
    """mcp-gateway mounts nginx.conf read-only; mcp-tunnel is the anonymous quick tunnel."""
    compose = _load_compose()
    services = compose["services"]

    gateway = services["mcp-gateway"]
    volumes = gateway.get("volumes") or []
    assert any(
        isinstance(v, str) and v.startswith("./nginx.conf:") and v.rstrip().endswith(":ro") for v in volumes
    ), f"mcp-gateway must mount ./nginx.conf read-only, got volumes={volumes!r}"

    tunnel = services["mcp-tunnel"]
    depends_on = tunnel.get("depends_on") or []
    assert "mcp-gateway" in depends_on, "mcp-tunnel must depend_on mcp-gateway"

    command = tunnel.get("command")
    assert isinstance(command, str), "mcp-tunnel must have a string 'command'"
    assert "tunnel" in command
    assert "--url" in command, "mcp-tunnel's command must use the anonymous --url quick tunnel"
    # The anonymous quick-tunnel invocation never carries a token/credential flag.
    assert "--token" not in command, (
        "mcp-tunnel's command must stay the anonymous, no-account quick tunnel (no --token flag)"
    )


def test_pilots_txt_and_compose_pilot_services_match_exactly():
    """pilots.txt and compose.yaml's pilot services have exactly matching sets.

    This is the card's own lasting regression guard: a future pilot added to
    one file but not the other must fail loudly here, not drift silently.
    """
    pilots_from_file = set(_load_pilots())
    assert len(pilots_from_file) == EXPECTED_PILOT_SERVICE_COUNT, (
        f"expected exactly {EXPECTED_PILOT_SERVICE_COUNT} pilots in pilots.txt, got {len(pilots_from_file)}"
    )

    compose = _load_compose()
    services = compose["services"]
    pilots_from_compose = {
        svc["environment"]["FOSS_MCP_PILOT"]
        for name, svc in services.items()
        if name not in ("mcp-gateway", "mcp-tunnel")
    }

    symmetric_difference = pilots_from_file ^ pilots_from_compose
    assert symmetric_difference == set(), (
        "pilots.txt and compose.yaml pilot services must match exactly; "
        f"mismatched entries: {sorted(symmetric_difference)}"
    )


# --- TC-833: two-segment /{family}/{platform}/ routing ------------------------

# One two-segment location per pilot, e.g.
#   location ~ ^/3d/java(?:/(?<rest>mcp/?)?)?$ { set $pilot 3d-java; ... }
TWO_SEGMENT_LOCATION_RE = re.compile(
    r"^\s*location\s+~\s+(?P<regex>\^/(?P<family>[a-z0-9]+)/(?P<platform>[a-z0-9]+)\S*\$)\s*\{(?P<body>[^}]*)\}",
    re.MULTILINE,
)
HYPHENATED_LOCATION_RE = re.compile(
    r"^\s*location\s+~\s+(?P<regex>\^/\(\?<pilot>\[a-z0-9\]\+-\[a-z0-9\]\+\)/\S*\$)\s*\{(?P<body>[^}]*)\}",
    re.MULTILINE,
)
SET_PILOT_RE = re.compile(r"set\s+\$pilot\s+(?P<pilot>[a-z0-9]+-[a-z0-9]+)\s*;")
PROXY_PASS_RE = re.compile(r"proxy_pass\s+(?P<target>\S+)\s*;")
HOST_HEADER_RE = re.compile(r"proxy_set_header\s+Host\s+(?P<host>\S+)\s*;")


def _two_segment_locations() -> dict[str, dict[str, str]]:
    content = NGINX_CONF.read_text(encoding="utf-8")
    found: dict[str, dict[str, str]] = {}
    for m in TWO_SEGMENT_LOCATION_RE.finditer(content):
        pilot = f"{m.group('family')}/{m.group('platform')}"
        assert pilot not in found, f"duplicate two-segment location for {pilot}"
        body = m.group("body")
        set_m = SET_PILOT_RE.search(body)
        pass_m = PROXY_PASS_RE.search(body)
        host_m = HOST_HEADER_RE.search(body)
        assert set_m and pass_m and host_m, f"two-segment location for {pilot} is incomplete: {body!r}"
        found[pilot] = {
            "regex": m.group("regex"),
            "pilot_var": set_m.group("pilot"),
            "proxy_pass": pass_m.group("target"),
            "host": host_m.group("host"),
        }
    return found


def _hyphenated_location() -> dict[str, str]:
    content = NGINX_CONF.read_text(encoding="utf-8")
    m = HYPHENATED_LOCATION_RE.search(content)
    assert m, "the hyphenated /{family}-{platform}/ location must still be present"
    body = m.group("body")
    pass_m = PROXY_PASS_RE.search(body)
    host_m = HOST_HEADER_RE.search(body)
    assert pass_m and host_m
    return {"proxy_pass": pass_m.group("target"), "host": host_m.group("host")}


def _nginx_regex_to_python(regex: str) -> re.Pattern[str]:
    # nginx (PCRE) named groups `(?<name>` -> Python `(?P<name>`.
    return re.compile(re.sub(r"\(\?<([a-z]+)>", r"(?P<\1>", regex))


def test_every_pilot_has_both_location_forms():
    pilots = _load_pilots()
    two_segment = _two_segment_locations()
    assert set(two_segment) == set(pilots), (
        "every pilot in pilots.txt needs a two-segment location and no others; "
        f"missing={sorted(set(pilots) - set(two_segment))}, extra={sorted(set(two_segment) - set(pilots))}"
    )
    # The hyphenated form is the generic regex location; it must still serve every
    # `family-platform` pilot identifier.
    hyphenated = _hyphenated_location()
    assert hyphenated["proxy_pass"].startswith("http://mcp-$pilot:8080/")
    content = NGINX_CONF.read_text(encoding="utf-8")
    hy = _nginx_regex_to_python(HYPHENATED_LOCATION_RE.search(content).group("regex"))
    for pilot in pilots:
        hyphenated_id = pilot.replace("/", "-")
        m = hy.match(f"/{hyphenated_id}/mcp")
        assert m and m.group("pilot") == hyphenated_id, f"hyphenated form does not route {hyphenated_id}"


def test_both_forms_resolve_to_the_same_upstream():
    hyphenated = _hyphenated_location()
    for pilot, loc in _two_segment_locations().items():
        family, platform = pilot.split("/")
        assert loc["pilot_var"] == f"{family}-{platform}", f"{pilot}: wrong $pilot value {loc['pilot_var']!r}"
        # Identical proxy settings to the hyphenated location.
        assert loc["proxy_pass"] == hyphenated["proxy_pass"], pilot
        assert loc["host"] == hyphenated["host"], pilot
        # ...and with $pilot = family-platform that is the compose service/container name.
        assert (
            loc["proxy_pass"]
            .replace("$pilot", loc["pilot_var"])
            .startswith(f"http://mcp-{family}-{platform}:8080/")
        )
    compose_services = set(_load_compose()["services"])
    for pilot in _load_pilots():
        assert f"mcp-{pilot.replace('/', '-')}" in compose_services


def test_two_segment_form_cannot_shadow_another_pilot_and_family_alone_is_not_routed():
    pilots = _load_pilots()
    assert len(pilots) == len(set(pilots)), "pilots.txt family/platform pairs must be unique"
    # Hyphenated ids must be unique too, or two pilots would share one upstream.
    assert len({p.replace("/", "-") for p in pilots}) == len(pilots)

    patterns = {p: _nginx_regex_to_python(loc["regex"]) for p, loc in _two_segment_locations().items()}
    for pilot in pilots:
        for path in (f"/{pilot}", f"/{pilot}/", f"/{pilot}/mcp", f"/{pilot}/mcp/"):
            matching = [p for p, rx in patterns.items() if rx.match(path)]
            assert matching == [pilot], f"{path} must be routed to exactly {pilot}, got {matching}"
        family, platform = pilot.split("/")
        for path in (
            f"/{family}",
            f"/{family}/",
            f"/{family}/{platform}x/mcp",
            f"/{family}/{platform}/mcp/x",
        ):
            if path.rstrip("/") == f"/{pilot}":
                continue
            assert not any(rx.match(path) for rx in patterns.values()), f"{path} must not be routed"
    for path in ("/3d/", "/3d", "/cells/", "/pdf/", "/unknown/pilot/mcp", "/"):
        assert not any(rx.match(path) for rx in patterns.values()), f"{path} must not be routed"


def test_two_segment_locations_do_not_overlap_the_hyphenated_location():
    content = NGINX_CONF.read_text(encoding="utf-8")
    hy = _nginx_regex_to_python(HYPHENATED_LOCATION_RE.search(content).group("regex"))
    for pilot in _load_pilots():
        for path in (f"/{pilot}", f"/{pilot}/", f"/{pilot}/mcp"):
            assert not hy.match(path), f"hyphenated location would shadow two-segment {path}"


def test_no_location_exposes_metrics_or_readiness_paths():
    content = NGINX_CONF.read_text(encoding="utf-8")
    for pilot, loc in _two_segment_locations().items():
        rx = _nginx_regex_to_python(loc["regex"])
        for tail in ("metrics", "readyz", "healthz", "livez", "mcp/../metrics"):
            assert not rx.match(f"/{pilot}/{tail}"), f"/{pilot}/{tail} must not be routed"
    # The nginx config itself never names an exposure location for them.
    assert not re.search(r"^\s*location[^{\n]*(metrics|readyz|healthz|livez)", content, re.MULTILINE)
    # The only exact-match location is the root banner.
    assert re.findall(r"location\s+=\s+(\S+)", content) == ["/"]


def test_nginx_conf_remains_structurally_balanced_and_keeps_shared_proxy_settings():
    content = NGINX_CONF.read_text(encoding="utf-8")
    assert content.count("{") == content.count("}"), "nginx.conf braces must balance"
    assert re.search(r"^\s*proxy_http_version 1\.1;", content, re.MULTILINE)
    assert re.search(r'^\s*proxy_set_header Connection "";', content, re.MULTILINE)
    assert re.search(r"^\s*proxy_buffering off;", content, re.MULTILINE)
    assert re.search(r"^\s*proxy_read_timeout 3600s;", content, re.MULTILINE)
    # The two-segment locations must not override the shared proxy settings.
    for m in TWO_SEGMENT_LOCATION_RE.finditer(content):
        for directive in ("proxy_http_version", "proxy_buffering", "proxy_read_timeout", "Connection"):
            assert directive not in m.group("body")
    # Hyphenated location is unchanged.
    assert "location ~ ^/(?<pilot>[a-z0-9]+-[a-z0-9]+)/(?<rest>.*)$ {" in content
    assert "proxy_pass http://mcp-$pilot:8080/$rest$is_args$args;" in content
