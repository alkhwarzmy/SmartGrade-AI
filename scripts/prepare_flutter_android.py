"""Create Android scaffolding using the pinned Flutter SDK, without overwriting app sources."""
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "flutter_app"


def prepare():
    target = APP / "android"
    if target.exists():
        print("Android scaffold already present; preserving existing native edits.")
        return
    with tempfile.TemporaryDirectory(prefix="smartgrade-flutter-") as temporary:
        generated = Path(temporary) / "smartgrade"
        subprocess.run([
            "flutter", "create", "--project-name", "smartgrade", "--org", "com.alkhwarzmy",
            "--platforms", "android", "--android-language", "kotlin", "--no-pub", str(generated),
        ], check=True)
        shutil.copytree(generated / "android", target,
                        ignore=shutil.ignore_patterns("local.properties", ".gradle", "*.jks", "*.keystore"))
    manifest = target / "app/src/main/AndroidManifest.xml"
    text = manifest.read_text()
    text = text.replace("<application", '<uses-permission android:name="android.permission.INTERNET"/>\\n    <application', 1)
    text = text.replace('android:label="smartgrade"', 'android:label="SmartGrade"')
    text = text.replace("<application", '<application android:allowBackup="false" android:usesCleartextTraffic="false"', 1)
    manifest.write_text(text.replace("\\n", "\n"))
    for build in ["debug", "profile"]:
        path = target / f"app/src/{build}/AndroidManifest.xml"
        path.write_text('''<manifest xmlns:android="http://schemas.android.com/apk/res/android"
xmlns:tools="http://schemas.android.com/tools">
<uses-permission android:name="android.permission.INTERNET"/>
<application android:usesCleartextTraffic="true" tools:replace="android:usesCleartextTraffic"/>
</manifest>
''')
    gradle = target / "app/build.gradle.kts"
    text = gradle.read_text().replace("minSdk = flutter.minSdkVersion", "minSdk = 24")
    text = text.replace('signingConfig = signingConfigs.getByName("debug")',
                        '// Production release requires an operator-provided signing configuration.')
    gradle.write_text(text)
    icon = target / "app/src/main/res/drawable/smartgrade_icon.xml"
    icon.parent.mkdir(parents=True, exist_ok=True)
    icon.write_text('''<vector xmlns:android="http://schemas.android.com/apk/res/android"
android:width="48dp" android:height="48dp" android:viewportWidth="48" android:viewportHeight="48">
<path android:fillColor="#14665F" android:pathData="M0,0h48v48h-48z"/>
<path android:strokeColor="#FFFFFF" android:strokeWidth="4" android:fillColor="#00000000"
android:pathData="M11,24l9,9l18,-19"/>
</vector>
''')
    manifest.write_text(manifest.read_text().replace("@mipmap/ic_launcher", "@drawable/smartgrade_icon"))
    print("Android scaffold ready; debug build is for internal device testing.")


if __name__ == "__main__":
    prepare()
