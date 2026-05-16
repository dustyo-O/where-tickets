# Overmind process map for local development.
# Launched by `just dev` from the repo root.
db:  docker compose up postgres
api: cd backend && uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
metro: cd mobile && npx react-native start
