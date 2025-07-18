import os
import shutil

# Пътища
frontend = os.path.join("frontend")
templates = os.path.join("backend", "templates")

# Списък с файлове за преместване
files = ["help_request.html", "volunteer_dashboard.html"]

# Създаване на templates папка ако не съществува
os.makedirs(templates, exist_ok=True)

# Преместване
for file in files:
    src = os.path.join(frontend, file)
    dst = os.path.join(templates, file)
    if os.path.exists(src):
        shutil.move(src, dst)
        print(f"✅ Преместен: {file}")
    else:
        print(f"⚠️ Не е намерен: {file}")
