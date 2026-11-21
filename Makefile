.PHONY: up down logs test build clean

up: .env
	docker compose up -d --build
	@echo "backend:  http://localhost:8000/health"
	@echo "frontend: http://localhost:3000"

down:
	docker compose down

logs:
	docker compose logs -f

build: .env
	docker compose build

test:
	cd backend && pip install -q -r requirements-dev.txt && python -m pytest -q

# wipes the SQLite volume and the cache
clean:
	docker compose down -v

.env:
	@echo ".env not found, copying .env.example (add your real keys to .env)"
	cp .env.example .env
