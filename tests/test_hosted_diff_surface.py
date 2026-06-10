"""Tests for diff → changed-surface extraction (cortex#363)."""

from __future__ import annotations

import pytest

from cortex.hosted.diff_surface import (
    DiffSurfaceValidationError,
    extract_changed_surface,
)
from cortex.hosted.scopes import ScopeType

PY_DIFF = """\
diff --git a/src/payments/webhook_client.py b/src/payments/webhook_client.py
--- a/src/payments/webhook_client.py
+++ b/src/payments/webhook_client.py
@@ -1,6 +1,7 @@
+import tenacity
 import requests
@@ -10,7 +11,7 @@ def send_with_retry(attempt):
-    delay = backoff_with_jitter(attempt)
+    delay = 5.0  # see acme/payments#812 and #44
@@ -30,4 +31,8 @@ class WebhookClient:
+    def close(self):
+        self._session.close()
"""

CONFIG_DIFF = """\
diff --git a/config/settings.toml b/config/settings.toml
--- a/config/settings.toml
+++ b/config/settings.toml
@@ -1,3 +1,3 @@
-retry_interval = 30
+retry_interval = 5
diff --git a/deploy/app.yaml b/deploy/app.yaml
--- a/deploy/app.yaml
+++ b/deploy/app.yaml
@@ -4,2 +4,2 @@
-  replicas: 2
+  replicas: 4
diff --git a/.env.example b/.env.example
--- a/.env.example
+++ b/.env.example
@@ -1,1 +1,2 @@
+WEBHOOK_TIMEOUT_SECONDS=30
"""

RENAME_DIFF = """\
diff --git a/src/old_name.py b/src/new_name.py
similarity index 96%
rename from src/old_name.py
rename to src/new_name.py
--- a/src/old_name.py
+++ b/src/new_name.py
@@ -1,2 +1,2 @@
-def legacy():
+def modern():
"""

NEW_FILE_DIFF = """\
diff --git a/docs/notes.md b/docs/notes.md
--- /dev/null
+++ b/docs/notes.md
@@ -0,0 +1,2 @@
+Notes only.
"""


def test_python_diff_extracts_paths_symbols_packages_and_issue_refs() -> None:
    surface = extract_changed_surface(PY_DIFF)
    assert surface.paths == ("src/payments/webhook_client.py",)
    assert "send_with_retry" in surface.symbols  # hunk-header context symbol
    assert "close" in surface.symbols  # added method
    assert surface.packages == ("tenacity",)  # added import only; existing untouched
    assert surface.issue_refs == ("acme/payments#812", "#44")


def test_config_diff_extracts_keys_per_format() -> None:
    surface = extract_changed_surface(CONFIG_DIFF)
    assert set(surface.config_keys) == {"retry_interval", "replicas", "WEBHOOK_TIMEOUT_SECONDS"}
    assert set(surface.paths) == {"config/settings.toml", "deploy/app.yaml", ".env.example"}


def test_rename_keeps_both_paths() -> None:
    surface = extract_changed_surface(RENAME_DIFF)
    assert "src/old_name.py" in surface.paths
    assert "src/new_name.py" in surface.paths
    assert {"legacy", "modern"} <= set(surface.symbols)


def test_new_file_does_not_record_dev_null() -> None:
    surface = extract_changed_surface(NEW_FILE_DIFF)
    assert surface.paths == ("docs/notes.md",)
    assert "/dev/null" not in surface.paths


def test_surface_feeds_query_scopes_with_normalized_values() -> None:
    surface = extract_changed_surface(PY_DIFF)
    scopes = surface.query_scopes()
    by_type = {(scope.scope_type, scope.normalized_value) for scope in scopes}
    assert (ScopeType.PATH, "src/payments/webhook_client.py") in by_type
    assert (ScopeType.ISSUE_REF, "#812") in by_type
    assert (ScopeType.ISSUE_REF, "#44") in by_type
    assert (ScopeType.SYMBOL, "webhookclient") in by_type or (
        ScopeType.SYMBOL,
        "WebhookClient".lower(),
    ) in by_type or (ScopeType.SYMBOL, "WebhookClient") in by_type


def test_extraction_is_deterministic() -> None:
    assert extract_changed_surface(PY_DIFF) == extract_changed_surface(PY_DIFF)


def test_empty_patch_fails_visibly() -> None:
    with pytest.raises(DiffSurfaceValidationError, match="empty"):
        extract_changed_surface("   \n")


def test_non_diff_text_fails_visibly() -> None:
    with pytest.raises(DiffSurfaceValidationError, match="not a unified diff"):
        extract_changed_surface("just some prose\nwith lines\n")


def test_changed_line_before_header_fails_visibly() -> None:
    with pytest.raises(DiffSurfaceValidationError, match="not a unified diff"):
        extract_changed_surface("+orphan added line\n")


def test_issue_refs_only_from_added_lines() -> None:
    diff = """\
diff --git a/notes.py b/notes.py
--- a/notes.py
+++ b/notes.py
@@ -1,2 +1,2 @@
-# removed mention of #99
+# added mention of #100
"""
    surface = extract_changed_surface(diff)
    assert surface.issue_refs == ("#100",)
