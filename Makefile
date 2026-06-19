run:
	uvicorn app.main:app --host 0.0.0.0 --port 8200 --reload

test:
	pytest tests/ -v

lint:
	ruff check app/

format:
	ruff format app/

build:
	docker build -t log-analyzer .
