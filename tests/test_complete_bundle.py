"""The Android source archive must include source and omit local keys/build caches."""
from scripts.build_colab_notebook import source_files
from scripts.build_complete_notebook import build_complete_notebook


def test_android_source_allowlist(tmp_path):
    names = [
        "flutter_app/lib/main.dart", "flutter_app/android/app/build.gradle.kts",
        "flutter_app/android/app/src/main/AndroidManifest.xml",
        "flutter_app/android/local.properties", "flutter_app/android/key.properties",
        "flutter_app/android/app/release.jks", "flutter_app/android/app/release.key",
        "flutter_app/android/app/.env.production", "flutter_app/android/.gradle/cache.txt",
        "flutter_app/android/.kotlin/cache.txt", "flutter_app/build/app.apk",
    ]
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic")
    included = {p.relative_to(tmp_path).as_posix() for p in source_files(tmp_path)}
    assert set(names[:3]) <= included
    assert not set(names[3:]) & included


def test_complete_notebook_is_commercial_with_window_and_android():
    notebook = build_complete_notebook()
    assert notebook["metadata"]["smartgrade_mode"] == "commercial"
    assert "flutter-android-source" in notebook["metadata"]["smartgrade_features"]
    sources = "\n".join(c["source"] for c in notebook["cells"] if c["cell_type"] == "code")
    assert "output.serve_kernel_port_as_window(PORT)" in sources
    assert "serve_kernel_port_as_iframe" not in sources
    assert "source_bundle(PROJECT_DIR)" in sources
    for cell in notebook["cells"]:
        if cell["cell_type"] == "code":
            compile(cell["source"], cell["id"], "exec")
