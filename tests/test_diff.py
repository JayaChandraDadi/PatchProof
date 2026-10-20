from patchproof.diff import parse_diff, render_diff

SAMPLE = """\
diff --git a/app/core.py b/app/core.py
index 1111111..2222222 100644
--- a/app/core.py
+++ b/app/core.py
@@ -1,5 +1,6 @@
 import os
-def f(x):
-    return x + 1
+def f(x, y=0):
+    # changed
+    return x + y + 1
 
 print(f(1))
@@ -20,3 +21,3 @@ class A:
     def g(self):
--- a comment line that starts with dashes
+    pass
     return 1
diff --git a/new.txt b/new.txt
new file mode 100644
--- /dev/null
+++ b/new.txt
@@ -0,0 +1,2 @@
+hello
+world
\\ No newline at end of file
diff --git a/old.py b/old.py
deleted file mode 100644
--- a/old.py
+++ /dev/null
@@ -1 +0,0 @@
-x = 1
diff --git a/a.py b/b.py
similarity index 90%
rename from a.py
rename to b.py
--- a/a.py
+++ b/b.py
@@ -1 +1 @@
-x = 1
+x = 2
diff --git a/img.png b/img.png
Binary files a/img.png and b/img.png differ
"""


def test_files_and_status():
    files = parse_diff(SAMPLE)
    assert [(f.path, f.status) for f in files] == [
        ("app/core.py", "modified"), ("new.txt", "added"), ("old.py", "deleted"),
        ("b.py", "renamed"), ("img.png", "modified"),
    ]
    assert files[-1].is_binary


def test_line_numbers():
    core = parse_diff(SAMPLE)[0]
    assert core.added_lines == {2, 3, 4, 22}
    assert 1 in core.commentable_lines and 5 in core.commentable_lines
    assert len(core.hunks) == 2
    # A removed line whose text starts with "--" must not be taken for a file header.
    assert core.hunks[1].lines[1].kind == "-"
    assert core.hunks[1].header == " class A:"


def test_new_file_lines():
    new = parse_diff(SAMPLE)[1]
    assert new.added_lines == {1, 2}
    assert new.old_path is None


def test_plain_unified_diff_without_git_header():
    text = "--- x.py\n+++ x.py\n@@ -1,2 +1,2 @@\n a = 1\n-b = 2\n+b = 3\n--- y.py\n+++ y.py\n@@ -3 +3 @@\n-c\n+d\n"
    files = parse_diff(text)
    assert [f.path for f in files] == ["x.py", "y.py"]
    assert files[0].added_lines == {2}
    assert files[1].added_lines == {3}


def test_render_shows_right_side_numbers():
    out = render_diff(parse_diff(SAMPLE)[:1])
    assert "    4 +    return x + y + 1" in out
    assert "      -def f(x):" in out
