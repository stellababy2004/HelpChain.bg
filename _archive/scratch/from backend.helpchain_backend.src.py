from backend.helpchain_backend.src.app import create_app

app = create_app()
routes = [r for r in app.url_map.iter_rules() if r.rule == "/"]

assert len(routes) == 1, f"❌ Multiple index routes found: {routes}"
print("✅ Single index route confirmed")
