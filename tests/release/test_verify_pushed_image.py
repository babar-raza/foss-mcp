"""G3/TC-569: scripts/release/verify_pushed_image.py, offline.

Docker, the registry and both suites are replaced by hand-written fakes. Nothing here touches docker, a registry
or the network. Every expected value (digests, image ids, commits, references) is written out by hand below.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RELEASE_DIR = REPO_ROOT / "scripts" / "release"
SOURCE = RELEASE_DIR / "verify_pushed_image.py"

sys.path.insert(0, str(RELEASE_DIR))

import verify_pushed_image as vpi  # noqa: E402

DIGEST = "sha256:" + "0123456789abcdef" * 4
OTHER_DIGEST = "sha256:" + "fedcba9876543210" * 4
IMAGE_ID = "sha256:" + "ab12" * 16
OTHER_IMAGE_ID = "sha256:" + "cd34" * 16
COMMIT = "a" * 40
OTHER_COMMIT = "b" * 40
REPOSITORY = "registry.example.internal:5000/foss-mcp/foss-mcp-serving"
PUSHED_REFERENCE = f"{REPOSITORY}:rev-abc1234"
EXPECTED_REFERENCE = f"{REPOSITORY}@{DIGEST}"
REVISION_LABEL = "org.opencontainers.image.revision"

PUSH_RESULT = {
    "image": "foss-mcp-serving:rev-abc1234",
    "pushed_reference": PUSHED_REFERENCE,
    "digest": DIGEST,
    "local_image_id": IMAGE_ID,
    "timestamp": "2026-10-19T10:00:00Z",
}
RECORD = {
    "schema_version": 1,
    "status": "LOCAL_ONLY",
    "image": {
        "reference": "foss-mcp-serving:rev-abc1234",
        "image_id": IMAGE_ID,
        "size_bytes": 1000,
        "source_commit": COMMIT,
    },
    "registry": None,
}


class FakeDeps:
    """Records every reference handed to each callable; each answer can be changed per test."""

    def __init__(
        self,
        *,
        pull=(0, "Status: Downloaded newer image"),
        inspected="default",
        gate_rc=0,
        adversarial_rc=0,
    ):
        self.pull_result = pull
        self.inspected = (
            {
                "image_id": IMAGE_ID,
                "repo_digests": [EXPECTED_REFERENCE],
                "labels": {REVISION_LABEL: COMMIT},
            }
            if inspected == "default"
            else inspected
        )
        self.gate_rc = gate_rc
        self.adversarial_rc = adversarial_rc
        self.calls = []

    def docker_pull(self, ref):
        self.calls.append(("pull", ref))
        return self.pull_result

    def docker_inspect(self, ref):
        self.calls.append(("inspect", ref))
        return self.inspected

    def run_gate(self, ref):
        self.calls.append(("gate", ref))
        return self.gate_rc

    def run_adversarial(self, ref):
        self.calls.append(("adversarial", ref))
        return self.adversarial_rc

    @property
    def deps(self):
        return vpi.Deps(self.docker_pull, self.docker_inspect, self.run_gate, self.run_adversarial)

    def refs(self, kind):
        return [ref for name, ref in self.calls if name == kind]


def _inspected(**overrides):
    base = {"image_id": IMAGE_ID, "repo_digests": [EXPECTED_REFERENCE], "labels": {REVISION_LABEL: COMMIT}}
    base.update(overrides)
    return base


# --- the passing flow ------------------------------------------------------------------------------------------


def test_matching_image_passes_and_gates_the_digest_reference():
    fake = FakeDeps()
    report = vpi.verify(PUSH_RESULT, RECORD, fake.deps)
    assert report.verified is True
    assert report.failure is None
    assert [step.status for step in report.steps] == ["pass"] * 7
    assert report.reference == EXPECTED_REFERENCE
    assert fake.refs("gate") == [EXPECTED_REFERENCE]
    assert fake.refs("adversarial") == [EXPECTED_REFERENCE]


def test_every_reference_handed_to_docker_and_the_suites_is_the_digest_form():
    fake = FakeDeps()
    vpi.verify(PUSH_RESULT, RECORD, fake.deps)
    assert len(fake.calls) == 4
    for _name, ref in fake.calls:
        assert ref == EXPECTED_REFERENCE
        assert ref.endswith("@" + DIGEST)
        assert "rev-abc1234" not in ref


def test_digest_reference_strips_a_tag_a_port_and_an_existing_digest():
    assert vpi.digest_reference(PUSHED_REFERENCE, DIGEST) == EXPECTED_REFERENCE
    assert vpi.digest_reference("host:5000/name:tag", DIGEST) == f"host:5000/name@{DIGEST}"
    assert vpi.digest_reference("host/a/b", DIGEST) == f"host/a/b@{DIGEST}"
    assert vpi.digest_reference(f"host/a:t@{OTHER_DIGEST}", DIGEST) == f"host/a@{DIGEST}"


def test_require_digest_reference_line_is_committed_exactly_once():
    lines = SOURCE.read_text(encoding="utf-8").splitlines()
    assert lines.count("REQUIRE_DIGEST_REFERENCE = True") == 1
    assert vpi.REQUIRE_DIGEST_REFERENCE is True


# --- each failure has its own code, message and step ------------------------------------------------------------


def _failed(fake, push=PUSH_RESULT, record=RECORD):
    report = vpi.verify(push, record, fake.deps)
    assert report.verified is False
    assert report.as_dict()["verified"] is False
    assert report.as_dict()["verdict"] == "FAIL"
    return report


def test_failed_pull_stops_before_anything_else():
    fake = FakeDeps(pull=(1, "Error response from daemon: manifest unknown"))
    report = _failed(fake)
    assert report.failure == "PULL_FAILED"
    assert report.step("pull").status == "fail"
    assert "manifest unknown" in report.step("pull").message
    assert [name for name, _ in fake.calls] == ["pull"]
    assert all(step.status == "skipped" for step in report.steps[1:])


def test_digest_mismatch_fails_and_names_both_digests():
    fake = FakeDeps(inspected=_inspected(repo_digests=[f"{REPOSITORY}@{OTHER_DIGEST}"]))
    report = _failed(fake)
    assert report.failure == "DIGEST_MISMATCH"
    assert DIGEST in report.step("manifest").message
    assert OTHER_DIGEST in report.step("manifest").message
    assert fake.refs("gate") == []


def test_image_id_mismatch_fails_before_the_suites():
    fake = FakeDeps(inspected=_inspected(image_id=OTHER_IMAGE_ID))
    report = _failed(fake)
    assert report.failure == "IMAGE_ID_MISMATCH"
    assert IMAGE_ID in report.step("image_id").message
    assert OTHER_IMAGE_ID in report.step("image_id").message
    assert fake.refs("gate") == [] and fake.refs("adversarial") == []


def test_revision_mismatch_fails():
    fake = FakeDeps(inspected=_inspected(labels={REVISION_LABEL: OTHER_COMMIT}))
    report = _failed(fake)
    assert report.failure == "REVISION_MISMATCH"
    assert COMMIT in report.step("revision").message and OTHER_COMMIT in report.step("revision").message
    assert fake.refs("gate") == []


@pytest.mark.parametrize("labels", [{}, {"other": "x"}, {REVISION_LABEL: ""}, None])
def test_missing_revision_label_fails(labels):
    fake = FakeDeps(inspected=_inspected(labels=labels))
    report = _failed(fake)
    assert report.failure == "LABEL_MISSING"
    assert REVISION_LABEL in report.step("revision").message
    assert fake.refs("gate") == []


def test_inspect_failure_fails():
    fake = FakeDeps(inspected=None)
    report = _failed(fake)
    assert report.failure == "INSPECT_FAILED"
    assert fake.refs("gate") == []


def test_failing_gate_fails_and_does_not_run_the_adversarial_suite():
    fake = FakeDeps(gate_rc=1)
    report = _failed(fake)
    assert report.failure == "GATE_FAILED"
    assert report.step("gate").status == "fail"
    assert report.step("adversarial").status == "skipped"
    assert fake.refs("adversarial") == []


def test_failing_adversarial_suite_fails_after_a_passing_gate():
    fake = FakeDeps(adversarial_rc=1)
    report = _failed(fake)
    assert report.failure == "ADVERSARIAL_FAILED"
    assert report.step("gate").status == "pass"
    assert report.step("adversarial").status == "fail"


def test_the_eight_failure_messages_are_distinct():
    cases = [
        FakeDeps(pull=(1, "x")),
        FakeDeps(inspected=None),
        FakeDeps(inspected=_inspected(repo_digests=[f"{REPOSITORY}@{OTHER_DIGEST}"])),
        FakeDeps(inspected=_inspected(image_id=OTHER_IMAGE_ID)),
        FakeDeps(inspected=_inspected(labels={})),
        FakeDeps(inspected=_inspected(labels={REVISION_LABEL: OTHER_COMMIT})),
        FakeDeps(gate_rc=1),
        FakeDeps(adversarial_rc=3),
    ]
    messages = []
    for fake in cases:
        report = _failed(fake)
        failing = next(step for step in report.steps if step.status == "fail")
        messages.append(failing.message.split(":")[0])
    assert len(set(messages)) == 8


# --- requests that are refused before anything runs --------------------------------------------------------------


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p, r: p.pop("digest"),
        lambda p, r: p.update(digest="rev-abc1234"),
        lambda p, r: p.update(digest="sha256:" + "A" * 64),
        lambda p, r: p.update(digest=""),
        lambda p, r: p.pop("pushed_reference"),
        lambda p, r: p.update(local_image_id=OTHER_IMAGE_ID),
        lambda p, r: r["image"].update(source_commit="abc1234"),
        lambda p, r: r["image"].pop("image_id"),
        lambda p, r: r.update(registry={"reference": "x", "digest": OTHER_DIGEST, "pushed_at": "now"}),
    ],
)
def test_unusable_inputs_are_refused_and_nothing_is_pulled(mutate):
    push, record = json.loads(json.dumps(PUSH_RESULT)), json.loads(json.dumps(RECORD))
    mutate(push, record)
    fake = FakeDeps()
    with pytest.raises(vpi.Refused):
        vpi.verify(push, record, fake.deps)
    assert fake.calls == []


def test_a_record_with_a_matching_registry_digest_is_accepted():
    record = json.loads(json.dumps(RECORD))
    record["status"] = "PUSHED"
    record["registry"] = {
        "reference": PUSHED_REFERENCE,
        "digest": DIGEST,
        "pushed_at": "2026-10-19T10:00:00Z",
    }
    assert vpi.verify(PUSH_RESULT, record, FakeDeps().deps).verified is True


# --- command line and report ------------------------------------------------------------------------------------


def _write_inputs(tmp_path):
    push = tmp_path / "push.json"
    record = tmp_path / "record.json"
    push.write_text(json.dumps(PUSH_RESULT), encoding="utf-8")
    record.write_text(json.dumps(RECORD), encoding="utf-8")
    return ["--push-result", str(push), "--record", str(record), "--report-dir", str(tmp_path / "out")]


def test_main_exits_zero_and_writes_a_verified_report(tmp_path, capsys):
    fake = FakeDeps()
    assert vpi.main(_write_inputs(tmp_path), fake.deps) == 0
    report = json.loads((tmp_path / "out" / "verify_pushed_image.json").read_text(encoding="utf-8"))
    assert report["verified"] is True and report["verdict"] == "PASS"
    assert report["reference"] == EXPECTED_REFERENCE
    assert [step["name"] for step in report["steps"]] == list(vpi.STEP_NAMES)
    assert [step["status"] for step in report["steps"]] == ["pass"] * 7
    assert "VERIFIED" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("fake", "code"),
    [
        (FakeDeps(pull=(1, "denied")), "PULL_FAILED"),
        (FakeDeps(inspected=_inspected(repo_digests=[f"{REPOSITORY}@{OTHER_DIGEST}"])), "DIGEST_MISMATCH"),
        (FakeDeps(inspected=_inspected(image_id=OTHER_IMAGE_ID)), "IMAGE_ID_MISMATCH"),
        (FakeDeps(inspected=_inspected(labels={})), "LABEL_MISSING"),
        (FakeDeps(inspected=_inspected(labels={REVISION_LABEL: OTHER_COMMIT})), "REVISION_MISMATCH"),
        (FakeDeps(gate_rc=1), "GATE_FAILED"),
        (FakeDeps(adversarial_rc=1), "ADVERSARIAL_FAILED"),
    ],
)
def test_main_exits_one_with_a_distinct_message_and_no_verified_report(tmp_path, capsys, fake, code):
    assert vpi.main(_write_inputs(tmp_path), fake.deps) == 1
    report = json.loads((tmp_path / "out" / "verify_pushed_image.json").read_text(encoding="utf-8"))
    assert report["verified"] is False and report["verdict"] == "FAIL"
    assert report["failure"] == code
    err = capsys.readouterr()
    assert code in err.err and vpi.FAILURE_MESSAGES[code] in err.err
    assert "VERIFIED" not in err.out + err.err


def test_main_refuses_a_tag_only_push_result_with_exit_two(tmp_path, capsys):
    argv = _write_inputs(tmp_path)
    tag_only = dict(PUSH_RESULT)
    del tag_only["digest"]
    (tmp_path / "push.json").write_text(json.dumps(tag_only), encoding="utf-8")
    fake = FakeDeps()
    assert vpi.main(argv, fake.deps) == 2
    assert fake.calls == []
    assert not (tmp_path / "out").exists()
    assert "digest" in capsys.readouterr().err


def test_main_refuses_unreadable_files_with_exit_two(tmp_path):
    argv = ["--push-result", str(tmp_path / "missing.json"), "--record", str(tmp_path / "r.json")]
    fake = FakeDeps()
    assert vpi.main(argv, fake.deps) == 2
    assert fake.calls == []


def test_dry_run_prints_the_digest_pull_and_runs_nothing(tmp_path, capsys):
    fake = FakeDeps()
    assert vpi.main([*_write_inputs(tmp_path), "--dry-run"], fake.deps) == 0
    assert capsys.readouterr().out.strip() == f"docker pull {EXPECTED_REFERENCE}"
    assert fake.calls == []
    assert not (tmp_path / "out").exists()


# --- the real docker side, with a fake subprocess.run -------------------------------------------------------------


class FakeRun:
    def __init__(self, handler):
        self.handler = handler
        self.commands = []

    def __call__(self, cmd, **kwargs):
        self.commands.append(list(cmd))
        rc, out = self.handler(cmd)
        return subprocess.CompletedProcess(cmd, rc, out, "")


def test_real_pull_runs_docker_pull_on_the_digest_reference_only():
    run = FakeRun(lambda cmd: (0, "ok"))
    assert vpi.real_pull(run, EXPECTED_REFERENCE) == (0, "ok")
    assert run.commands == [["docker", "pull", EXPECTED_REFERENCE]]


def test_real_inspect_parses_id_repo_digests_and_labels():
    payload = [
        {
            "Id": IMAGE_ID,
            "RepoDigests": [EXPECTED_REFERENCE],
            "Config": {"Labels": {REVISION_LABEL: COMMIT}},
        }
    ]
    run = FakeRun(lambda cmd: (0, json.dumps(payload)))
    assert vpi.real_inspect(run, EXPECTED_REFERENCE) == {
        "image_id": IMAGE_ID,
        "repo_digests": [EXPECTED_REFERENCE],
        "labels": {REVISION_LABEL: COMMIT},
    }
    assert run.commands == [["docker", "image", "inspect", EXPECTED_REFERENCE]]


@pytest.mark.parametrize(
    "handler", [lambda c: (1, "no such image"), lambda c: (0, "not json"), lambda c: (0, "[]")]
)
def test_real_inspect_returns_none_when_docker_cannot_describe_the_image(handler):
    assert vpi.real_inspect(FakeRun(handler), EXPECTED_REFERENCE) is None


def test_real_suite_gates_a_local_alias_of_the_digest_image_and_removes_it(tmp_path):
    run = FakeRun(lambda cmd: (0, ""))
    seen = []

    def suite(argv):
        seen.append(argv)
        return 0

    assert vpi.real_suite(run, EXPECTED_REFERENCE, suite, tmp_path) == 0
    alias = vpi.local_alias(DIGEST)
    assert alias == "foss-mcp-verify-pushed:" + DIGEST[len("sha256:") :][:12]
    assert run.commands == [["docker", "tag", EXPECTED_REFERENCE, alias], ["docker", "rmi", alias]]
    assert seen == [["--image", alias, "--report-dir", str(tmp_path)]]


def test_real_suite_removes_the_alias_even_when_the_suite_raises(tmp_path):
    run = FakeRun(lambda cmd: (0, ""))

    def suite(argv):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        vpi.real_suite(run, EXPECTED_REFERENCE, suite, tmp_path)
    assert run.commands[-1][:2] == ["docker", "rmi"]


# --- no credential is read, printed or stored -------------------------------------------------------------------


def test_source_reads_no_credential_environment_or_docker_config():
    code = "\n".join(
        line for line in SOURCE.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("#")
    )
    for forbidden in (
        "os.environ",
        "getenv",
        "environ",
        '".docker"',
        "'.docker'",
        "/.docker",
        "config.json",
        "docker login",
        "password",
        "token",
    ):
        assert forbidden not in code.lower().replace('"""', ""), forbidden
    assert re.search(r"\bopen\(", code) is None


def test_report_holds_only_references_ids_and_step_results(tmp_path):
    planted = "hunter2-planted-credential-value-9f3b"
    fake = FakeDeps()
    assert vpi.main(_write_inputs(tmp_path), fake.deps) == 0
    text = (tmp_path / "out" / "verify_pushed_image.json").read_text(encoding="utf-8")
    assert planted not in text
    assert set(json.loads(text)) == {
        "schema_version",
        "generated_at",
        "image",
        "reference",
        "digest",
        "expected_image_id",
        "expected_revision",
        "verified",
        "verdict",
        "failure",
        "steps",
    }
