"""Canary tests for the release-image record (REQ-G3-004, DECISION_LOG 2026-10-11).

The state machine must remember the final standalone image, and must refuse to remember the wrong one: an
image whose gate report belongs to a different image, a report that did not pass every pilot, a push with no
digest, or a tag reused for different content. Every refusal below is a way a "final image" could silently be
the wrong thing.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import gatecli as C
import gatectl as G
import pytest
import release as R

SHA = "a" * 40
IMAGE_ID = "sha256:" + "b" * 64
OTHER_ID = "sha256:" + "c" * 64
DIGEST = "sha256:" + "d" * 64
REF = "foss-mcp-serving:demo-aaaaaaa"
PILOTS = 3


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "REPO", tmp_path)
    monkeypatch.setattr(G, "EVIDENCE", tmp_path / "evidence")
    monkeypatch.setattr(R, "expected_pilot_count", lambda: PILOTS)
    monkeypatch.setattr(R, "expected_pilot_keys", lambda: {f"p{i}" for i in range(1, PILOTS + 1)})
    monkeypatch.setattr(R, "commit_problem", lambda sha: None)
    monkeypatch.setattr(
        R,
        "inspect_image",
        lambda ref: {
            "image_id": IMAGE_ID,
            "size_bytes": 52428800,
            "created": "2026-10-11T00:00:00Z",
            "source_commit": SHA,
        },
    )
    return tmp_path


def report(
    tmp_path: Path, *, image_id=IMAGE_ID, verdict="PASS", passed=PILOTS, total=PILOTS, name="summary.json"
) -> Path:
    doc = {
        "schema_version": 1,
        "image": REF,
        "image_id": image_id,
        "verdict": verdict,
        "pilots_total": total,
        "pilots_passed": passed,
        "budget": {"memory": "256m", "cpus": 0.5},
        "pilots": [
            {
                "key": f"p{i}",
                "status": "pass" if i <= passed else "fail",
                "peak_memory_mb": [97.5, 120.25, 80.0][i - 1] if i <= 3 else 1.0,
            }
            for i in range(1, total + 1)
        ],
    }
    p = tmp_path / name
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


def push_result(tmp_path: Path, *, digest=DIGEST, local_id=IMAGE_ID, name="push.json") -> Path:
    doc = {
        "image": REF,
        "pushed_reference": "registry.example.internal/foss-mcp-serving:demo-aaaaaaa",
        "digest": digest,
        "local_image_id": local_id,
        "timestamp": "2026-10-11T10:00:00Z",
    }
    p = tmp_path / name
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


def test_a_gate_tested_image_is_recorded_local_only_and_validates(env):
    doc, path = R.record(REF, report(env))
    assert doc["status"] == "LOCAL_ONLY" and doc["registry"] is None
    assert path == env / "evidence" / "release" / f"{R.stem_for(REF)}.json"
    assert R.problems() == []
    assert G.schema_errors(G.load_schema(R.SCHEMA), json.loads(path.read_text(encoding="utf-8"))) == []
    copied = env / doc["gate_report"]["file"]
    assert copied.is_file()
    assert hashlib.sha256(copied.read_bytes()).hexdigest() == doc["gate_report"]["sha256"]
    assert doc["gate_report"]["peak_memory_mb"] == 120.25 and doc["gate_report"]["memory_budget"] == "256m"


def test_state_rows_carry_the_image_and_validate_against_the_state_schema(env):
    R.record(REF, report(env), push_result(env))
    rows = R.state_rows()
    assert rows == [
        {
            "image": REF,
            "image_id": IMAGE_ID,
            "source_commit": SHA,
            "size_mb": 50.0,
            "status": "PUSHED",
            "gate_report": f"PASS {PILOTS}/{PILOTS}",
            "registry_reference": "registry.example.internal/foss-mcp-serving:demo-aaaaaaa",
            "registry_digest": DIGEST,
            "record": f"evidence/release/{R.stem_for(REF)}.json",
        }
    ]
    sub = G.load_schema("state.schema.json")["properties"]["release_images"]
    assert G.schema_errors(sub, rows) == []


def test_a_push_result_upgrades_the_record_and_a_later_plain_record_never_downgrades_it(env):
    R.record(REF, report(env))
    doc, _ = R.record(REF, report(env), push_result(env))
    assert doc["status"] == "PUSHED" and doc["registry"]["digest"] == DIGEST
    again, _ = R.record(REF, report(env))
    assert again["status"] == "PUSHED" and again["registry"]["digest"] == DIGEST


@pytest.mark.parametrize("ref", ["foss-mcp-serving", "foss-mcp-serving:latest", "Foss:Tag", "x y:1", ""])
def test_a_reference_without_an_explicit_non_latest_tag_is_refused(env, ref):
    with pytest.raises(R.ReleaseRefused):
        R.record(ref, report(env))
    assert not (env / "evidence").exists()


def test_a_report_for_a_different_image_id_proves_nothing_and_is_refused(env):
    with pytest.raises(R.ReleaseRefused, match="another image|not sha256"):
        R.record(REF, report(env, image_id=OTHER_ID))
    assert not (env / "evidence").exists()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"verdict": "FAIL"},
        {"passed": PILOTS - 1},
        {"total": PILOTS - 1, "passed": PILOTS - 1},
        {"total": PILOTS + 1, "passed": PILOTS + 1},
    ],
)
def test_a_report_that_is_not_a_full_pass_of_every_pilot_is_refused(env, kwargs):
    with pytest.raises(R.ReleaseRefused, match="need PASS|contradicts"):
        R.record(REF, report(env, **kwargs))
    assert not (env / "evidence").exists()


def test_a_report_missing_required_fields_is_refused(env):
    p = env / "bad.json"
    p.write_text(json.dumps({"verdict": "PASS"}), encoding="utf-8")
    with pytest.raises(R.ReleaseRefused, match="lacks"):
        R.record(REF, p)


def test_an_image_without_a_revision_label_is_refused(env, monkeypatch):
    monkeypatch.setattr(
        R,
        "inspect_image",
        lambda ref: {"image_id": IMAGE_ID, "size_bytes": 1, "created": "", "source_commit": ""},
    )
    with pytest.raises(R.ReleaseRefused, match="revision"):
        R.record(REF, report(env))


def test_a_source_commit_outside_this_history_is_refused(env, monkeypatch):
    monkeypatch.setattr(
        R, "commit_problem", lambda sha: "source commit aaaaaaaaaaaa is not an ancestor of HEAD"
    )
    with pytest.raises(R.ReleaseRefused, match="ancestor"):
        R.record(REF, report(env))


@pytest.mark.parametrize("digest", ["", "sha256:abc", "md5:" + "d" * 32, "d" * 64])
def test_a_push_with_no_valid_digest_is_not_recorded(env, digest):
    with pytest.raises(R.ReleaseRefused, match="digest"):
        R.record(REF, report(env), push_result(env, digest=digest))


def test_a_push_result_for_a_different_local_image_is_refused(env):
    with pytest.raises(R.ReleaseRefused, match="different local image"):
        R.record(REF, report(env), push_result(env, local_id=OTHER_ID))


def test_a_tag_is_immutable_a_different_image_id_under_the_same_tag_is_refused(env, monkeypatch):
    R.record(REF, report(env))
    monkeypatch.setattr(
        R,
        "inspect_image",
        lambda ref: {"image_id": OTHER_ID, "size_bytes": 1, "created": "", "source_commit": SHA},
    )
    with pytest.raises(R.ReleaseRefused, match="immutable|different image id"):
        R.record(REF, report(env, image_id=OTHER_ID))


def test_validate_catches_a_tampered_gate_report_copy(env):
    doc, _ = R.record(REF, report(env))
    (env / doc["gate_report"]["file"]).write_text("{}", encoding="utf-8")
    assert any("sha256" in p for p in R.problems())


def test_validate_catches_pushed_without_a_digest_and_a_renamed_record(env):
    _, path = R.record(REF, report(env), push_result(env))
    d = json.loads(path.read_text(encoding="utf-8"))
    d["registry"] = None
    path.write_text(json.dumps(d), encoding="utf-8")
    assert any("PUSHED" in p for p in R.problems())
    path.rename(path.with_name("something-else.json"))
    assert any("file name disagrees" in p for p in R.problems())


def test_validate_catches_an_unreadable_record(env):
    (env / "evidence" / "release").mkdir(parents=True)
    (env / "evidence" / "release" / "junk.json").write_text("{not json", encoding="utf-8")
    assert any("not valid JSON" in p for p in R.problems())


def test_g3_cannot_exit_without_a_pushed_gate_tested_image(env):
    assert R.gate_exit_problems("G3")
    R.record(REF, report(env))
    assert R.gate_exit_problems("G3"), "a LOCAL_ONLY image does not satisfy the hosting requirement"
    R.record(REF, report(env), push_result(env))
    assert R.gate_exit_problems("G3") == []
    assert R.gate_exit_problems("G2") == []


def test_a_pushed_image_whose_commit_left_the_history_no_longer_satisfies_g3(env, monkeypatch):
    R.record(REF, report(env), push_result(env))
    monkeypatch.setattr(
        R, "commit_problem", lambda sha: "source commit aaaaaaaaaaaa is not an ancestor of HEAD"
    )
    assert R.gate_exit_problems("G3")


def test_the_cli_prints_refused_and_fails_and_prints_recorded_on_success(env, capsys):
    class Args:
        image = REF
        gate_report = report(env, verdict="FAIL")
        push_result = None

    assert C.cmd_release_record(Args()) == G.EXIT_FAIL
    assert "REFUSED" in capsys.readouterr().out
    Args.gate_report = report(env)
    assert C.cmd_release_record(Args()) == G.EXIT_OK
    out = capsys.readouterr().out
    assert "RECORDED" in out and "not pushed yet" in out


def test_commit_problem_against_a_real_repository(tmp_path, monkeypatch):
    import subprocess

    for name in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{name}_NAME", "t")
        monkeypatch.setenv(f"GIT_{name}_EMAIL", "t@example.invalid")
    r = tmp_path / "repo"
    r.mkdir()
    for args in (["init", "-q", "-b", "main"], ["commit", "-q", "--allow-empty", "-m", "one"]):
        subprocess.run(["git", *args], cwd=r, check=True, capture_output=True)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=r, capture_output=True, text=True, check=True
    ).stdout.strip()
    subprocess.run(["git", "checkout", "-q", "-b", "side"], cwd=r, check=True)
    subprocess.run(
        ["git", "commit", "-q", "--allow-empty", "-m", "two"], cwd=r, check=True, capture_output=True
    )
    side = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=r, capture_output=True, text=True, check=True
    ).stdout.strip()
    subprocess.run(["git", "checkout", "-q", "main"], cwd=r, check=True, capture_output=True)
    monkeypatch.setattr(G, "REPO", r)
    assert R.commit_problem(sha) is None
    assert "ancestor" in (R.commit_problem(side) or "")
    assert "not a commit" in (R.commit_problem("f" * 40) or "")


# ---- red-team hardening (2026-10-11) -------------------------------------------------------------------------


def _rewrite_report(path: Path, **changes) -> None:
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc.update(changes)
    path.write_text(json.dumps(doc), encoding="utf-8")


@pytest.mark.parametrize("field", ["pilots_total", "pilots_passed"])
@pytest.mark.parametrize("value", [True, "3", 3.0, None])
def test_counts_must_be_real_integers_not_booleans_or_strings(env, field, value):
    p = report(env)
    _rewrite_report(p, **{field: value})
    with pytest.raises(R.ReleaseRefused, match="integer"):
        R.record(REF, p)


def test_a_report_must_cover_exactly_the_charts_pilots(env):
    p = report(env)
    doc = json.loads(p.read_text(encoding="utf-8"))
    doc["pilots"][2]["key"] = "not-a-chart-pilot"
    p.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(R.ReleaseRefused, match="chart"):
        R.record(REF, p)


def test_a_report_that_counts_more_passes_than_it_lists_is_refused(env):
    p = report(env, passed=PILOTS - 1)
    _rewrite_report(p, pilots_passed=PILOTS)
    with pytest.raises(R.ReleaseRefused, match="passing pilot"):
        R.record(REF, p)


def test_a_pass_report_with_no_pilot_list_is_refused(env):
    p = report(env)
    doc = json.loads(p.read_text(encoding="utf-8"))
    del doc["pilots"]
    p.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(R.ReleaseRefused, match="list its pilots"):
        R.record(REF, p)


def test_a_push_result_without_a_local_image_id_is_not_recorded(env):
    p = push_result(env)
    doc = json.loads(p.read_text(encoding="utf-8"))
    del doc["local_image_id"]
    p.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(R.ReleaseRefused, match="local_image_id"):
        R.record(REF, report(env), p)


@pytest.mark.parametrize("ref", ["", "no-slash-or-tag", "reg/Upper:y", "reg/x y:1", None])
def test_a_push_result_with_an_unusable_pushed_reference_is_not_recorded(env, ref):
    p = push_result(env)
    doc = json.loads(p.read_text(encoding="utf-8"))
    doc["pushed_reference"] = ref
    p.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(R.ReleaseRefused, match="pushed_reference"):
        R.record(REF, report(env), p)


def test_a_registry_with_a_port_is_a_usable_pushed_reference(env):
    p = push_result(env)
    doc = json.loads(p.read_text(encoding="utf-8"))
    doc["pushed_reference"] = "registry.example.internal:5000/team/foss-mcp-serving:demo-aaaaaaa"
    p.write_text(json.dumps(doc), encoding="utf-8")
    assert R.record(REF, report(env), p)[0]["status"] == "PUSHED"


def test_two_references_that_used_to_flatten_to_one_file_name_no_longer_collide():
    assert R.stem_for("a/b:t") != R.stem_for("a_b:t")
    assert R.stem_for("a:b__c") != R.stem_for("a:b/c")


def test_validate_rederives_the_copied_report_so_a_doctored_copy_with_a_fixed_hash_is_still_caught(env):
    doc, path = R.record(REF, report(env), push_result(env))
    copy = env / doc["gate_report"]["file"]
    forged = json.loads(copy.read_text(encoding="utf-8"))
    forged["verdict"] = "FAIL"
    copy.write_text(json.dumps(forged), encoding="utf-8")
    rec = json.loads(path.read_text(encoding="utf-8"))
    rec["gate_report"]["sha256"] = hashlib.sha256(copy.read_bytes()).hexdigest()
    path.write_text(json.dumps(rec), encoding="utf-8")
    assert any("disagrees with the report itself" in p for p in R.problems())
    assert R.gate_exit_problems("G3"), "a record that fails validation never satisfies the gate"


def test_validate_catches_a_report_copy_for_another_image(env):
    doc, path = R.record(REF, report(env))
    copy = env / doc["gate_report"]["file"]
    _rewrite_report(copy, image_id=OTHER_ID)
    rec = json.loads(path.read_text(encoding="utf-8"))
    rec["gate_report"]["sha256"] = hashlib.sha256(copy.read_bytes()).hexdigest()
    rec["gate_report"]["image_id"] = OTHER_ID
    path.write_text(json.dumps(rec), encoding="utf-8")
    assert any("different image id" in p for p in R.problems())


def test_validate_catches_a_gate_report_path_that_points_elsewhere(env):
    _, path = R.record(REF, report(env))
    other = env / "somewhere.json"
    other.write_text(report(env).read_text(encoding="utf-8"), encoding="utf-8")
    rec = json.loads(path.read_text(encoding="utf-8"))
    rec["gate_report"]["file"] = "somewhere.json"
    rec["gate_report"]["sha256"] = hashlib.sha256(other.read_bytes()).hexdigest()
    path.write_text(json.dumps(rec), encoding="utf-8")
    assert any("not the one derived" in p for p in R.problems())


def test_a_schema_invalid_record_is_reported_and_never_crashes_state_or_the_gate(env):
    _, path = R.record(REF, report(env), push_result(env))
    rec = json.loads(path.read_text(encoding="utf-8"))
    del rec["image"]["size_bytes"]
    path.write_text(json.dumps(rec), encoding="utf-8")
    assert R.state_rows() == []
    assert R.problems()
    assert R.gate_exit_problems("G3")


def test_recording_over_an_unreadable_existing_record_is_refused_not_a_traceback(env):
    _, path = R.record(REF, report(env))
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(R.ReleaseRefused, match="unreadable"):
        R.record(REF, report(env))
