"""Manual CI approval validates immutable Git heads and required protection."""

from governance_fixtures import governance_git_fixture


def test_manual_ci_review_is_bound_to_git_and_protection(tmp_path):
    with governance_git_fixture(tmp_path) as run:
        result = run.audit(run.production)
        assert result["review_required"] is False, result
        assert run.finish(run.production, "skipped", "false") == "success"
        result = run.audit(run.sensitive)
        assert result["review_required"] is True, result
        assert run.finish(run.sensitive, "success", "true") == "success"
        for approval in ("failure", "cancelled", "skipped"):
            assert run.finish(run.sensitive, approval, "true") == "failure"
        assert run.finish(run.sensitive, "success", "") == "failure"
        assert run.finish(run.sensitive, "success", "true", "failure") == "failure"
        run.state["head"] = run.production
        assert run.finish(run.sensitive, "success", "true") == "failure"
        run.state["head"] = run.sensitive
        run.state["base"] = run.production
        assert run.finish(run.sensitive, "success", "true") == "failure"
        run.state["base"] = run.base
        run.state["environment"] = False
        assert run.finish(run.sensitive, "success", "true") == "failure"
        run.state["environment"] = True
        run.state["protected"] = False
        assert run.finish(run.sensitive, "success", "true") == "failure"
        run.state["protected"] = True
        run.state["contexts"] = ["validation"]
        assert run.finish(run.sensitive, "success", "true") == "failure"
        run.state["contexts"] = ["validation", "validation-governance"]
        run.state["reviewer"] = "SomeoneElse"
        assert run.finish(run.sensitive, "success", "true") == "failure"
        run.state["reviewer"] = "DodgyBadger"
        run.state["prevent_self_review"] = True
        assert run.finish(run.sensitive, "success", "true") == "failure"
        run.state["prevent_self_review"] = False
        run.state["strict"] = False
        assert run.finish(run.sensitive, "success", "true") == "failure"
        run.state["strict"] = True
        run.state["admin"] = False
        assert run.finish(run.sensitive, "success", "true") == "failure"
        run.state["admin"] = True
        run.state["http_failure"] = True
        assert run.finish(run.sensitive, "success", "true") == "failure"
        assert not run.marker.exists(), "CI governance must not import scenario sources"
