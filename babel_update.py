# babel_update.py
import os
import sys
import subprocess

LANGUAGES = sys.argv[1:] if len(sys.argv) > 1 else ["bg", "en", "fr"]

# Ensure messages.pot is regenerated
print("\n[1] Извличане на съобщенията в messages.pot...")
extract_cmd = [
    sys.executable, "-m", "babel.messages.frontend", "extract",
    "-F", "babel.cfg", "-o", "messages.pot", "."
]
subprocess.run(extract_cmd, check=True)

# Инициализиране или актуализиране на преводи
for lang in LANGUAGES:
    po_file = os.path.join("translations", lang, "LC_MESSAGES", "messages.po")
    if not os.path.exists(po_file):
        print(f"[2] Създаване на нов превод за: {lang}")
        subprocess.run(["py", "-m", "babel.messages.frontend", "init",
                        "-i", "messages.pot", "-d", "translations", "-l", lang], check=True)
    else:
        print(f"[2] Актуализиране на съществуващ превод за: {lang}")
        subprocess.run(["py", "-m", "babel.messages.frontend", "update",
                        "-i", "messages.pot", "-d", "translations"], check=True)

# Компилация на преводите
print("[3] Компилиране на .mo файловете...")
subprocess.run(["py", "-m", "babel.messages.frontend", "compile",
                "-d", "translations"], check=True)

print("\n✅ Готово! Преводите са извлечени, обновени и компилирани.")
